"""Actor validation utilities."""

from __future__ import annotations

import inspect
import warnings
from typing import Optional


def find_sync_handler(
    actor_cls: type,
    *attr_names: str,
) -> Optional[tuple[type, str]]:
    """MRO traversal to find the first sync handler definition.

    Walks the MRO up to (but excluding) the Actor base class, checking each
    ``attr_name`` in ``cls.__dict__``.  Returns ``(defining_class, attr_name)``
    for the first sync (non-coroutine) match, or ``None`` if all handlers are
    async or absent.
    """
    from everything_is_an_actor.core.actor import Actor as _ActorBase

    for cls in actor_cls.__mro__:
        if cls is _ActorBase or cls is object:
            break
        for attr in attr_names:
            handler = cls.__dict__.get(attr)
            if handler is not None and callable(handler) and not inspect.iscoroutinefunction(handler):
                return cls, attr
    return None


def validate_agent_actor_compatibility(actor_cls: type, *, mode: str = "unknown") -> None:
    """Validate AgentActor handler compatibility at spawn-time.

    Enforces that AgentActor subclasses implement async execute() and do not
    define sync receive() or on_receive(). Emits deprecation warnings for
    sync handlers (with migration path) rather than hard-failing, to provide
    a smooth transition period for existing code.

    Args:
        actor_cls: The actor class to validate.
        mode: The system mode (e.g., 'single', 'multi-loop', 'unified') for error context.

    Raises:
        TypeError: If actor has incompatible sync execute() method.
    """
    from everything_is_an_actor.agents.agent_actor import AgentActor as _AgentActorBase

    # Only validate AgentActor subclasses
    if not issubclass(actor_cls, _AgentActorBase):
        return

    # Check for sync receive() or on_receive() - emit deprecation warning
    sync_handler = find_sync_handler(actor_cls, "receive", "on_receive")
    if sync_handler is not None:
        defining_cls, attr = sync_handler
        warnings.warn(
            f"DEPRECATION: Actor '{actor_cls.__name__}' has sync {attr}() "
            f"(defined in '{defining_cls.__name__}'). "
            "AgentActor now requires async execute() instead of sync receive(). "
            "This will become a hard error in a future version. "
            "Please migrate by implementing 'async def execute(self, input)' instead. "
            f"See docs/COMPATIBILITY_MATRIX.md for migration guide.",
            DeprecationWarning,
            stacklevel=3,
        )

    # Check if execute() is defined and is async - this is still a hard error
    if "execute" not in actor_cls.__dict__:
        # execute() might be inherited, which is fine
        return

    execute_method = actor_cls.__dict__.get("execute")
    if execute_method is not None and not (
        inspect.iscoroutinefunction(execute_method) or inspect.isasyncgenfunction(execute_method)
    ):
        raise TypeError(
            f"Actor '{actor_cls.__name__}' has sync execute() method; "
            "AgentActor requires async execute(). "
            "Change 'def execute(self, input)' to 'async def execute(self, input)'."
        )
