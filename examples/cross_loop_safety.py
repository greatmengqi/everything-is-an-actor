"""
跨 Loop 通信的线程安全保障

关键保障：
1. 线程安全：所有操作在目标 loop 中执行
2. Future 正确传递：使用全局注册表
3. 异常传播：通过 Future.set_exception
4. 消息顺序：asyncio.run_coroutine_threadsafe 保证 FIFO
5. 生命周期：原子检查+操作
6. 死锁预防：timeout + 非阻塞 API
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ============================================================================
# 核心数据结构
# ============================================================================

@dataclass
class Envelope:
    """消息信封（跨 loop 传递的数据结构）。"""
    payload: Any
    sender: Optional[str] = None
    correlation_id: Optional[str] = None
    reply_to: Optional[str] = None  # system_id of caller


@dataclass
class Reply:
    """回复消息。"""
    correlation_id: str
    result: Any = None
    error: Optional[Exception] = None


# ============================================================================
# 全局回复注册表（线程安全）
# ============================================================================

class ReplyRegistry:
    """
    全局回复注册表。

    为什么需要？
    - Future 绑定到创建它的 loop
    - 跨 loop 不能直接操作 Future
    - 通过注册表 + call_soon_threadsafe 解决
    """

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()

    def register(self, corr_id: str, loop: asyncio.AbstractEventLoop) -> asyncio.Future:
        """注册一个等待回复的 Future。"""
        future = loop.create_future()
        with self._lock:
            self._pending[corr_id] = future
        return future

    def resolve(self, reply: Reply, target_loop: asyncio.AbstractEventLoop):
        """解决 Future（线程安全）。"""
        with self._lock:
            future = self._pending.pop(reply.correlation_id, None)

        if future is None:
            return  # 已超时或已取消

        # 在 Future 所属的 loop 中设置结果
        if reply.error:
            target_loop.call_soon_threadsafe(
                future.set_exception,
                reply.error,
            )
        else:
            target_loop.call_soon_threadsafe(
                future.set_result,
                reply.result,
            )

    def discard(self, corr_id: str):
        """丢弃 Future（超时时）。"""
        with self._lock:
            self._pending.pop(corr_id, None)


# ============================================================================
# 跨 Loop ActorRef
# ============================================================================

class CrossLoopActorRef:
    """支持跨事件循环通信的 Actor 引用。"""

    def __init__(
        self,
        cell,  # _ActorCell
        loop: asyncio.AbstractEventLoop,
        registry: ReplyRegistry,
        system_id: str,
    ):
        self._cell = cell
        self._loop = loop
        self._registry = registry
        self._system_id = system_id

    @property
    def path(self) -> str:
        return self._cell.path

    @property
    def is_alive(self) -> bool:
        return not self._cell.stopped

    # ========================================================================
    # tell: 单向消息
    # ========================================================================

    async def tell(self, message: Any, sender: Optional[CrossLoopActorRef] = None):
        """
        发送单向消息（线程安全）。

        保障：
        1. 消息在目标 loop 中入队
        2. 状态检查在目标 loop 中原子执行
        3. 死信处理
        """
        current_loop = asyncio.get_running_loop()

        if current_loop is self._loop:
            # 同 loop：直接操作
            await self._tell_internal(message, sender)
        else:
            # 跨 loop：在目标 loop 中执行
            future = asyncio.run_coroutine_threadsafe(
                self._tell_internal(message, sender),
                self._loop,
            )
            await asyncio.wrap_future(future)

    async def _tell_internal(self, message: Any, sender: Optional[CrossLoopActorRef]):
        """内部：在目标 loop 中执行 tell。"""
        # 原子操作：检查状态 + 入队
        if self._cell.stopped:
            # 发送到死信队列
            self._cell.system._dead_letter(self, message, sender)
            return

        await self._cell.enqueue(Envelope(
            payload=message,
            sender=sender.path if sender else None,
        ))

    # ========================================================================
    # ask: 请求-响应
    # ========================================================================

    async def ask(
        self,
        message: Any,
        timeout: float = 5.0,
    ) -> Any:
        """
        发送请求并等待响应（线程安全）。

        保障：
        1. Future 在调用者 loop 中创建
        2. 回复通过注册表路由到正确的 loop
        3. 超时自动清理
        4. 异常正确传播
        """
        current_loop = asyncio.get_running_loop()

        # 生成 correlation ID
        corr_id = uuid.uuid4().hex

        # 在当前 loop 注册 Future
        future = self._registry.register(corr_id, current_loop)

        try:
            # 发送消息
            if current_loop is self._loop:
                await self._ask_internal(message, corr_id)
            else:
                future_send = asyncio.run_coroutine_threadsafe(
                    self._ask_internal(message, corr_id),
                    self._loop,
                )
                await asyncio.wrap_future(future_send)

            # 等待回复（在当前 loop）
            return await asyncio.wait_for(future, timeout=timeout)

        except asyncio.TimeoutError:
            # 超时清理
            self._registry.discard(corr_id)
            raise

        except Exception:
            # 异常清理
            self._registry.discard(corr_id)
            raise

    async def _ask_internal(self, message: Any, corr_id: str):
        """内部：在目标 loop 中执行 ask 发送。"""
        if self._cell.stopped:
            raise RuntimeError(f"Actor {self.path} is stopped")

        await self._cell.enqueue(Envelope(
            payload=message,
            correlation_id=corr_id,
            reply_to=self._system_id,
        ))

    # ========================================================================
    # 回复处理（目标 actor 调用）
    # ========================================================================

    def send_reply(
        self,
        corr_id: str,
        result: Any = None,
        error: Optional[Exception] = None,
        target_loop: asyncio.AbstractEventLoop = None,
    ):
        """
        发送回复（线程安全）。

        由目标 actor 调用，回复给调用者。
        """
        reply = Reply(
            correlation_id=corr_id,
            result=result,
            error=error,
        )

        # 通过注册表路由到调用者的 loop
        self._registry.resolve(reply, target_loop)


# ============================================================================
# 使用示例
# ============================================================================

"""
# 创建多 loop 系统
system = MultiLoopActorSystem(num_loops=4)

