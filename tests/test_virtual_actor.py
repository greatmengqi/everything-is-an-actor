"""Tests for VirtualActorRegistry — on-demand activation and idle deactivation."""

import asyncio

import pytest

from everything_is_an_actor import Actor, ActorSystem, AfterIdle
from everything_is_an_actor.virtual import VirtualActorRegistry

# Shared "database" for persistence tests
_fake_db: dict[str, list] = {}


class EchoAgent(Actor):
    """Simple agent that echoes messages. Deactivates after 0.5s idle."""

    def stop_policy(self):
        return AfterIdle(seconds=0.5)

    async def on_started(self):
        self._call_count = 0

    async def on_receive(self, message):
        self._call_count += 1
        return f"echo:{message}:{self._call_count}"


class StatefulAgent(Actor):
    """Agent that tracks state across messages within a single activation."""

    def stop_policy(self):
        return AfterIdle(seconds=0.5)

    async def on_started(self):
        self._history = []

    async def on_receive(self, message):
        self._history.append(message)
        return list(self._history)


class PersistentAgent(Actor):
    """Agent that loads/saves state from a fake DB on activate/deactivate."""

    def stop_policy(self):
        return AfterIdle(seconds=0.5)

    async def on_started(self):
        actor_id = self.context.self_ref.name
        self._history = list(_fake_db.get(actor_id, []))

    async def on_stopped(self):
        actor_id = self.context.self_ref.name
        _fake_db[actor_id] = list(self._history)

    async def on_receive(self, message):
        self._history.append(message)
        return list(self._history)


class CrashOnceAgent(Actor):
    """Agent that crashes on the first message, succeeds on subsequent ones."""

    def stop_policy(self):
        return AfterIdle(seconds=0.5)

    async def on_started(self):
        self._started_count = 0

    async def on_receive(self, message):
        if message == "crash":
            raise RuntimeError("intentional crash")
        return f"ok:{message}"


class SlowActivateAgent(Actor):
    """Agent with slow on_started to test concurrent activation."""

    def stop_policy(self):
        return AfterIdle(seconds=0.5)

    async def on_started(self):
        await asyncio.sleep(0.3)  # simulate slow state loading
        self._ready = True

    async def on_receive(self, message):
        return f"ready:{message}"


class SlowStopAgent(Actor):
    """Agent whose on_stopped takes a long time (but completes)."""

    stopped_called = False
    stopped_completed = False

    def stop_policy(self):
        return AfterIdle(seconds=0.3)

    async def on_receive(self, message):
        return f"ok:{message}"

    async def on_stopped(self):
        SlowStopAgent.stopped_called = True
        await asyncio.sleep(0.5)  # slow cleanup
        SlowStopAgent.stopped_completed = True


class FailingStopAgent(Actor):
    """Agent whose on_stopped raises an exception."""

    stop_attempts = 0

    def stop_policy(self):
        return AfterIdle(seconds=0.3)

    async def on_receive(self, message):
        return f"ok:{message}"

    async def on_stopped(self):
        FailingStopAgent.stop_attempts += 1
        raise RuntimeError("on_stopped failed")


@pytest.mark.asyncio
async def test_basic_activation():
    """Virtual actor is activated on first message."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    assert registry.active_count == 0
    reply = await registry.ask(EchoAgent, "s1", "hello")
    assert reply == "echo:hello:1"
    assert registry.active_count == 1
    assert registry.is_active(EchoAgent, "s1")

    await system.shutdown()


@pytest.mark.asyncio
async def test_multiple_sessions():
    """Different actor IDs activate different instances."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    r1 = await registry.ask(EchoAgent, "s1", "a")
    r2 = await registry.ask(EchoAgent, "s2", "b")
    assert r1 == "echo:a:1"
    assert r2 == "echo:b:1"
    assert registry.active_count == 2

    await system.shutdown()


@pytest.mark.asyncio
async def test_same_session_reuses_actor():
    """Multiple messages to the same ID go to the same actor instance."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    r1 = await registry.ask(StatefulAgent, "s1", "a")
    r2 = await registry.ask(StatefulAgent, "s1", "b")
    r3 = await registry.ask(StatefulAgent, "s1", "c")
    assert r1 == ["a"]
    assert r2 == ["a", "b"]
    assert r3 == ["a", "b", "c"]

    await system.shutdown()


@pytest.mark.asyncio
async def test_idle_deactivation():
    """Actor deactivates after idle timeout."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(EchoAgent, "s1", "hello")
    assert registry.is_active(EchoAgent, "s1")

    # Wait for idle deactivation (0.5s timeout + buffer)
    await asyncio.sleep(1.0)
    assert not registry.is_active(EchoAgent, "s1")
    assert registry.active_count == 0

    await system.shutdown()


