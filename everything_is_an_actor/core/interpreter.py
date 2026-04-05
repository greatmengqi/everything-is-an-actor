"""Interpreters for Actor Free workflows.

Provides:
- LiveInterpreter: executes workflows against a real ActorSystem
- MockInterpreter: captures/controls workflows for testing
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeVar

from everything_is_an_actor.core.frees import Free, Pure, Suspend, FlatMap

if TYPE_CHECKING:
    from everything_is_an_actor.core.actor_f import ActorF
    from everything_is_an_actor.core.system import ActorSystem

A = TypeVar("A")


class LiveInterpreter:
    """Interprets ActorF Free programs against a real ActorSystem.

    Each suspended operation is executed as it is encountered, producing
    real side effects (actor spawning, message passing, etc.).
    """

    def __init__(self, system: "ActorSystem") -> None:
        self._system = system

    async def __call__(self, op: "ActorF") -> Free[ActorF, Any]:
        """Interpret a single ActorF operation, returning a Free to be chained."""
        from everything_is_an_actor.core.actor_f import AskF as AskOp, SpawnF as SpawnOp
        from everything_is_an_actor.core.actor_f import TellF as TellOp, StopF as StopOp, GetRefF as GetRefOp

        if isinstance(op, SpawnOp):
            ref = await self._system.spawn(op.actor_cls, op.name)
            return Pure(ref)
        elif isinstance(op, TellOp):
            await op.ref._tell(op.msg)
            return Pure(None)
        elif isinstance(op, AskOp):
            result = await op.ref._ask(op.msg)
            return Pure(result)
        elif isinstance(op, StopOp):
            op.ref.stop()
            return Pure(None)
        elif isinstance(op, GetRefOp):
            # Not meaningful in a top-level Free; raise to avoid silent bugs
            raise RuntimeError("get_ref() requires a calling ActorContext — use inside an actor")
        else:
            raise RuntimeError(f"Unknown ActorF operation: {type(op).__name__}")


# Cached interpreter instance (avoid repeated allocation)
_cached_interpreter: "LiveInterpreter | None" = None


async def run_free(system: "ActorSystem", free: Free[ActorF, A]) -> A:
    """Run a Free[A] workflow against a real ActorSystem.

    Fast path: simple Suspend[T] operations are executed directly without trampoline.
    """
    # Fast path: simple single operation (common case: tell, ask)
    if isinstance(free, Suspend):
        op = free.thunk
        from everything_is_an_actor.core.actor_f import AskF as AskOp, SpawnF as SpawnOp
        from everything_is_an_actor.core.actor_f import TellF as TellOp, StopF as StopOp

        if isinstance(op, TellOp):
            await op.ref._tell(op.msg)
            return None  # type: ignore[return-value]
        elif isinstance(op, AskOp):
            return await op.ref._ask(op.msg)
        elif isinstance(op, SpawnOp):
            ref = await system.spawn(op.actor_cls, op.name)
            return ref  # type: ignore[return-value]
        elif isinstance(op, StopOp):
            op.ref.stop()
            return None  # type: ignore[return-value]
        # Fall through to trampoline for other ops

    # Slow path: full trampoline for complex workflows
    global _cached_interpreter
    if _cached_interpreter is None or _cached_interpreter._system is not system:
        _cached_interpreter = LiveInterpreter(system)
    return await _run_trampoline(free, _cached_interpreter)


async def _run_trampoline(free: Free[ActorF, Any], interpreter: LiveInterpreter) -> Any:
    """Trampolining interpreter that handles Free chains iteratively.

    Walks the Free structure, interpreting Suspend operations as it encounters them.
    """
    # Stack to track the chain - avoid deep recursion
    stack: list[Callable[[Any], Free[ActorF, Any]]] = []

    current: Free[ActorF, Any] = free

    while True:
        if isinstance(current, Pure):
            # Hit a pure value - unwind the stack
            if not stack:
                return current.value
            # Pop continuation and apply
            f = stack.pop()
            current = f(current.value)
        elif isinstance(current, Suspend):
            # Execute the suspended operation, then apply any continuations
            result = await interpreter(current.thunk)
            if not stack:
                # No more continuations, return directly
                if isinstance(result, Pure):
                    return result.value
                current = result
            else:
                # Apply continuations to the result
                f = stack.pop()
                current = result.flatMap(f)
        elif isinstance(current, FlatMap):
            # Push the continuation and recurse into the thunk
            stack.append(current.f)
            current = current.thunk
        else:
            raise RuntimeError(f"Unreachable Free state: {type(current)}")


# ---------------------------------------------------------------------------
# Mock Interpreter (for testing without real actors)
# ---------------------------------------------------------------------------


class MockRef:
    """A mock ActorRef that records operations instead of executing them."""

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.path = f"/mock/{name}"
        self._sent: list[Any] = []
        self._replies: dict[Any, Any] = {}
        self._stopped = False

    def _tell(self, msg: Any) -> None:
        self._sent.append(msg)

    def _ask(self, msg: Any, *, timeout: float = 5.0) -> Any:
        """Synchronous ask for MockRef (no real async needed)."""
        self._sent.append(msg)
        if msg in self._replies:
            return self._replies[msg]
        return None

    def stop(self) -> None:
        self._stopped = True

    def set_reply(self, msg: Any, result: Any) -> None:
        self._replies[msg] = result

    @property
    def is_alive(self) -> bool:
        return not self._stopped


class MockSystem:
    """A mock ActorSystem that provides mock refs instead of real actors.

    Use with MockInterpreter to test workflows without spawning real actors.
    """

    def __init__(self) -> None:
        self._refs: dict[str, MockRef] = {}

    def get_ref(self, name: str) -> MockRef:
        if name not in self._refs:
            self._refs[name] = MockRef(name)
        return self._refs[name]

    async def spawn(self, actor_cls: type, name: str) -> MockRef:
        ref = MockRef(name)
        self._refs[name] = ref
        return ref


class MockInterpreterSync:
    """Synchronous version of MockInterpreter for use with run_free_mock_sync."""

    def __init__(self, system: MockSystem) -> None:
        self._system = system

    def __call__(self, op: "ActorF") -> Free[ActorF, Any]:
        """Interpret a single ActorF operation synchronously."""
        from everything_is_an_actor.core.actor_f import AskF as AskOp, SpawnF as SpawnOp
        from everything_is_an_actor.core.actor_f import TellF as TellOp, StopF as StopOp, GetRefF as GetRefOp

        if isinstance(op, SpawnOp):
            ref = self._system.get_ref(op.name)
            return Pure(ref)
        elif isinstance(op, TellOp):
            op.ref._tell(op.msg)
            return Pure(None)
        elif isinstance(op, AskOp):
            # MockRef._ask is synchronous in this interpreter
            result = op.ref._ask(op.msg)
            return Pure(result)
        elif isinstance(op, StopOp):
            op.ref.stop()
            return Pure(None)
        elif isinstance(op, GetRefOp):
            raise RuntimeError("get_ref() requires a calling ActorContext")
        else:
            raise RuntimeError(f"Unknown ActorF operation: {type(op).__name__}")


class MockInterpreter:
    """Interprets ActorF Free programs using a MockSystem (no real actors).

    Supports both MockRef (sync) and real ActorRef (async) for testing.
    """

    def __init__(self, system: MockSystem) -> None:
        self._system = system

    async def __call__(self, op: "ActorF") -> Free[ActorF, Any]:
        """Interpret a single ActorF operation asynchronously."""
        from everything_is_an_actor.core.actor_f import AskF as AskOp, SpawnF as SpawnOp
        from everything_is_an_actor.core.actor_f import TellF as TellOp, StopF as StopOp, GetRefF as GetRefOp

        if isinstance(op, SpawnOp):
            ref = self._system.get_ref(op.name)
            return Pure(ref)
        elif isinstance(op, TellOp):
            op.ref._tell(op.msg)
            return Pure(None)
        elif isinstance(op, AskOp):
            # Support both MockRef (sync) and ActorRef (async)
            result = await op.ref._ask(op.msg)
            return Pure(result)
        elif isinstance(op, StopOp):
            op.ref.stop()
            return Pure(None)
        elif isinstance(op, GetRefOp):
            raise RuntimeError("get_ref() requires a calling ActorContext")
        else:
            raise RuntimeError(f"Unknown ActorF operation: {type(op).__name__}")


async def run_free_mock(system: MockSystem, free: Free[ActorF, A]) -> A:
    """Run a Free[A] workflow against a MockSystem (async)."""
    interpreter = MockInterpreter(system)
    return await _run_trampoline(free, interpreter)


def run_free_mock_sync(system: MockSystem, free: Free[ActorF, A]) -> A:
    """Run a Free[A] workflow against a MockSystem (synchronous version)."""
    interpreter = MockInterpreterSync(system)
    return _run_trampoline_sync(free, interpreter)


def _run_trampoline_sync(free: Free[ActorF, Any], interpreter: MockInterpreter) -> Any:
    """Synchronous trampolining interpreter for MockSystem.

    Uses explicit stack to handle deeply nested flatMap chains.
    """
    stack: list[Callable[[Any], Free[ActorF, Any]]] = []
    current: Free[ActorF, Any] = free

    while True:
        if isinstance(current, Pure):
            # Hit a pure value - unwind the stack
            if not stack:
                return current.value
            # Pop continuation and apply
            f = stack.pop()
            current = f(current.value)
        elif isinstance(current, Suspend):
            # Execute the suspended operation, then apply any continuations
            result = interpreter(current.thunk)
            if not stack:
                # No more continuations, return directly
                if isinstance(result, Pure):
                    return result.value
                current = result
            else:
                # Apply continuations to the result
                f = stack.pop()
                current = result.flatMap(f)
        elif isinstance(current, FlatMap):
            # Push the continuation and recurse into the thunk
            stack.append(current.f)
            current = current.thunk
        else:
            raise RuntimeError(f"Unreachable Free state: {type(current)}")


__all__ = [
    "LiveInterpreter",
    "MockInterpreter",
    "MockInterpreterSync",
    "MockSystem",
    "MockRef",
    "run_free",
    "run_free_mock",
    "run_free_mock_sync",
]
