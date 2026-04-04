"""Tests for Free Monad layer (frees.py, actor_f.py, interpreter.py)."""

import pytest

from everything_is_an_actor import Actor, ActorSystem, ActorRef
from everything_is_an_actor.actor_f import ask, spawn, stop, tell, tell_direct
from everything_is_an_actor.frees import FlatMap, Free, Pure, Suspend
from everything_is_an_actor.interpreter import MockInterpreter, MockInterpreterSync, MockRef, MockSystem, run_free, run_free_mock, run_free_mock_sync


class EchoActor(Actor):
    async def on_receive(self, message):
        return f"echo: {message}"


class IncrementActor(Actor):
    async def on_started(self):
        self.count = 0

    async def on_receive(self, message):
        if message == "inc":
            self.count += 1
            return self.count
        if message == "get":
            return self.count
        return self.count


# ---------------------------------------------------------------------------
# Free Monad base types tests
# ---------------------------------------------------------------------------


def test_pure_lift():
    """Pure wraps a value without wrapping it in Suspend."""
    p = Pure(42)
    assert isinstance(p, Pure)
    assert p.value == 42


def test_suspend_wraps_operation():
    """Suspend wraps a base functor operation."""
    s = Suspend({"type": "test", "value": 1})
    assert isinstance(s, Suspend)
    assert isinstance(s.thunk, dict)


def test_free_map():
    """Free.map applies function to pure value."""
    p = Pure(5)
    result = p.map(lambda x: x * 2)
    # map uses flatMap internally
    assert isinstance(result, FlatMap)


def test_free_flatMap():
    """Free.flatMap chains computations."""
    p = Pure(5)
    result = p.flatMap(lambda x: Pure(x * 2))
    assert isinstance(result, FlatMap)


# ---------------------------------------------------------------------------
# MockSystem and MockInterpreter tests
# ---------------------------------------------------------------------------


def test_mock_system_get_ref():
    """MockSystem returns same ref for same name."""
    system = MockSystem()
    ref1 = system.get_ref("test")
    ref2 = system.get_ref("test")
    assert ref1 is ref2


def test_mock_ref_set_reply():
    """MockRef can have predefined replies."""
    ref = MockRef("test")
    ref.set_reply("hello", "world")
    result = ref._ask("hello")
    assert result == "world"


def test_mock_ref_ask_returns_none_for_unknown():
    """MockRef.ask returns None for unset messages."""
    ref = MockRef("test")
    result = ref._ask("unknown")
    assert result is None


def test_mock_ref_tell_records():
    """MockRef.tell records sent messages."""
    ref = MockRef("test")
    ref._tell("msg1")
    ref._tell("msg2")
    assert ref._sent == ["msg1", "msg2"]


def test_run_free_mock_simple():
    """run_free_mock executes simple ask workflow."""
    system = MockSystem()
    ref = system.get_ref("echo")
    ref.set_reply("ping", "pong")

    result = run_free_mock_sync(system, ask(ref, "ping"))
    assert result == "pong"


def test_run_free_mock_tell():
    """run_free_mock executes tell workflow."""
    system = MockSystem()
    ref = system.get_ref("counter")

    run_free_mock_sync(system, tell(ref, "inc"))
    assert ref._sent == ["inc"]


# ---------------------------------------------------------------------------
# LiveInterpreter (real actor) tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_free_ask():
    """run_free executes ask against real actor system."""
    system = ActorSystem()
    try:
        ref = await system.spawn(EchoActor, "echo")
        result = await run_free(system, ask(ref, "hello"))
        assert result == "echo: hello"
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_run_free_spawn_and_ask():
    """run_free can spawn actors and then use them."""
    system = ActorSystem()
    try:
        # Spawn via Free
        ref = await run_free(system, spawn("echo2", EchoActor))
        result = await run_free(system, ask(ref, "world"))
        assert result == "echo: world"
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_run_free_tell():
    """run_free executes tell against real actor system."""
    system = ActorSystem()
    try:
        ref = await system.spawn(IncrementActor, "counter")
        await run_free(system, tell(ref, "inc"))
        await run_free(system, tell(ref, "inc"))
        result = await run_free(system, ask(ref, "get"))
        assert result == 2
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_run_free_composition():
    """run_free executes flatMap chains (composition)."""
    system = ActorSystem()
    try:
        ref = await system.spawn(IncrementActor, "counter")

        def workflow():
            return tell(ref, "inc").flatMap(lambda _: tell(ref, "inc")).flatMap(lambda _: ask(ref, "get"))

        result = await run_free(system, workflow())
        assert result == 2
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_tell_direct_performance():
    """tell_direct bypasses Free monad for high throughput."""
    system = ActorSystem()
    try:
        ref = await system.spawn(IncrementActor, "counter", mailbox_size=10000)

        # Send many messages via tell_direct
        n = 1000
        for _ in range(n):
            await tell_direct(ref, "inc")

        # Verify all were processed
        result = await system.ask(ref, "get", timeout=10.0)
        assert result == n
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_run_free_stops_actor():
    """run_free executes stop against real actor system."""
    system = ActorSystem()
    ref = await system.spawn(EchoActor, "to_stop")
    assert ref.is_alive

    await run_free(system, stop(ref))

    # Actor may not be immediately stopped, give it a moment
    import asyncio
    await asyncio.sleep(0.1)
    assert not ref.is_alive

    await system.shutdown()