@pytest.mark.asyncio
async def test_reactivation_after_idle():
    """Actor can be reactivated after idle deactivation."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # First activation
    r1 = await registry.ask(EchoAgent, "s1", "hello")
    assert r1 == "echo:hello:1"

    # Wait for deactivation
    await asyncio.sleep(1.0)
    assert not registry.is_active(EchoAgent, "s1")

    # Reactivation — new instance, counter resets
    r2 = await registry.ask(EchoAgent, "s1", "world")
    assert r2 == "echo:world:1"
    assert registry.is_active(EchoAgent, "s1")

    await system.shutdown()


@pytest.mark.asyncio
async def test_manual_deactivation():
    """Actor can be manually deactivated."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(EchoAgent, "s1", "hello")
    assert registry.is_active(EchoAgent, "s1")

    await registry.deactivate(EchoAgent, "s1")
    assert not registry.is_active(EchoAgent, "s1")

    await system.shutdown()


@pytest.mark.asyncio
async def test_deactivate_all():
    """All actors can be deactivated at once."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(EchoAgent, "s1", "a")
    await registry.ask(EchoAgent, "s2", "b")
    await registry.ask(EchoAgent, "s3", "c")
    assert registry.active_count == 3

    await registry.deactivate_all()
    await asyncio.sleep(0.1)  # let watcher tasks clean up
    assert registry.active_count == 0

    await system.shutdown()


@pytest.mark.asyncio
async def test_tell_activates():
    """tell() also activates the actor."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.tell(EchoAgent, "s1", "hello")
    assert registry.is_active(EchoAgent, "s1")

    await system.shutdown()


@pytest.mark.asyncio
async def test_concurrent_activation():
    """Concurrent messages to the same ID only activate once."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # Send 10 concurrent asks to the same actor ID
    results = await asyncio.gather(
        *[registry.ask(StatefulAgent, "s1", f"msg{i}") for i in range(10)]
    )

    # All should succeed, actor activated only once
    assert registry.active_count == 1
    # The last result should contain all 10 messages (order may vary due to concurrency)
    lengths = [len(r) for r in results]
    assert max(lengths) == 10  # Final result has all messages

    await system.shutdown()


@pytest.mark.asyncio
async def test_on_deactivate_callback():
    """Deactivation callback is called when actor stops."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    deactivated = []
    registry.on_deactivate(lambda key: deactivated.append(key))

    await registry.ask(EchoAgent, "s1", "hello")
    await registry.deactivate(EchoAgent, "s1")

    # Give the watcher task time to run
    await asyncio.sleep(0.1)
    assert "EchoAgent:s1" in deactivated

    await system.shutdown()


@pytest.mark.asyncio
async def test_different_actor_types_same_id():
    """Different actor types with the same ID are separate virtual actors."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    r1 = await registry.ask(EchoAgent, "s1", "hello")
    r2 = await registry.ask(StatefulAgent, "s1", "world")

    assert r1 == "echo:hello:1"
    assert r2 == ["world"]
    assert registry.active_count == 2

    await system.shutdown()


# ── State persistence across activations ──────────────────────────────


@pytest.mark.asyncio
async def test_state_persists_across_activations():
    """State saved in on_stopped is available in next on_started."""
    _fake_db.clear()
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # First activation: build up state
    r1 = await registry.ask(PersistentAgent, "s1", "a")
    r2 = await registry.ask(PersistentAgent, "s1", "b")
    assert r1 == ["a"]
    assert r2 == ["a", "b"]

    # Wait for idle deactivation → on_stopped saves to _fake_db
    await asyncio.sleep(1.0)
    assert not registry.is_active(PersistentAgent, "s1")
    assert _fake_db.get("v:s1") == ["a", "b"]

    # Second activation: state restored from _fake_db
    r3 = await registry.ask(PersistentAgent, "s1", "c")
    assert r3 == ["a", "b", "c"]

    await system.shutdown()
    _fake_db.clear()


@pytest.mark.asyncio
async def test_state_independent_per_id():
    """Different actor IDs have independent state."""
    _fake_db.clear()
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(PersistentAgent, "s1", "x")
    await registry.ask(PersistentAgent, "s2", "y")

    r1 = await registry.ask(PersistentAgent, "s1", "z")
    r2 = await registry.ask(PersistentAgent, "s2", "w")

    assert r1 == ["x", "z"]
    assert r2 == ["y", "w"]

    await system.shutdown()
    _fake_db.clear()


# ── Concurrent activation with slow startup ───────────────────────────


@pytest.mark.asyncio
async def test_slow_activation_concurrent_asks():
    """Concurrent asks during slow activation all wait and succeed."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # 5 concurrent asks while on_started takes 0.3s
    results = await asyncio.gather(
        *[registry.ask(SlowActivateAgent, "s1", f"msg{i}") for i in range(5)]
    )

    assert registry.active_count == 1
    assert all(r.startswith("ready:") for r in results)

    await system.shutdown()


