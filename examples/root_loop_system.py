"""
Root-Loop Actor System: 每个 Root Actor 一个专用 Event Loop（教学简化版）

⚠️ 这是简化的原型实现，仅用于演示多 loop 架构的核心思路。
生产环境请使用：
    from everything_is_an_actor.core.unified_system import ActorSystem, ActorSystemMode
    system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP)

与生产版 backends/multi_loop.py 的差异：
- 无 spawn 失败回滚（资源泄漏）
- 无有界邮箱 / QueueFull 处理
- tell 静默丢弃（无 dead-letter 计数）
- stop 不递归清理后代
- sync actor 校验不完整（仅查 __dict__，MRO 绕过）

设计原则：
1. 一个 Root Actor = 一个专用 Event Loop（运行在独立线程）
2. 所有 Children 继承 Root 的 Loop
3. 跨 Loop 通信使用 asyncio.run_coroutine_threadsafe
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

if TYPE_CHECKING:
    from everything_is_an_actor.core.actor import Actor

logger = logging.getLogger(__name__)

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")

_MISSING = object()  # sentinel for backward-compatible tell/ask overloads


@dataclass
class _Reply:
    """显式 ask 回复信封，避免用 Exception 实例混充 payload。"""
    ok: bool
    value: Any = None
    error: Optional[Exception] = None


@dataclass
class _Envelope:
    """消息信封"""
    payload: Any
    sender_path: Optional[str]
    # asyncio.Future 驻在 caller 的 loop 中；reply_loop=None 表示与 actor 同 loop
    reply_future: Optional[asyncio.Future] = None
    reply_loop: Optional[asyncio.AbstractEventLoop] = None


@dataclass
class LoopContext:
    """单个 Event Loop 的上下文。"""
    loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    root_name: str
    actor_count: int = 0
    
    def is_alive(self) -> bool:
        return self.thread.is_alive()


class _RootActorCell:
    """
    Root Actor 的运行时容器。
    
    管理：
    - Actor 实例生命周期
    - 消息队列（mailbox）
    - 处理循环
    """
    
    def __init__(
        self,
        actor_cls: type[Actor],
        name: str,
        kwargs: dict,
        ref: "RootActorRef",
    ):
        self.actor_cls = actor_cls
        self.name = name
        self.kwargs = kwargs
        self.ref = ref
        
        self.actor: Optional[Actor] = None
        self.mailbox: asyncio.Queue[_Envelope] = asyncio.Queue()
        self.stopped = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """启动 Actor"""
        self.actor = self.actor_cls(**self.kwargs)
        self._task = asyncio.create_task(self._run())
        
        try:
            await self.actor.on_started()
        except Exception as e:
            logger.error("Actor %s on_started failed: %s", self.name, e)
    
    async def _run(self) -> None:
        """消息处理循环"""
        while not self.stopped:
            try:
                # 阻塞等待消息（零轮询开销）
                env = await self.mailbox.get()
                
                if self.stopped:
                    break
                
                await self._process_message(env)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Actor %s error: %s", self.name, e)
        
        # 清理
        if self.actor:
            try:
                await self.actor.on_stopped()
            except Exception:
                pass
    
    def _resolve_future(
        self,
        fut: asyncio.Future,
        caller_loop: Optional[asyncio.AbstractEventLoop],
        *,
        result: Any = None,
        error: Optional[Exception] = None,
    ) -> None:
        """在正确的 loop 中完成 asyncio.Future，避免跨 loop 直接 set。"""
        def _set() -> None:
            if fut.done():
                return
            if error is not None:
                fut.set_exception(error)
            else:
                fut.set_result(result)

        if caller_loop is None:
            # 同 loop：已在 owning loop 线程中，直接 set
            _set()
        else:
            # 跨 loop：通过 call_soon_threadsafe 在 caller's loop 线程中 set
            caller_loop.call_soon_threadsafe(_set)

    async def _process_message(self, env: _Envelope) -> None:
        """处理单条消息"""
        if self.actor is None:
            return

        try:
            result = await self.actor.on_receive(env.payload)
            if env.reply_future is not None:
                self._resolve_future(env.reply_future, env.reply_loop,
                                     result=_Reply(ok=True, value=result))
        except Exception as e:
            if env.reply_future is not None:
                self._resolve_future(env.reply_future, env.reply_loop,
                                     result=_Reply(ok=False, error=e))
            logger.error("Actor %s failed to process message: %s", self.name, e)

    def enqueue(self, envelope: _Envelope) -> None:
        """入队消息（线程安全）。check-and-enqueue 在 owning loop 中原子执行，消除 check-then-act 竞态。"""
        def _enqueue_or_reject() -> None:
            if self.stopped:
                if envelope.reply_future is not None:
                    self._resolve_future(
                        envelope.reply_future, envelope.reply_loop,
                        result=_Reply(ok=False, error=RuntimeError(f"Actor {self.name} stopped")),
                    )
                else:
                    logger.warning(
                        "Actor %s: dropped tell message after stop (payload=%r)",
                        self.name, envelope.payload,
                    )
                return
            self.mailbox.put_nowait(envelope)

        self.ref.loop.call_soon_threadsafe(_enqueue_or_reject)

    def _drain_mailbox(self) -> None:
        """排空邮箱，向所有挂起的 ask 返回 stopped 错误。在 owning loop 中执行。"""
        while True:
            try:
                env = self.mailbox.get_nowait()
                if env.reply_future is not None:
                    self._resolve_future(
                        env.reply_future, env.reply_loop,
                        result=_Reply(ok=False, error=RuntimeError(f"Actor {self.name} stopped")),
                    )
            except asyncio.QueueEmpty:
                break

    def stop(self) -> None:
        """停止 Actor"""
        self.stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
        # 排空邮箱：call_soon_threadsafe FIFO 保证 drain 在所有已入队的 _enqueue_or_reject 之后执行
        self.ref.loop.call_soon_threadsafe(self._drain_mailbox)


class RootLoopActorSystem:
    """
    Root-Loop Actor System。
    
    每个 Root Actor 获得一个专用的 Event Loop，运行在独立线程中。
    Root 的所有 Children 共享同一个 Loop。
    
    Example:
        system = RootLoopActorSystem()
        
        # 每个 spawn 创建一个专用 Loop
        worker = await system.spawn(WorkerActor, "worker")      # Loop 1
        coordinator = await system.spawn(CoordActor, "coord")   # Loop 2
        logger_actor = await system.spawn(LoggerActor, "logger") # Loop 3
        
        # worker 的 children 共享 worker 的 Loop
        child = await worker.spawn_child(ChildActor, "child")   # 同 Loop 1
    """
    
    def __init__(self, name: str = "root-loop-system"):
        self.name = name
        self._loop_contexts: Dict[str, LoopContext] = {}
        self._actor_to_root: Dict[str, str] = {}  # actor_path -> root_name
        self._cells: Dict[str, _RootActorCell] = {}  # actor_path -> cell
        self._lock = threading.Lock()
        self._shutting_down = False
    
    # ========================================================================
    # Loop 管理
    # ========================================================================
    
    def _create_loop(self, root_name: str) -> LoopContext:
        """为 Root Actor 创建专用 Event Loop。"""
        
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        
        def run_loop():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()
        
        thread = threading.Thread(
            target=run_loop,
            name=f"loop-{root_name}",
            daemon=True,
        )
        thread.start()
        ready.wait(timeout=5.0)  # 等待 loop 启动
        
        ctx = LoopContext(
            loop=loop,
            thread=thread,
            root_name=root_name,
        )
        
        logger.info("Created loop for root '%s'", root_name)
        return ctx
    
    def _get_loop_for_actor(self, actor_path: str) -> Optional[asyncio.AbstractEventLoop]:
        """获取 Actor 所在的 Loop。"""
        with self._lock:
            root_name = self._actor_to_root.get(actor_path)
            if root_name is None:
                return None
            ctx = self._loop_contexts.get(root_name)
            return ctx.loop if ctx else None
    
    def _shutdown_loop(self, root_name: str, timeout: float = 5.0) -> None:
        """关闭指定 Root 的 Loop。"""
        with self._lock:
            ctx = self._loop_contexts.pop(root_name, None)
        
        if ctx is None:
            return
        
        # 在 Loop 中执行关闭
        future = asyncio.run_coroutine_threadsafe(
            self._stop_loop_tasks(ctx.loop),
            ctx.loop,
        )
        try:
            future.result(timeout=timeout)
        except Exception:
            pass
        
        # 停止 loop
        ctx.loop.call_soon_threadsafe(ctx.loop.stop)
        ctx.thread.join(timeout=timeout)
        
        logger.info("Shutdown loop for root '%s'", root_name)
    
    async def _stop_loop_tasks(self, loop: asyncio.AbstractEventLoop) -> None:
        """取消 Loop 中的所有任务。"""
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    # ========================================================================
    # Actor 生命周期
    # ========================================================================
    
    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        **kwargs,
    ) -> "RootActorRef[MsgT, RetT]":
        """
        创建 Root Actor（获得专用 Loop）。

        Args:
            actor_cls: Actor 类
            name: Actor 名称（全局唯一）
            **kwargs: 传递给 Actor 构造函数的参数

        Returns:
            RootActorRef
        """
        import inspect
        # fail-fast：_process_message 直接 await on_receive()，不经过 sync bridging
        if (
            "receive" in actor_cls.__dict__
            and callable(actor_cls.__dict__["receive"])
            and not inspect.iscoroutinefunction(actor_cls.__dict__["receive"])
        ):
            raise TypeError(
                f"Actor '{actor_cls.__name__}' defines a sync receive(); "
                "RootLoopActorSystem only supports async on_receive(). "
                "Use ActorSystem() for sync actors."
            )

        with self._lock:
            if name in self._loop_contexts:
                raise ValueError(f"Root actor '{name}' already exists")
            if self._shutting_down:
                raise RuntimeError("System is shutting down")
        
        # 创建专用 Loop
        ctx = self._create_loop(name)
        
        with self._lock:
            self._loop_contexts[name] = ctx
            self._actor_to_root[name] = name
            ctx.actor_count = 1
        
        # 在专用 Loop 中创建 Actor
        # 这里简化：返回一个 ref，实际创建在 Loop 中异步进行
        ref = RootActorRef(
            system=self,
            name=name,
            actor_cls=actor_cls,
            loop=ctx.loop,
            kwargs=kwargs,
        )
        
        # 在 Loop 中初始化 Actor
        future = asyncio.run_coroutine_threadsafe(
            ref._initialize(),
            ctx.loop,
        )
        future.result(timeout=10.0)
        
        logger.info("Spawned root actor '%s'", name)
        return ref
    
    def register_child(self, parent_path: str, child_name: str) -> str:
        """
        注册子 Actor（继承父节点的 Loop）。
        
        Args:
            parent_path: 父 Actor 的路径
            child_name: 子 Actor 名称
        
        Returns:
            子 Actor 的完整路径
        """
        with self._lock:
            # Normalize path (remove leading slash)
            normalized_parent = parent_path.lstrip("/")
            root_name = self._actor_to_root.get(normalized_parent)
            if root_name is None:
                raise ValueError(f"Parent '{parent_path}' not found")
            
            child_path = f"{normalized_parent}/{child_name}"
            self._actor_to_root[child_path] = root_name
            
            ctx = self._loop_contexts.get(root_name)
            if ctx:
                ctx.actor_count += 1
            
            return child_path
    
    def unregister_actor(self, actor_path: str) -> None:
        """注销 Actor。"""
        with self._lock:
            root_name = self._actor_to_root.pop(actor_path, None)
            if root_name:
                ctx = self._loop_contexts.get(root_name)
                if ctx:
                    ctx.actor_count -= 1
    
    # ========================================================================
    # 跨 Loop 通信
    # ========================================================================
    
    def tell(
        self,
        target: "str | RootActorRef",
        message: Any,
    ) -> None:
        """
        发送消息（不等待回复）。
        
        用法：system.tell(target, message)
        
        Args:
            target: 目标 Actor（路径或 Ref）
            message: 消息内容
        """
        # Normalize target
        target_path = target.path if isinstance(target, RootActorRef) else target
        target_path = target_path.lstrip("/")
        
        loop = self._get_loop_for_actor(target_path)
        if loop is None:
            logger.warning("Target '%s' not found, dropping message", target_path)
            return
        
        # 在目标 Loop 中执行
        asyncio.run_coroutine_threadsafe(
            self._deliver_message(target_path, message, sender_path=None),
            loop,
        )
    
    def ask(
        self,
        target: "str | RootActorRef",
        message: Any,
        timeout: float = 30.0,
    ) -> Future:
        """
        发送消息并等待回复。
        
        用法：system.ask(target, message, timeout=30.0)
        
        Args:
            target: 目标 Actor（路径或 Ref）
            message: 消息内容
            timeout: 超时时间
        
        Returns:
            concurrent.futures.Future
        """
        # Normalize target
        target_path = target.path if isinstance(target, RootActorRef) else target
        target_path = target_path.lstrip("/")
        
        loop = self._get_loop_for_actor(target_path)
        if loop is None:
            future = Future()
            future.set_exception(ValueError(f"Target '{target_path}' not found"))
            return future
        
        # 在目标 Loop 中执行
        coro = self._deliver_and_wait(target_path, message, sender_path=None, timeout=timeout)
        return asyncio.run_coroutine_threadsafe(coro, loop)
    
    async def _deliver_message(
        self,
        target_path: str,
        message: Any,
        sender_path: Optional[str],
    ) -> None:
        """在目标 Loop 中投递消息（tell）。"""
        # 查找 Actor Cell
        cell = self._get_cell(target_path)
        if cell is None:
            logger.warning("Target '%s' not found, dropping message", target_path)
            return
        
        # 创建信封（不需要回复）
        envelope = _Envelope(
            payload=message,
            sender_path=sender_path,
        )
        
        # 入队
        cell.enqueue(envelope)
    
    async def _deliver_and_wait(
        self,
        target_path: str,
        message: Any,
        sender_path: Optional[str],
        timeout: float = 30.0,
    ) -> Any:
        """投递消息并等待回复（ask）。"""
        cell = self._get_cell(target_path)
        if cell is None:
            raise ValueError(f"Target '{target_path}' not found")

        caller_loop = asyncio.get_running_loop()
        fut: asyncio.Future = caller_loop.create_future()

        # reply_loop=None → 同 loop 直接 set；否则 call_soon_threadsafe 跨 loop set
        target_loop = cell.ref.loop
        envelope = _Envelope(
            payload=message,
            sender_path=sender_path,
            reply_future=fut,
            reply_loop=None if caller_loop is target_loop else caller_loop,
        )
        cell.enqueue(envelope)

        try:
            reply: _Reply = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"Ask to '{target_path}' timed out after {timeout}s")

        if not reply.ok:
            raise reply.error  # type: ignore[misc]
        return reply.value
    
    def _get_cell(self, actor_path: str) -> Optional[_RootActorCell]:
        """获取 Actor Cell"""
        with self._lock:
            return self._cells.get(actor_path)
    
    # ========================================================================
    # 系统管理
    # ========================================================================
    
    async def shutdown(self, timeout: float = 10.0) -> None:
        """关闭所有 Loop。"""
        self._shutting_down = True
        
        root_names = list(self._loop_contexts.keys())
        for name in root_names:
            self._shutdown_loop(name, timeout=timeout)
        
        self._actor_to_root.clear()
        logger.info("RootLoopActorSystem '%s' shutdown complete", self.name)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息。"""
        with self._lock:
            return {
                "name": self.name,
                "loop_count": len(self._loop_contexts),
                "total_actors": sum(ctx.actor_count for ctx in self._loop_contexts.values()),
                "loops": [
                    {
                        "root": ctx.root_name,
                        "actors": ctx.actor_count,
                        "alive": ctx.is_alive(),
                    }
                    for ctx in self._loop_contexts.values()
                ],
            }