# ---------------------------------------------------------------------------
# TaskResult Monad tests
# ---------------------------------------------------------------------------


from everything_is_an_actor.agents.task import TaskResult, TaskStatus


def test_task_result_map_success():
    """TaskResult.map applies function to successful results."""
    tr = TaskResult(task_id="t1", output=5, status=TaskStatus.COMPLETED)
    result = tr.map(lambda x: x * 2)
    assert result.output == 10


def test_task_result_map_failure():
    """TaskResult.map preserves failed results (short-circuit)."""
    tr = TaskResult(task_id="t1", error="failed", status=TaskStatus.FAILED)
    result = tr.map(lambda x: x * 2)
    assert result.status == TaskStatus.FAILED
    assert result.error == "failed"


def test_task_result_flatMap_success():
    """TaskResult.flatMap chains successful results."""
    tr = TaskResult(task_id="t1", output=5, status=TaskStatus.COMPLETED)

    def f(x):
        return TaskResult(task_id="t2", output=x + 10, status=TaskStatus.COMPLETED)

    result = tr.flatMap(f)
    assert result.output == 15


def test_task_result_flatMap_failure():
    """TaskResult.flatMap short-circuits on failure."""
    tr = TaskResult(task_id="t1", error="failed", status=TaskStatus.FAILED)

    def f(x):
        return TaskResult(task_id="t2", output=x + 10, status=TaskStatus.COMPLETED)

    result = tr.flatMap(f)
    assert result.status == TaskStatus.FAILED
    assert result.error == "failed"


def test_task_result_pure():
    """TaskResult.pure creates successful result."""
    tr = TaskResult.pure(42)
    assert tr.output == 42
    assert tr.status == TaskStatus.COMPLETED
    assert tr.error is None


def test_task_result_apply():
    """TaskResult.apply applies wrapped function."""
    tr = TaskResult(task_id="t1", output=5, status=TaskStatus.COMPLETED)
    result = tr.apply(lambda x: x * 2)
    assert result.output == 10


# ---------------------------------------------------------------------------
# ActorRef.free_xxx API tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ref_free_ask():
    """ActorRef.free_ask returns a Free that can be executed."""
    system = ActorSystem()
    try:
        ref = await system.spawn(EchoActor, "echo")
        result = await run_free(system, ref.free_ask("hello"))
        assert result == "echo: hello"
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_ref_free_tell():
    """ActorRef.free_tell returns a Free that can be executed."""
    system = ActorSystem()
    try:
        ref = await system.spawn(IncrementActor, "counter")
        await run_free(system, ref.free_tell("inc"))
        await run_free(system, ref.free_tell("inc"))
        result = await run_free(system, ref.free_ask("get"))
        assert result == 2
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_ref_free_stop():
    """ActorRef.free_stop returns a Free that can be executed."""
    system = ActorSystem()
    ref = await system.spawn(EchoActor, "to_stop")
    assert ref.is_alive

    await run_free(system, ref.free_stop())

    import asyncio
    await asyncio.sleep(0.1)
    assert not ref.is_alive

    await system.shutdown()


@pytest.mark.anyio
async def test_ref_free_composition():
    """ActorRef.free_xxx supports flatMap chaining."""
    system = ActorSystem()
    try:
        ref = await system.spawn(IncrementActor, "counter")

        def workflow():
            return ref.free_tell("inc").flatMap(lambda _: ref.free_tell("inc")).flatMap(lambda _: ref.free_ask("get"))

        result = await run_free(system, workflow())
        assert result == 2
    finally:
        await system.shutdown()


def test_ref_free_ask_returns_suspend():
    """ActorRef.free_ask returns a Suspend wrapping AskF."""
    from everything_is_an_actor.ref import ActorRef

    class MockCell:
        name = "mock"
        path = "/mock"
        stopped = False

    ref = ActorRef.__new__(ActorRef)
    ref._cell = MockCell()

    op = ref.free_ask("hello")
    assert isinstance(op, Suspend)


def test_ref_free_tell_returns_suspend():
    """ActorRef.free_tell returns a Suspend wrapping TellF."""
    from everything_is_an_actor.ref import ActorRef

    class MockCell:
        name = "mock"
        path = "/mock"
        stopped = False

    ref = ActorRef.__new__(ActorRef)
    ref._cell = MockCell()

    op = ref.free_tell("hello")
    assert isinstance(op, Suspend)


def test_ref_free_stop_returns_suspend():
    """ActorRef.free_stop returns a Suspend wrapping StopF."""
    from everything_is_an_actor.ref import ActorRef

    class MockCell:
        name = "mock"
        path = "/mock"
        stopped = False

    ref = ActorRef.__new__(ActorRef)
    ref._cell = MockCell()

    op = ref.free_stop()
    assert isinstance(op, Suspend)
