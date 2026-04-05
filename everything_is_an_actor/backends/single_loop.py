"""
Single Loop Backend

单事件循环后端，最高性能。
适用于纯异步、无阻塞场景。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, TypeVar

from everything_is_an_actor.backend import ActorBackend

if TYPE_CHECKING:
    from everything_is_an_actor.actor import Actor
    from everything_is_an_actor.ref import ActorRef
    from everything_is_an_actor.unified_system import ActorSystemConfig

logger = logging.getLogger(__name__)

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


class SingleLoopBackend(ActorBackend):
    """
    单 Loop 后端。
    
    所有 Actor 共享一个事件循环，最高性能。
    """
    
    def __init__(self, name: str, config: "ActorSystemConfig"):
        self.name = name
        self.config = config

        if config.num_workers > 1:
            import warnings
            warnings.warn(
                f"ActorSystem(mode='single', num_workers={config.num_workers}): "
                "num_workers is ignored in single-loop mode. "
                "Use mode='multi-loop' for I/O isolation or ActorSystem(threaded=True) for sync actors.",
                UserWarning,
                stacklevel=3,
            )

        # 使用现有的 ActorSystem 实现（避免命名冲突）
        from everything_is_an_actor import system as _system_module
        self._system = _system_module.ActorSystem(
            name=name,
        )
        self._backend_system = self._system  # 别名，避免混淆
    
    async def spawn(
        self,
        actor_cls: type["Actor[MsgT, RetT]"],
        name: str,
        **kwargs,
    ) -> "ActorRef[MsgT, RetT]":
        """创建 Actor"""
        # Validate AgentActor compatibility at spawn-time
        from everything_is_an_actor.validation import validate_agent_actor_compatibility
        validate_agent_actor_compatibility(actor_cls, mode="single")

        return await self._system.spawn(actor_cls, name, **kwargs)
    
    async def tell(
        self,
        ref: "ActorRef",
        message: Any,
    ) -> None:
        """发送消息"""
        result = ref._tell(message)
        if asyncio.iscoroutine(result):
            await result
    
    async def ask(
        self,
        ref: "ActorRef",
        message: Any,
        timeout: float,
    ) -> Any:
        """发送消息并等待回复"""
        return await ref._ask(message, timeout=timeout)
    
    async def spawn_child(
        self,
        parent_ref: "ActorRef",
        actor_cls: type["Actor"],
        name: str,
        **kwargs,
    ) -> "ActorRef":
        """创建子 Actor"""
        # 获取 parent 的 cell
        cell = parent_ref._cell
        return await cell.spawn_child(actor_cls, name, **kwargs)
    
    async def stop(self, ref: "ActorRef") -> None:
        """停止 Actor"""
        ref.stop()
    
    async def shutdown(self) -> None:
        """关闭系统"""
        await self._system.shutdown()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "mode": self.config.mode,
            "name": self.name,
            "actor_count": len(self._system._root_cells),
        }