# ── High density ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_high_density_activation():
    """Many virtual actors can be activated concurrently."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # Activate 100 agents concurrently
    results = await asyncio.gather(
        *[registry.ask(EchoAgent, f"s{i}", f"msg{i}") for i in range(100)]
    )

    assert registry.active_count == 100
    assert all(r.startswith("echo:") for r in results)

    # Deactivate all
    await registry.deactivate_all()
    await asyncio.sleep(0.2)
    assert registry.active_count == 0

    await system.shutdown()


# ── Deactivation during message processing ────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_then_reactivate_immediately():
    """Deactivate and immediately send a new message → clean reactivation."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    r1 = await registry.ask(EchoAgent, "s1", "first")
    assert r1 == "echo:first:1"

    await registry.deactivate(EchoAgent, "s1")
    await asyncio.sleep(0.1)  # let cleanup finish

    # Immediately reactivate
    r2 = await registry.ask(EchoAgent, "s1", "second")
    assert r2 == "echo:second:1"  # fresh instance, counter reset

    await system.shutdown()


# ── active_ids property ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_ids_tracking():
    """active_ids reflects current state accurately."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(EchoAgent, "s1", "a")
    await registry.ask(EchoAgent, "s2", "b")
    ids = registry.active_ids
    assert "EchoAgent:s1" in ids
    assert "EchoAgent:s2" in ids

    await registry.deactivate(EchoAgent, "s1")
    await asyncio.sleep(0.1)
    ids = registry.active_ids
    assert "EchoAgent:s1" not in ids
    assert "EchoAgent:s2" in ids

    await system.shutdown()


# ── Rapid activation/deactivation cycles ──────────────────────────────


@pytest.mark.asyncio
async def test_rapid_reactivation_cycles():
    """Actor can go through multiple activate/deactivate cycles."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    for cycle in range(5):
        r = await registry.ask(EchoAgent, "s1", f"cycle{cycle}")
        assert r == f"echo:cycle{cycle}:1"  # fresh instance each cycle
        await registry.deactivate(EchoAgent, "s1")
        await asyncio.sleep(0.1)
        assert not registry.is_active(EchoAgent, "s1")

    await system.shutdown()


# ── is_active after system shutdown ───────────────────────────────────


@pytest.mark.asyncio
async def test_is_active_false_for_nonexistent():
    """is_active returns False for never-activated actors."""
    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    assert not registry.is_active(EchoAgent, "never_existed")
    assert registry.active_count == 0

    await system.shutdown()


# ── Lifecycle guarantees ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_stopped_completes_on_idle_deactivation():
    """on_stopped runs fully when actor deactivates due to idle timeout."""
    SlowStopAgent.stopped_called = False
    SlowStopAgent.stopped_completed = False

    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(SlowStopAgent, "s1", "hello")
    assert registry.is_active(SlowStopAgent, "s1")

    # Wait for idle deactivation (0.3s) + on_stopped (0.5s) + buffer
    await asyncio.sleep(1.5)
    assert SlowStopAgent.stopped_called
    assert SlowStopAgent.stopped_completed
    assert not registry.is_active(SlowStopAgent, "s1")

    await system.shutdown()


@pytest.mark.asyncio
async def test_on_stopped_completes_on_manual_deactivation():
    """on_stopped runs fully on manual deactivation."""
    SlowStopAgent.stopped_called = False
    SlowStopAgent.stopped_completed = False

    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(SlowStopAgent, "s1", "hello")
    await registry.deactivate(SlowStopAgent, "s1")

    assert SlowStopAgent.stopped_called
    assert SlowStopAgent.stopped_completed

    await system.shutdown()


@pytest.mark.asyncio
async def test_on_stopped_completes_on_system_shutdown():
    """on_stopped runs fully even during system shutdown."""
    SlowStopAgent.stopped_called = False
    SlowStopAgent.stopped_completed = False

    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(SlowStopAgent, "s1", "hello")

    # Shutdown with enough timeout for on_stopped to complete
    await system.shutdown(timeout=5.0)

    assert SlowStopAgent.stopped_called
    assert SlowStopAgent.stopped_completed


@pytest.mark.asyncio
async def test_on_stopped_failure_does_not_block_deactivation():
    """on_stopped exception doesn't prevent actor from being cleaned up."""
    FailingStopAgent.stop_attempts = 0

    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    await registry.ask(FailingStopAgent, "s1", "hello")
    assert registry.is_active(FailingStopAgent, "s1")

    # Wait for idle deactivation
    await asyncio.sleep(1.0)

    assert FailingStopAgent.stop_attempts >= 1  # on_stopped was called
    assert not registry.is_active(FailingStopAgent, "s1")  # actor still cleaned up

    await system.shutdown()


@pytest.mark.asyncio
async def test_reactivation_after_failed_on_stopped():
    """Actor can be reactivated even if previous on_stopped failed."""
    FailingStopAgent.stop_attempts = 0

    system = ActorSystem("test")
    registry = VirtualActorRegistry(system)

    # First activation
    r1 = await registry.ask(FailingStopAgent, "s1", "first")
    assert r1 == "ok:first"

    # Wait for deactivation (on_stopped will fail)
    await asyncio.sleep(1.0)
    assert not registry.is_active(FailingStopAgent, "s1")

    # Reactivation should still work
    r2 = await registry.ask(FailingStopAgent, "s1", "second")
    assert r2 == "ok:second"

    await system.shutdown()
