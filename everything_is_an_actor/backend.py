"""
Actor System 后端抽象

定义统一的 Backend 接口，支持多种实现：
- SingleLoopBackend: 单事件循环，最高性能
- MultiLoopBackend: 多事件循环，I/O 隔离
- MultiProcessBackend: 多进程，CPU 并行
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, TypeVar

if TYPE_CHECKING:
    from everything_is_an_actor.actor import Actor
    from everything_is_an_actor.ref import ActorRef

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


class ActorBackend(ABC):
    """
    Actor 后端抽象。

    定义了所有后端必须实现的接口。
    """

    @abstractmethod
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
        """
        pass

    @abstractmethod
    async def tell(
        self,
        ref: ActorRef,
        message: Any,
    ) -> None:
        """
        发送消息（不等待回复）。

        Args:
            ref: 目标 Actor
            message: 消息内容
        """
        pass

    @abstractmethod
    async def ask(
        self,
        ref: ActorRef,
        message: Any,
        timeout: float,
    ) -> Any:
        """
        发送消息并等待回复。

        Args:
            ref: 目标 Actor
            message: 消息内容
            timeout: 超时时间

        Returns:
            回复内容
        """
        pass

    @abstractmethod
    async def spawn_child(
        self,
        parent_ref: ActorRef,
        actor_cls: type[Actor],
        name: str,
        **kwargs,
    ) -> ActorRef:
        """
        创建子 Actor。

        Args:
            parent_ref: 父 Actor
            actor_cls: Actor 类
            name: 子 Actor 名称
            **kwargs: 传递给 Actor 的参数

        Returns:
            ActorRef
        """
        pass

    @abstractmethod
    async def stop(self, ref: ActorRef) -> None:
        """停止 Actor"""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """关闭系统"""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        pass