# ============================================================================
# ActorRef
# ============================================================================

class RootActorRef:
    """
    Root Actor 的引用。
    
    封装了跨 Loop 通信逻辑。
    """
    
    def __init__(
        self,
        system: RootLoopActorSystem,
        name: str,
        actor_cls: type,
        loop: asyncio.AbstractEventLoop,
        kwargs: dict,
    ):
        self.system = system
        self.name = name
        self.path = f"/{name}"
        self.actor_cls = actor_cls
        self._loop = loop
        self._kwargs = kwargs
        self._cell: Optional[_RootActorCell] = None
    
    async def _initialize(self) -> None:
        """在专用 Loop 中初始化 Actor。"""
        # 创建 Cell
        self._cell = _RootActorCell(
            actor_cls=self.actor_cls,
            name=self.name,
            kwargs=self._kwargs,
            ref=self,
        )
        
        # 注册到系统
        with self.system._lock:
            self.system._cells[self.name] = self._cell
        
        # 启动 Cell
        await self._cell.start()
    
    def _resolve_target(self, target_or_message: Any, message: Any) -> tuple[str, Any]:
        """Resolve overloaded (target, message) | (message,) calling convention."""
        if message is _MISSING:
            return self.name, target_or_message
        target = target_or_message
        path = target.path if isinstance(target, RootActorRef) else str(target)
        return path.lstrip("/"), message

    def tell(self, target_or_message: Any, message: Any = _MISSING) -> None:
        """
        发送消息（不等待回复）。

        兼容两种调用方式：
        - ref.tell(message)               # 发给自己（旧式）
        - ref.tell(target, message)       # 发给指定目标（新式）
        """
        target_path, actual_message = self._resolve_target(target_or_message, message)

        loop = self.system._get_loop_for_actor(target_path)
        if loop is None:
            logger.warning("Target '%s' not found, dropping message", target_path)
            return

        asyncio.run_coroutine_threadsafe(
            self.system._deliver_message(target_path, actual_message, sender_path=self.name),
            loop,
        )

    def ask(
        self,
        target_or_message: Any,
        message: Any = _MISSING,
        timeout: float = 30.0,
    ) -> Future:
        """
        发送消息并等待回复。

        兼容两种调用方式：
        - ref.ask(message, timeout=30.0)          # 发给自己（旧式）
        - ref.ask(target, message, timeout=30.0)  # 发给指定目标（新式）

        Returns:
            concurrent.futures.Future
        """
        target_path, actual_message = self._resolve_target(target_or_message, message)

        loop = self.system._get_loop_for_actor(target_path)
        if loop is None:
            future: Future = Future()
            future.set_exception(ValueError(f"Target '{target_path}' not found"))
            return future

        coro = self.system._deliver_and_wait(target_path, actual_message, sender_path=self.name, timeout=timeout)
        return asyncio.run_coroutine_threadsafe(coro, loop)
    
    async def spawn_child(
        self,
        actor_cls: type[Actor],
        name: str,
        **kwargs,
    ) -> "RootActorRef":
        """创建子 Actor（共享当前 Loop）。"""
        child_path = self.system.register_child(self.path, name)
        
        # 子 Actor 使用相同的 Loop
        ref = RootActorRef(
            system=self.system,
            name=child_path,
            actor_cls=actor_cls,
            loop=self._loop,
            kwargs=kwargs,
        )
        
        future = asyncio.run_coroutine_threadsafe(
            ref._initialize(),
            self._loop,
        )
        future.result(timeout=10.0)
        
        return ref
    
    def stop(self) -> None:
        """停止 Actor。"""
        if self._cell:
            self._cell.stop()
        
        with self.system._lock:
            self.system._cells.pop(self.name, None)
        
        if self.name in self.system._loop_contexts:
            # Root Actor: 关闭整个 Loop，先原子清理所有共享此 Loop 的子 actor 路由元数据，
            # 防止 tell/ask 在 shutdown 后解析到已销毁的 loop/cell
            with self.system._lock:
                stale_paths = [
                    path for path, root in self.system._actor_to_root.items()
                    if root == self.name
                ]
                for path in stale_paths:
                    self.system._actor_to_root.pop(path, None)
                    self.system._cells.pop(path, None)
            self.system._shutdown_loop(self.name)
        else:
            # Child Actor: 只停止自己（用 name 不用 path，_actor_to_root 无 leading slash）
            self.system.unregister_actor(self.name)
    
    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    import time
    
    async def demo():
        from everything_is_an_actor.core.actor import Actor
        
        class WorkerActor(Actor):
            async def on_receive(self, msg):
                print(f"[Worker] Got: {msg}")
                return f"processed: {msg}"
        
        class LoggerActor(Actor):
            async def on_receive(self, msg):
                print(f"[Logger] Log: {msg}")
        
        system = RootLoopActorSystem("demo")
        
        # 每个 spawn 创建一个专用 Loop
        worker = await system.spawn(WorkerActor, "worker")
        logger_actor = await system.spawn(LoggerActor, "logger")
        
        print("Stats:", system.get_stats())
        
        # 跨 Loop 通信
        worker.tell("hello from main")
        result = worker.ask("ping", timeout=5.0)
        print(f"Result: {result.result(timeout=5.0)}")
        
        # 创建子 Actor（共享 worker 的 Loop）
        child = await worker.spawn_child(WorkerActor, "subtask")
        child.tell("hello from child")
        
        print("Stats after child:", system.get_stats())
        
        await system.shutdown()
    
    asyncio.run(demo())
