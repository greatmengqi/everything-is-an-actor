"""Virtual Actor Registry — on-demand activation and idle deactivation.

Virtual actors exist conceptually forever but only consume resources when active.
The registry manages their lifecycle: activate on first message, deactivate on idle.

Usage::

    from everything_is_an_actor import ActorSystem, AfterIdle, Actor
    from everything_is_an_actor.core.virtual import VirtualActorRegistry

    class ChatAgent(Actor):
        def stop_policy(self):
            return AfterIdle(seconds=300)  # deactivate after 5 min idle

        async def on_started(self):        # = on_activate
            self.history = await load_from_db(self.context.self_ref.name)

        async def on_stopped(self):        # = on_deactivate
            await save_to_db(self.context.self_ref.name, self.history)

        async def on_receive(self, message):
            return f"reply to {message}"

    async def main():
        system = ActorSystem("app")
        registry = VirtualActorRegistry(system)

        # Actor is activated on first message, deactivated on idle
        reply = await registry.ask(ChatAgent, "session_123", "hello")
        await registry.tell(ChatAgent, "session_456", "world")

        await system.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from everything_is_an_actor.core.actor import Actor
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.core.ref import ActorRef

logger = logging.getLogger(__name__)

MsgT2 = TypeVar("MsgT2")
RetT2 = TypeVar("RetT2")


class RegistryStore:
    """Pluggable backend for virtual actor registry.

    Default is in-memory. Override for persistence (Redis, DB, etc.)::

        class RedisRegistryStore(RegistryStore):
            async def put(self, key: str) -> None:
                await redis.sadd("virtual_actors", key)

            async def delete(self, key: str) -> None:
                await redis.srem("virtual_actors", key)

            async def list_all(self) -> list[str]:
                return list(await redis.smembers("virtual_actors"))
    """

    async def put(self, key: str) -> None:  # noqa: ARG002
        """Record that a virtual actor exists. Called on activation."""

    async def delete(self, key: str) -> None:  # noqa: ARG002
        """Remove record of a virtual actor. Called on deactivation."""

    async def list_all(self) -> list[str]:
        """List all known virtual actor keys. For recovery/broadcast."""
        return []


class _InMemoryStore(RegistryStore):
    """Default in-memory store. No persistence across restarts."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def put(self, key: str) -> None:
        self._keys.add(key)

    async def delete(self, key: str) -> None:
        self._keys.discard(key)

    async def list_all(self) -> list[str]:
        return list(self._keys)


