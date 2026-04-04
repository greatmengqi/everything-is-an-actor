"""Retry + idempotency helpers for Actor ask/tell patterns.

This module provides:
- Message envelope carrying retry/idempotency metadata
- In-memory idempotency store (process-local)
- ask_with_retry helper (bounded retries + exponential backoff + jitter)

Design notes:
- Keep transport-agnostic; works with current in-memory mailbox.
- Business handlers must opt in by using ``IdempotentActorMixin`` and
  wrapping logic with ``handle_idempotent``.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RetryEnvelope:
    """Metadata wrapper for idempotent/retriable messages."""

    payload: Any
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: str | None = None
    attempt: int = 1
    max_attempts: int = 1
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @classmethod
    def wrap(
        cls,
        payload: Any,
        *,
        idempotency_key: str | None = None,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> "RetryEnvelope":
        return cls(
            payload=payload,
            idempotency_key=idempotency_key,
            attempt=attempt,
            max_attempts=max_attempts,
        )


class IdempotencyStore:
    """Process-local idempotency result store."""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def has(self, key: str) -> bool:
        return key in self._results

    def get(self, key: str) -> Any:
        return self._results[key]

    def set(self, key: str, value: Any) -> None:
        self._results[key] = value


class IdempotentActorMixin:
    """Mixin adding idempotent handling utility for actors.

    Usage in actor::

        class MyActor(IdempotentActorMixin, Actor):
            async def on_receive(self, message):
                return await self.handle_idempotent(message, self._handle)

            async def _handle(self, payload):
                ...
    """

    def _idempotency_store(self) -> IdempotencyStore:
        store = getattr(self, "_idem_store", None)
        if store is None:
            store = IdempotencyStore()
            setattr(self, "_idem_store", store)
        return store

    async def handle_idempotent(self, message: Any, handler):
        if not isinstance(message, RetryEnvelope):
            return await handler(message)

        key = message.idempotency_key
        if not key:
            return await handler(message.payload)

        store = self._idempotency_store()
        if store.has(key):
            return store.get(key)

        result = await handler(message.payload)
        store.set(key, result)
        return result


async def ask_with_retry(
    ref,
    payload: Any,
    *,
    timeout: float = 5.0,
    max_attempts: int = 3,
    base_backoff_s: float = 0.1,
    max_backoff_s: float = 5.0,
    jitter_ratio: float = 0.3,
    retry_exceptions: tuple[type[BaseException], ...] = (asyncio.TimeoutError,),
    idempotency_key: str | None = None,
) -> Any:
    """Ask actor with bounded retries and envelope metadata."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    key = idempotency_key or uuid.uuid4().hex
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        msg = RetryEnvelope.wrap(
            payload,
            idempotency_key=key,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        try:
            return await ref._ask(msg, timeout=timeout)
        except retry_exceptions as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break

            backoff = min(max_backoff_s, base_backoff_s * (2 ** (attempt - 1)))
            jitter = backoff * jitter_ratio * random.random()
            await asyncio.sleep(backoff + jitter)

    raise last_exc  # type: ignore[misc]  # always set: loop runs ≥1 time and sets on last iteration
