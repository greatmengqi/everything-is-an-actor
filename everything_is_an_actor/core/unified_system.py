"""
统一 Actor System

支持三种模式：
- single: 单 Loop，最高性能
- multi-loop: 多 Loop，I/O 隔离
- multi-process: 多进程，CPU 并行
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

from everything_is_an_actor.core.backend import ActorBackend
from everything_is_an_actor.core.composable_future import ComposableFuture

if TYPE_CHECKING:
    from everything_is_an_actor.core.actor import Actor
    from everything_is_an_actor.core.ref import ActorRef

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")

logger = logging.getLogger(__name__)


class ActorSystemMode(str, Enum):
    """Actor System 模式"""

    SINGLE = "single"
    MULTI_LOOP = "multi-loop"
    MULTI_PROCESS = "multi-process"
    AUTO = "auto"


@dataclass
class ActorSystemConfig:
    """Actor System 配置"""

    mode: ActorSystemMode = ActorSystemMode.SINGLE
    num_workers: int = 4
    mailbox_size: int = 10000
    default_timeout: float = 30.0
    shutdown_timeout: float = 10.0
    batch_size: int = 100
    # Backpressure policy for multi-loop backend: 'block' (default, non-lossy), 'drop', or 'fail'
    backpressure_policy: str = "block"


class ActorSystem:
    """
    统一 Actor System。

    通过 mode 参数切换后端实现：

    Example:
        # 单 Loop（默认，最高性能）
        system = ActorSystem()

        # 多 Loop（I/O 隔离）
        system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP, num_workers=4)

        # 自动选择
        system = ActorSystem(mode=ActorSystemMode.AUTO)
    """

    def __init__(
        self,
        name: str = "actor-system",
        *,
        mode: ActorSystemMode = ActorSystemMode.SINGLE,
        num_workers: int = 4,
        config: Optional[ActorSystemConfig] = None,
    ):
        self.name = name

        if config is None:
            config = ActorSystemConfig(
                mode=mode,
                num_workers=num_workers,
            )

        self.config = config

        actual_mode = ActorSystemMode(mode)
        if actual_mode is ActorSystemMode.AUTO:
            actual_mode = self._detect_best_mode()
            config.mode = actual_mode

        self._backend: ActorBackend = self._create_backend(actual_mode, config)

    @staticmethod
    def _detect_best_mode() -> ActorSystemMode:
        """自动检测最佳模式（multi-process 未实现，auto 不选它）。"""
        cpu_count = os.cpu_count() or 1
        return ActorSystemMode.SINGLE if cpu_count <= 2 else ActorSystemMode.MULTI_LOOP

    def _create_backend(
        self,
        mode: ActorSystemMode,
        config: ActorSystemConfig,
    ) -> ActorBackend:
        """创建后端"""
        if mode is ActorSystemMode.SINGLE:
            from everything_is_an_actor.backends.single_loop import SingleLoopBackend

            return SingleLoopBackend(name=self.name, config=config)

        if mode is ActorSystemMode.MULTI_LOOP:
            from everything_is_an_actor.backends.multi_loop import MultiLoopBackend

            return MultiLoopBackend(name=self.name, config=config)

        if mode is ActorSystemMode.MULTI_PROCESS:
            raise ValueError("mode=MULTI_PROCESS is not yet implemented. Use SINGLE or MULTI_LOOP instead.")

        raise ValueError(f"Unknown mode: {mode}")

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        **kwargs,
    ) -> ActorRef[MsgT, RetT]:
        """
        创建 Actor。

        Args:
            actor_cls: Actor 类
            name: Actor 名称
            **kwargs: 传递给 Actor 的参数

        Returns:
            ActorRef

        Raises:
            TypeError: 在 multi-loop 模式下尝试使用 sync receive() 的 Actor。
        """
        # 兼容性校验委托给 backend（单一权威），避免 config.mode 与实际 backend 产生 skew
        return await self._backend.spawn(actor_cls, name, **kwargs)

    async def tell(self, ref: ActorRef, message: Any) -> None:
        """发送消息（不等待回复）。"""
        import asyncio as _aio

        result = ref._tell(message)
        if _aio.iscoroutine(result):
            await result

    def ask(
        self,
        ref: ActorRef,
        message: Any,
        timeout: Optional[float] = None,
    ) -> ComposableFuture:
        """发送消息并等待回复。返回 ComposableFuture 支持链式组合。"""
        t = timeout if timeout is not None else self.config.default_timeout
        return ref._ask(message, timeout=t)

    async def spawn_child(
        self,
        parent_ref: ActorRef,
        actor_cls: type[Actor],
        name: str,
        **kwargs,
    ) -> ActorRef:
        """创建子 Actor"""
        return await self._backend.spawn_child(parent_ref, actor_cls, name, **kwargs)

    async def stop(self, ref: ActorRef) -> None:
        """停止 Actor"""
        await self._backend.stop(ref)

    async def shutdown(self) -> None:
        """关闭系统"""
        await self._backend.shutdown()

    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        return self._backend.get_stats()

    @property
    def mode(self) -> ActorSystemMode:
        """当前模式"""
        return self.config.mode