# Actor A 在 Loop 0
actor_a = await system.spawn(ActorA, "a")  # → Loop 0

# Actor B 在 Loop 1
actor_b = await system.spawn(ActorB, "b")  # → Loop 1

# 跨 loop 通信（自动线程安全）
result = await actor_a.ask(actor_b, "hello")  # ← 内部处理所有线程安全

# 流程：
# 1. Actor A (Loop 0) 创建 Future，注册到 registry
# 2. 消息通过 run_coroutine_threadsafe 发送到 Loop 1
# 3. Actor B (Loop 1) 处理消息
# 4. Actor B 调用 send_reply
# 5. 通过 call_soon_threadsafe 设置 Future 结果
# 6. Actor A 收到结果
"""


# ============================================================================
# 线程安全检查清单
# ============================================================================

THREAD_SAFETY_CHECKLIST = """
跨 Loop 通信线程安全检查清单：

✅ 1. 所有 mailbox 操作在目标 loop 中执行
   - 不直接访问其他 loop 的数据结构
   - 使用 run_coroutine_threadsafe

✅ 2. Future 在调用者 loop 中创建
   - Future 绑定到创建它的 loop
   - 通过 call_soon_threadsafe 设置结果

✅ 3. 状态检查原子化
   - 检查 + 操作在同一次调度中完成
   - 不暴露竞态窗口

✅ 4. 异常正确传播
   - 使用 Future.set_exception
   - 在调用者 loop 中设置

✅ 5. 超时清理
   - 超时后从注册表移除 Future
   - 防止内存泄漏

✅ 6. 死信处理
   - 发送到已停止 actor 的消息进入死信队列
   - 不静默丢弃

✅ 7. 顺序保证
   - run_coroutine_threadsafe 内部使用 FIFO 队列
   - 同一 actor 的消息按发送顺序处理

✅ 8. 死锁预防
   - ask 必须有 timeout
   - 提供非阻塞 tell API
"""


# ============================================================================
# 性能优化
# ============================================================================

"""
优化 1：避免不必要的跨 loop

async def tell(self, msg):
    current = asyncio.get_running_loop()
    if current is self._loop:
        # 同 loop，零开销
        await self._tell_internal(msg)
    else:
        # 跨 loop，有线程同步开销
        ...


优化 2：批量消息

# 跨 loop 发送 100 条消息
for msg in messages:
    await actor.tell(msg)  # ← 100 次线程同步

# 优化：批量发送
await actor.tell_batch(messages)  # ← 1 次线程同步


优化 3：消息池

# 重用 Envelope 对象（减少 GC）
pool = EnvelopePool()
envelope = pool.acquire()
try:
    envelope.payload = msg
    await actor._cell.enqueue(envelope)
finally:
    pool.release(envelope)
"""
