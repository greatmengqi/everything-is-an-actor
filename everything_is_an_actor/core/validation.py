"""Actor validation utilities.

This module is part of ``core/`` and must not depend on ``agents/``, ``flow/``,
or ``integrations/``. Subclass-specific validation (e.g., ``AgentActor``
handler shape) belongs on the subclass itself via the
``Actor.__validate_spawn_class__`` hook — not here.
"""

from __future__ import annotations

import inspect
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
