"""
Multi Loop Backend

多事件循环后端，I/O 隔离。
每个 Root Actor 一个专用 Loop，阻塞操作不影响其他。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

from everything_is_an_actor.backend import ActorBackend

if TYPE_CHECKING:
    from everything_is_an_actor.actor import Actor
    from everything_is_an_actor.ref import ActorRef
    from everything_is_an_actor.unified_system import ActorSystemConfig

logger = logging.getLogger(__name__)

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


class BackpressurePolicy(str, Enum):
    """Backpressure policy for multi-loop backend queues."""
    BLOCK = "block"  # Block until queue has space (neither tell nor ask are dropped)
    DROP = "drop"    # Drop messages silently (tell only, ask always fails)
    FAIL = "fail"    # Raise exception for both tell and ask


class MailboxFullError(RuntimeError):
    """Raised when mailbox is full and backpressure policy is FAIL."""
    pass



@dataclass
class _Envelope:
    """消息信封

    ask() 通过 ComposableFuture.promise() 注入 resolve/reject 回调。
    回调自带跨 loop 线程安全（内部自动检测并走 call_soon_threadsafe），
    不再需要 reply_loop 字段。
    """
    payload: Any
    sender_path: Optional[str]
    resolve: Optional[Any] = None  # Callable[[Any], None] | None
    reject: Optional[Any] = None   # Callable[[Exception], None] | None


@dataclass
class LoopContext:
    """Loop 上下文"""
    loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    root_name: str
    actor_count: int = 0


class _RootActorCell:
    """Root Actor 运行时容器"""
    
    def __init__(
        self,
        actor_cls: type,
        name: str,
        kwargs: dict,
        loop: asyncio.AbstractEventLoop,
        backend: "MultiLoopBackend",
        maxsize: int = 0,
        backpressure_policy: BackpressurePolicy = BackpressurePolicy.BLOCK,
    ):
        self.actor_cls = actor_cls
        self.name = name
        self.kwargs = kwargs
        self.loop = loop
        self.backend = backend
        self.backpressure_policy = backpressure_policy

        self.actor = None
        # maxsize=0 → 无界；> 0 → 有界（由 config.mailbox_size 控制）
        self.mailbox: asyncio.Queue[_Envelope] = asyncio.Queue(maxsize=maxsize)
        self.stopped = False
        self._task: Optional[asyncio.Task] = None
        # Dead-letter counters — observable via get_stats()
        self.dropped_stopped: int = 0    # tell dropped: actor stopped
        self.dropped_full: int = 0       # tell dropped: mailbox full
        self.dropped_closed: int = 0     # tell dropped: owning loop closed
    
    async def start(self) -> None:
        """启动 Actor：先完成 on_started，再启动消息循环，保证初始化顺序。

        启动失败时重新抛出异常，由 spawn() 负责清理资源，确保不会产生僵尸引用。
        """
        self.actor = self.actor_cls(**self.kwargs)
        try:
            await self.actor.on_started()
        except Exception as e:
            logger.error("Actor %s on_started failed: %s", self.name, e)
            raise
        self._task = asyncio.create_task(self._run())
    
    async def _run(self) -> None:
        """消息处理循环"""
        while not self.stopped:
            try:
                env = await self.mailbox.get()

                if self.stopped:
                    if env.reject is not None:
                        env.reject(RuntimeError(f"Actor {self.name} stopped"))
                    else:
                        logger.warning(
                            "Actor %s: dropped tell message after stop (payload=%r)",
                            self.name, env.payload,
                        )
                    break

                try:
                    await self._process_message(env)
                except asyncio.CancelledError:
                    if env.reject is not None:
                        env.reject(RuntimeError(f"Actor {self.name} stopped"))
                    raise

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Actor %s error: %s", self.name, e)

        if self.actor:
            try:
                await self.actor.on_stopped()
            except Exception:
                pass
    
    async def _process_message(self, env: _Envelope) -> None:
        """处理消息"""
        if self.actor is None:
            return

        try:
            result = await self.actor.on_receive(env.payload)
            if env.resolve is not None:
                env.resolve(result)
        except Exception as e:
            if env.reject is not None:
                env.reject(e)
            logger.error("Actor %s failed to process message: %s", self.name, e)

    def _reject_envelope(self, envelope: _Envelope, reason: str, counter_attr: str) -> None:
        """Reject an envelope: reply with error for ask, increment dead-letter counter for tell."""
        if envelope.reject is not None:
            envelope.reject(RuntimeError(f"Actor {self.name} {reason}"))
        else:
            setattr(self, counter_attr, getattr(self, counter_attr) + 1)
            logger.warning("Actor %s: dropped tell, %s (payload=%r)", self.name, reason, envelope.payload)

    async def _enqueue_with_backpressure(self, envelope: _Envelope) -> None:
        """Enqueue with backpressure policy handling."""
        if self.backpressure_policy == BackpressurePolicy.BLOCK:
            # Block until queue has space (non-lossy, default)
            await self.mailbox.put(envelope)
        elif self.backpressure_policy == BackpressurePolicy.DROP:
            # Drop silently (tell only, ask always fails)
            try:
                self.mailbox.put_nowait(envelope)
            except asyncio.QueueFull:
                self._reject_envelope(envelope, "mailbox full", "dropped_full")
        elif self.backpressure_policy == BackpressurePolicy.FAIL:
            # Raise exception for both tell and ask
            try:
                self.mailbox.put_nowait(envelope)
            except asyncio.QueueFull:
                # Reject the envelope, which will raise for ask and increment counter for tell
                self._reject_envelope(envelope, "mailbox full", "dropped_full")
                # For tell, also raise an exception to make it visible
                if envelope.resolve is None:
                    raise MailboxFullError(
                        f"Actor '{self.name}' mailbox full and backpressure policy is FAIL. "
                        "Message dropped. Consider increasing mailbox_size or using BLOCK policy."
                    )
        else:
            # Unknown policy, default to BLOCK for safety
            await self.mailbox.put(envelope)

    def enqueue_from_same_loop(self, envelope: _Envelope) -> None:
        """从同一个 Loop 入队（无跨线程）"""
        if self.stopped:
            self._reject_envelope(envelope, "stopped", "dropped_stopped")
            return

        if self.backpressure_policy == BackpressurePolicy.BLOCK:
            # For BLOCK policy, we need to handle this differently since we're in synchronous context
            # We'll use put_nowait and if full, we'll create a task to put it asynchronously
            try:
                self.mailbox.put_nowait(envelope)
            except asyncio.QueueFull:
                # Create a task to put it in the background
                asyncio.create_task(self.mailbox.put(envelope))
        elif self.backpressure_policy == BackpressurePolicy.DROP:
            # Drop silently
            try:
                self.mailbox.put_nowait(envelope)
            except asyncio.QueueFull:
                self._reject_envelope(envelope, "mailbox full", "dropped_full")
        elif self.backpressure_policy == BackpressurePolicy.FAIL:
            # Raise exception
            try:
                self.mailbox.put_nowait(envelope)
            except asyncio.QueueFull:
                self._reject_envelope(envelope, "mailbox full", "dropped_full")
                if envelope.resolve is None:
                    raise MailboxFullError(
                        f"Actor '{self.name}' mailbox full and backpressure policy is FAIL. "
                        "Message dropped. Consider increasing mailbox_size or using BLOCK policy."
                    )

    def enqueue_from_other_loop(self, envelope: _Envelope) -> None:
        """从其他 Loop 入队（跨线程安全，check-and-enqueue 在 owning loop 中原子执行）"""
        def _enqueue_or_reject() -> None:
            if self.stopped or self.backend.is_shutting_down:
                self._reject_envelope(envelope, "stopped", "dropped_stopped")
                return

            if self.backpressure_policy == BackpressurePolicy.BLOCK:
                # For BLOCK policy, create a task to put (will block until space available)
                asyncio.create_task(self.mailbox.put(envelope))
            elif self.backpressure_policy == BackpressurePolicy.DROP:
                # Drop silently
                try:
                    self.mailbox.put_nowait(envelope)
                except asyncio.QueueFull:
                    self._reject_envelope(envelope, "mailbox full", "dropped_full")
            elif self.backpressure_policy == BackpressurePolicy.FAIL:
                # Raise exception
                try:
                    self.mailbox.put_nowait(envelope)
                except asyncio.QueueFull:
                    self._reject_envelope(envelope, "mailbox full", "dropped_full")
                    if envelope.resolve is None:
                        # For tell, log error (can't raise across threads)
                        logger.error(
                            "Actor '%s': mailbox full and backpressure policy is FAIL. "
                            "Message dropped. Consider increasing mailbox_size or using BLOCK policy.",
                            self.name
                        )

        try:
            self.loop.call_soon_threadsafe(_enqueue_or_reject)
        except RuntimeError:
            self._reject_envelope(envelope, "loop closed", "dropped_closed")

    def _drain_mailbox(self) -> None:
        """排空邮箱，向所有挂起的 ask 返回 stopped 错误。

        在 owning loop 线程中执行，在 stop() 后通过 call_soon_threadsafe 调用，
        确保所有在 stopped=True 设置前已入队的消息得到妥善拒绝。
        """
        while True:
            try:
                env = self.mailbox.get_nowait()
                if env.reject is not None:
                    env.reject(RuntimeError(f"Actor {self.name} stopped"))
            except asyncio.QueueEmpty:
                break

    def stop(self) -> None:
        """停止"""
        self.stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
        # 在 owning loop 中排空邮箱：所有在 stopped=True 前入队的 _enqueue_or_reject
        # 闭包均已在队列中（call_soon_threadsafe FIFO），drain 在它们之后执行
        self.loop.call_soon_threadsafe(self._drain_mailbox)


class _MultiLoopActorRef:
    """Multi Loop Actor 引用"""
    
    def __init__(
        self,
        cell: _RootActorCell,
        name: str,
        backend: "MultiLoopBackend",
    ):
        self._cell = cell
        self.name = name
        self.path = f"/{name}"
        self._backend = backend
        self.loop = cell.loop
    
    def _tell(self, message: Any) -> None:
        """发送消息（不等待）"""
        envelope = _Envelope(payload=message, sender_path=None)

        try:
            current_loop = asyncio.get_running_loop()
            if current_loop is self.loop:
                self._cell.enqueue_from_same_loop(envelope)
            else:
                self._cell.enqueue_from_other_loop(envelope)
        except RuntimeError:
            self._cell.enqueue_from_other_loop(envelope)

    def _ask(self, message: Any, timeout: float = 30.0) -> ComposableFuture:
        """发送消息并等待回复。返回 ComposableFuture 支持链式组合。

        Uses ``ComposableFuture.promise()`` — resolve/reject callbacks are
        thread-safe, so the actor (on its own loop) can resolve without
        manual ``call_soon_threadsafe`` plumbing.
        """
        from everything_is_an_actor.composable_future import ComposableFuture

        async def _ask() -> Any:
            cf, resolve, reject = ComposableFuture.promise()
            envelope = _Envelope(
                payload=message, sender_path=None,
                resolve=resolve, reject=reject,
            )
            self._cell.enqueue_from_same_loop(envelope)
            return await cf.with_timeout(timeout)

        return ComposableFuture(_ask(), loop=self.loop)
    
    def stop(self) -> None:
        """停止"""
        self._cell.stop()


class MultiLoopBackend(ActorBackend):
    """
    多 Loop 后端。
    
    每个 Root Actor 一个专用 Loop。
    """
    
    def __init__(self, name: str, config: ActorSystemConfig):
        self.name = name
        self.config = config
        
        self._loop_contexts: Dict[str, LoopContext] = {}
        self._cells: Dict[str, _RootActorCell] = {}
        self._root_descendants: Dict[str, set[str]] = {}  # root_name → child names
        self._lock = threading.Lock()
        self._shutting_down = False
    
    def _create_loop(self, root_name: str) -> LoopContext:
        """创建专用 Loop"""
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
        ready.wait(timeout=5.0)
        
        return LoopContext(
            loop=loop,
            thread=thread,
            root_name=root_name,
        )
    
    def _shutdown_loop(self, root_name: str, timeout: float = 5.0) -> None:
        """关闭 Loop"""
        with self._lock:
            ctx = self._loop_contexts.pop(root_name, None)
        
        if ctx is None:
            return
        
        # 停止 loop
        ctx.loop.call_soon_threadsafe(ctx.loop.stop)
        ctx.thread.join(timeout=timeout)
    
    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def _rollback_spawn(
        self, name: str, *, shutdown_loop: bool = False, parent_name: Optional[str] = None,
    ) -> None:
        """Undo a failed spawn: remove cell, optionally close loop or decrement parent count."""
        with self._lock:
            self._cells.pop(name, None)
            if parent_name is not None:
                root_name = parent_name.split("/")[0]
                descendants = self._root_descendants.get(root_name)
                if descendants is not None:
                    descendants.discard(name)
                ctx = self._loop_contexts.get(parent_name)
                if ctx is not None:
                    ctx.actor_count = max(0, ctx.actor_count - 1)
            if shutdown_loop:
                self._root_descendants.pop(name, None)
        if shutdown_loop:
            self._shutdown_loop(name)

    @staticmethod
    def _canonical_name(name: str) -> str:
        """规范化 actor name：去除 leading slash，保证 _cells/_loop_contexts key 统一。"""
        return name.lstrip("/")

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        **kwargs,
    ) -> ActorRef[MsgT, RetT]:
        """创建 Actor"""
        # Validate AgentActor compatibility at spawn-time
        from everything_is_an_actor.validation import validate_agent_actor_compatibility
        validate_agent_actor_compatibility(actor_cls, mode="multi-loop")

        name = self._canonical_name(name)
        with self._lock:
            if name in self._cells:
                raise ValueError(f"Actor '{name}' already exists")
            if self._shutting_down:
                raise RuntimeError("System is shutting down")

        # 提取系统级 kwargs，避免污染 actor 构造函数
        mailbox_size = kwargs.pop("mailbox_size", self.config.mailbox_size)
        backpressure_policy_str = kwargs.pop("backpressure_policy", self.config.backpressure_policy)
        kwargs.pop("mailbox", None)  # multi-loop 自行管理 asyncio.Queue

        # Parse backpressure policy
        try:
            backpressure_policy = BackpressurePolicy(backpressure_policy_str.lower())
        except ValueError:
            logger.warning(
                "Invalid backpressure_policy '%s', defaulting to BLOCK. "
                "Valid values: 'block', 'drop', 'fail'",
                backpressure_policy_str
            )
            backpressure_policy = BackpressurePolicy.BLOCK

        # 创建专用 Loop
        ctx = self._create_loop(name)

        with self._lock:
            self._loop_contexts[name] = ctx
            self._root_descendants[name] = set()
            ctx.actor_count = 1

        # 创建 Cell
        cell = _RootActorCell(
            actor_cls=actor_cls,
            name=name,
            kwargs=kwargs,
            loop=ctx.loop,
            backend=self,
            maxsize=mailbox_size,
            backpressure_policy=backpressure_policy,
        )

        with self._lock:
            self._cells[name] = cell

        # 在 Loop 中启动；启动失败时原子清理，避免产生僵尸引用
        future = asyncio.run_coroutine_threadsafe(cell.start(), ctx.loop)
        try:
            future.result(timeout=10.0)
        except Exception as e:
            self._rollback_spawn(name, shutdown_loop=True)
            raise RuntimeError(f"Actor '{name}' failed to start: {e}") from e

        return _MultiLoopActorRef(cell, name, self)
    
    async def tell(
        self,
        ref: ActorRef,
        message: Any,
    ) -> None:
        """发送消息"""
        result = ref._tell(message)
        if asyncio.iscoroutine(result):
            await result
    
    async def ask(
        self,
        ref: ActorRef,
        message: Any,
        timeout: float,
    ) -> Any:
        """发送消息并等待回复"""
        return await ref._ask(message, timeout=timeout)
    
    async def spawn_child(
        self,
        parent_ref: ActorRef,
        actor_cls: type[Actor],
        name: str,
        **kwargs,
    ) -> ActorRef:
        """创建子 Actor（共享父节点的 Loop）"""
        # Validate AgentActor compatibility at spawn-time
        from everything_is_an_actor.validation import validate_agent_actor_compatibility
        validate_agent_actor_compatibility(actor_cls, mode="multi-loop")

        parent_name = self._canonical_name(parent_ref.name)
        child_name = self._canonical_name(f"{parent_name}/{name}")

        with self._lock:
            if child_name in self._cells:
                raise ValueError(f"Actor '{child_name}' already exists")

            parent_ctx = self._loop_contexts.get(parent_name)
            if parent_ctx is None:
                raise ValueError(f"Parent '{parent_name}' not found")
        
        # 提取系统级 kwargs，避免污染 actor 构造函数
        mailbox_size = kwargs.pop("mailbox_size", self.config.mailbox_size)
        backpressure_policy_str = kwargs.pop("backpressure_policy", self.config.backpressure_policy)
        kwargs.pop("mailbox", None)

        # Parse backpressure policy
        try:
            backpressure_policy = BackpressurePolicy(backpressure_policy_str.lower())
        except ValueError:
            logger.warning(
                "Invalid backpressure_policy '%s', defaulting to BLOCK. "
                "Valid values: 'block', 'drop', 'fail'",
                backpressure_policy_str
            )
            backpressure_policy = BackpressurePolicy.BLOCK

        # 创建 Cell（共享 Loop）
        cell = _RootActorCell(
            actor_cls=actor_cls,
            name=child_name,
            kwargs=kwargs,
            loop=parent_ref.loop,
            backend=self,
            maxsize=mailbox_size,
            backpressure_policy=backpressure_policy,
        )
        
        root_name = parent_name.split("/")[0]
        with self._lock:
            self._cells[child_name] = cell
            parent_ctx.actor_count += 1
            descendants = self._root_descendants.get(root_name)
            if descendants is not None:
                descendants.add(child_name)

        # 在共享 Loop 中启动；启动失败时回滚，避免产生僵尸引用
        future = asyncio.run_coroutine_threadsafe(cell.start(), parent_ref.loop)
        try:
            future.result(timeout=10.0)
        except Exception as e:
            self._rollback_spawn(child_name, parent_name=parent_name)
            raise RuntimeError(f"Child actor '{child_name}' failed to start: {e}") from e

        return _MultiLoopActorRef(cell, child_name, self)
    
    def _collect_descendants(self, name: str) -> list[_RootActorCell]:
        """Collect and remove all descendant cells of *name* from _cells and _root_descendants.

        Must be called while holding self._lock.
        """
        root_name = name.split("/")[0]
        descendants = self._root_descendants.get(root_name)
        if descendants is None:
            return []

        # Partition: names that are descendants of *name*
        prefix = f"{name}/"
        to_remove = [d for d in descendants if d.startswith(prefix)]
        cells: list[_RootActorCell] = []
        for d in to_remove:
            descendants.discard(d)
            cell = self._cells.pop(d, None)
            if cell is not None:
                cells.append(cell)
        return cells

    async def stop(self, ref: ActorRef) -> None:
        """停止 Actor"""
        ref.stop()

        name = self._canonical_name(ref.name)
        is_root = False
        root_cell: Optional[_RootActorCell] = None
        child_cells: list[_RootActorCell] = []
        with self._lock:
            root_cell = self._cells.pop(name, None)
            is_root = name in self._loop_contexts
            child_cells = self._collect_descendants(name)
            if is_root:
                self._root_descendants.pop(name, None)
            else:
                # 更新父 root 的 actor_count（本 actor + 其后代），避免 get_stats() 统计膨胀
                root_name = name.split("/")[0]
                descendants = self._root_descendants.get(root_name)
                if descendants is not None:
                    descendants.discard(name)
                ctx = self._loop_contexts.get(root_name)
                if ctx is not None:
                    ctx.actor_count = max(0, ctx.actor_count - 1 - len(child_cells))

        # 在锁外停止子 cell，避免持锁期间操作耗时资源
        for cell in child_cells:
            cell.stop()

        # 释放锁后再调 _shutdown_loop，避免与 _shutdown_loop 内部的 with self._lock 死锁
        if is_root:
            # 等待 root+child 任务真正结束后再关 loop，确保 stop() 返回时无消息仍在处理
            all_cells = ([root_cell] if root_cell is not None else []) + child_cells
            wait_futures = []
            for c in all_cells:
                if c._task is not None:
                    wait_futures.append(
                        asyncio.run_coroutine_threadsafe(
                            asyncio.wait_for(asyncio.shield(c._task), timeout=5.0),
                            c.loop,
                        )
                    )
            for fut in wait_futures:
                try:
                    fut.result(timeout=6.0)
                except Exception as e:
                    logger.warning("Actor '%s': task did not quiesce cleanly during stop: %s", name, e)
            self._shutdown_loop(name)
    
    async def shutdown(self) -> None:
        """关闭系统（有序：先停 cell 触发 on_stopped，再关 loop）"""
        self._shutting_down = True

        with self._lock:
            cells = list(self._cells.values())
            root_names = list(self._loop_contexts.keys())

        # 在各自 owning loop 中停止 cell，确保 on_stopped() 执行
        stop_futures = []
        for cell in cells:
            cell.stop()
            if cell._task is not None:
                stop_futures.append(
                    asyncio.run_coroutine_threadsafe(
                        asyncio.wait_for(asyncio.shield(cell._task), timeout=5.0),
                        cell.loop,
                    )
                )
        for fut in stop_futures:
            try:
                fut.result(timeout=6.0)
            except Exception as e:
                logger.warning("Actor task did not quiesce cleanly during shutdown: %s", e)

        # 所有 cell 已停止，关闭各 loop 线程
        for name in root_names:
            self._shutdown_loop(name)

        with self._lock:
            self._cells.clear()
            self._root_descendants.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            cells = list(self._cells.values())
            return {
                "mode": self.config.mode,
                "name": self.name,
                "loop_count": len(self._loop_contexts),
                "total_actors": sum(ctx.actor_count for ctx in self._loop_contexts.values()),
                "dead_letters": {
                    "dropped_stopped": sum(c.dropped_stopped for c in cells),
                    "dropped_full": sum(c.dropped_full for c in cells),
                    "dropped_closed": sum(c.dropped_closed for c in cells),
                },
            }