class VirtualActorRegistry:
    """Manages virtual actors: activate on demand, deactivate on idle.

    Thread-safe for concurrent activation requests to the same actor ID.

    Args:
        system: The ActorSystem instance.
        store: Pluggable registry backend. Default is in-memory (no persistence).
            Supply a custom RegistryStore for persistence across restarts.
        default_idle_seconds: Default idle timeout for virtual actors.
    """

    def __init__(
        self,
        system: Any,
        *,
        store: RegistryStore | None = None,
        default_idle_seconds: float = 300.0,
    ) -> None:
        from everything_is_an_actor.core.system import ActorSystem

        self._system: ActorSystem = system
        self._active: dict[str, ActorRef] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._store: RegistryStore = store or _InMemoryStore()
        self._default_idle_seconds = default_idle_seconds
        self._on_deactivate_callbacks: list[Any] = []

    @property
    def active_count(self) -> int:
        """Number of currently active virtual actors."""
        return len(self._active)

    @property
    def active_ids(self) -> list[str]:
        """List of currently active virtual actor IDs."""
        return list(self._active.keys())

    async def known_ids(self) -> list[str]:
        """List all known virtual actor keys (active + previously activated).

        Requires a persistent RegistryStore. With the default in-memory store,
        this only returns currently active IDs.
        """
        return await self._store.list_all()

    def _make_key(self, actor_cls: type[Actor], actor_id: str) -> str:
        return f"{actor_cls.__name__}:{actor_id}"

    async def _ensure_active(
        self,
        actor_cls: type[Actor[MsgT2, RetT2]],
        actor_id: str,
        **spawn_kwargs: Any,
    ) -> ActorRef[MsgT2, RetT2]:
        """Ensure the virtual actor is active. Activate if needed."""
        key = self._make_key(actor_cls, actor_id)

        # Fast path: already active and alive
        ref = self._active.get(key)
        if ref is not None and ref.is_alive:
            return ref

        # Slow path: need to activate. One lock per actor ID prevents double activation.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Double-check after acquiring lock
            ref = self._active.get(key)
            if ref is not None and ref.is_alive:
                return ref

            # Clean up stale entry if actor died
            self._active.pop(key, None)

            # Remove stale root cell if it exists from a previous incarnation
            name = f"v:{actor_id}"
            if name in self._system._root_cells:
                del self._system._root_cells[name]

            ref = await self._system.spawn(actor_cls, name, **spawn_kwargs)
            self._active[key] = ref
            await self._store.put(key)

            # Monitor for deactivation in background
            ComposableFuture.eager(
                self._watch_deactivation(key, ref),
                name=f"virtual-watch:{key}",
            )

            logger.debug("Virtual actor activated: %s", key)
            return ref

    async def _watch_deactivation(self, key: str, ref: ActorRef) -> None:
        """Wait for the actor to stop, then clean up registry entry."""
        try:
            await ref.join()
        except Exception:
            pass
        await self._store.delete(key)
        self._active.pop(key, None)
        # Clean up root cell
        name = ref.name
        if name in self._system._root_cells:
            del self._system._root_cells[name]
        # Clean up lock if no longer needed
        self._locks.pop(key, None)
        logger.debug("Virtual actor deactivated: %s", key)
        for cb in self._on_deactivate_callbacks:
            try:
                cb(key)
            except Exception:
                pass

    async def tell(
        self,
        actor_cls: type[Actor],
        actor_id: str,
        message: Any,
        **spawn_kwargs: Any,
    ) -> None:
        """Send a fire-and-forget message. Activates the actor if needed."""
        ref = await self._ensure_active(actor_cls, actor_id, **spawn_kwargs)
        await self._system.tell(ref, message)

    async def ask(
        self,
        actor_cls: type[Actor],
        actor_id: str,
        message: Any,
        *,
        timeout: float = 30.0,
        **spawn_kwargs: Any,
    ) -> Any:
        """Send a message and wait for reply. Activates the actor if needed."""
        ref = await self._ensure_active(actor_cls, actor_id, **spawn_kwargs)
        return await self._system.ask(ref, message, timeout=timeout)

    async def ask_stream(
        self,
        actor_cls: type[Actor],
        actor_id: str,
        message: Any,
        *,
        timeout: float = 30.0,
        **spawn_kwargs: Any,
    ) -> Any:
        """Stream events from an agent actor. Activates the actor if needed."""
        ref = await self._ensure_active(actor_cls, actor_id, **spawn_kwargs)
        async for item in self._system.ask_stream(ref, message, timeout=timeout):
            yield item

    def is_active(self, actor_cls: type[Actor], actor_id: str) -> bool:
        """Check if a virtual actor is currently active."""
        key = self._make_key(actor_cls, actor_id)
        ref = self._active.get(key)
        return ref is not None and ref.is_alive

    async def deactivate(self, actor_cls: type[Actor], actor_id: str) -> None:
        """Manually deactivate a virtual actor."""
        key = self._make_key(actor_cls, actor_id)
        ref = self._active.get(key)
        if ref is not None and ref.is_alive:
            ref.stop()
            await ref.join()

    async def deactivate_all(self) -> None:
        """Deactivate all active virtual actors."""
        refs = [(k, r) for k, r in self._active.items() if r.is_alive]
        for _, ref in refs:
            ref.stop()
        for _, ref in refs:
            try:
                await ref.join()
            except Exception:
                pass

    def on_deactivate(self, callback: Any) -> None:
        """Register a callback for when a virtual actor deactivates.

        Callback receives the key string (e.g. "ChatAgent:session_123").
        """
        self._on_deactivate_callbacks.append(callback)
