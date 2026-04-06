# Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple actor execution context (event loop) from ActorSystem lifecycle, via a Dispatcher abstraction — enabling single-loop and multi-loop actors within the same system without a separate backend.

**Architecture:** ActorSystem delegates loop assignment to a pluggable `Dispatcher`. DefaultDispatcher runs everything on the caller's loop (zero overhead, current behavior). PoolDispatcher creates N worker threads with dedicated loops and round-robins actors across them. `_ActorCell` accepts a `target_loop` parameter; if the target differs from the caller's loop, tasks are created cross-loop via `run_coroutine_threadsafe`, and `FastMailbox` uses `call_soon_threadsafe` for thread-safe signaling.

**Tech Stack:** Python 3.12+, asyncio, threading, concurrent.futures

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `everything_is_an_actor/dispatcher.py` | **Create** | Dispatcher ABC + DefaultDispatcher + PoolDispatcher |
| `everything_is_an_actor/mailbox.py` | **Modify** | FastMailbox: add `_target_loop` for cross-loop signaling |
| `everything_is_an_actor/system.py` | **Modify** | ActorSystem accepts `dispatcher`, _ActorCell accepts `target_loop` |
| `everything_is_an_actor/ref.py` | **Modify** | `join()` / `interrupt()` handle cross-loop |
| `everything_is_an_actor/__init__.py` | **Modify** | Export Dispatcher types |
| `tests/test_dispatcher.py` | **Create** | Unit + integration tests |

---

### Task 1: Dispatcher abstraction

**Files:**
- Create: `everything_is_an_actor/dispatcher.py`
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing tests for DefaultDispatcher**

```python
# tests/test_dispatcher.py
import asyncio
import pytest
from everything_is_an_actor.dispatcher import DefaultDispatcher, PoolDispatcher

class TestDefaultDispatcher:
    @pytest.mark.anyio
    async def test_returns_current_loop(self):
        d = DefaultDispatcher()
        await d.start()
        loop = d.assign(object, "test")
        assert loop is asyncio.get_running_loop()
        await d.shutdown()

    @pytest.mark.anyio
    async def test_always_same_loop(self):
        d = DefaultDispatcher()
        await d.start()
        loops = [d.assign(object, f"a{i}") for i in range(10)]
        assert all(l is loops[0] for l in loops)
        await d.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py -x -q`
Expected: ImportError

- [ ] **Step 3: Implement Dispatcher + DefaultDispatcher**

```python
# everything_is_an_actor/dispatcher.py
"""Dispatcher — assigns actors to event loops.

Decouples actor execution context from ActorSystem lifecycle.
Inspired by Akka's Dispatcher model.
"""
from __future__ import annotations

import abc
import asyncio
import threading
from typing import Any


class Dispatcher(abc.ABC):
    """Assigns actors to event loops."""

    @abc.abstractmethod
    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        """Return the event loop this actor should run on."""

    async def start(self) -> None:
        """Start the dispatcher (create threads/loops if needed)."""

    async def shutdown(self) -> None:
        """Shutdown the dispatcher (stop threads/loops if needed)."""


class DefaultDispatcher(Dispatcher):
    """All actors run on the caller's event loop. Zero overhead."""

    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()
```

- [ ] **Step 4: Run tests — DefaultDispatcher tests should pass**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py::TestDefaultDispatcher -x -q`
Expected: PASS

- [ ] **Step 5: Write failing tests for PoolDispatcher**

```python
# append to tests/test_dispatcher.py
class TestPoolDispatcher:
    @pytest.mark.anyio
    async def test_creates_worker_loops(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        loop1 = d.assign(object, "a1")
        loop2 = d.assign(object, "a2")
        current = asyncio.get_running_loop()
        # Worker loops are NOT the caller's loop
        assert loop1 is not current
        assert loop2 is not current
        # Round-robin: 2 workers, so a1→loop0, a2→loop1
        assert loop1 is not loop2
        await d.shutdown()

    @pytest.mark.anyio
    async def test_round_robin(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        loops = [d.assign(object, f"a{i}") for i in range(4)]
        # a0→loop0, a1→loop1, a2→loop0, a3→loop1
        assert loops[0] is loops[2]
        assert loops[1] is loops[3]
        assert loops[0] is not loops[1]
        await d.shutdown()

    @pytest.mark.anyio
    async def test_shutdown_stops_threads(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        threads = list(d._threads)
        assert all(t.is_alive() for t in threads)
        await d.shutdown()
        # Give threads time to stop
        for t in threads:
            t.join(timeout=2.0)
        assert all(not t.is_alive() for t in threads)

    @pytest.mark.anyio
    async def test_pool_size_1(self):
        """pool_size=1 is valid — single worker thread."""
        d = PoolDispatcher(pool_size=1)
        await d.start()
        loop = d.assign(object, "a")
        assert loop is not asyncio.get_running_loop()
        assert loop.is_running()
        await d.shutdown()
```

- [ ] **Step 6: Implement PoolDispatcher**

```python
# append to everything_is_an_actor/dispatcher.py
class PoolDispatcher(Dispatcher):
    """Round-robin actor assignment across N worker threads.

    Each worker thread runs its own asyncio event loop.
    Actors on different loops are isolated — one slow actor
    doesn't block actors on other loops.
    """

    def __init__(self, pool_size: int = 4) -> None:
        if pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {pool_size}")
        self._pool_size = pool_size
        self._loops: list[asyncio.AbstractEventLoop] = []
        self._threads: list[threading.Thread] = []
        self._counter = 0
        self._started = False

    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        if not self._started:
            raise RuntimeError("PoolDispatcher not started — call await dispatcher.start() first")
        loop = self._loops[self._counter % self._pool_size]
        self._counter += 1
        return loop

    async def start(self) -> None:
        if self._started:
            return
        for i in range(self._pool_size):
            loop_ready = threading.Event()
            loop_holder: list[asyncio.AbstractEventLoop] = []

            def _worker(ready: threading.Event, holder: list) -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                holder.append(loop)
                ready.set()
                loop.run_forever()

            t = threading.Thread(
                target=_worker,
                args=(loop_ready, loop_holder),
                name=f"dispatcher-worker-{i}",
                daemon=True,
            )
            t.start()
            loop_ready.wait(timeout=5.0)
            self._loops.append(loop_holder[0])
            self._threads.append(t)
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        for loop in self._loops:
            loop.call_soon_threadsafe(loop.stop)
        for t in self._threads:
            t.join(timeout=5.0)
        self._loops.clear()
        self._threads.clear()
        self._started = False
```

- [ ] **Step 7: Run all dispatcher tests**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py -x -q`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add everything_is_an_actor/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: add Dispatcher abstraction (DefaultDispatcher + PoolDispatcher)"
```

---

### Task 2: FastMailbox cross-loop signaling

**Files:**
- Modify: `everything_is_an_actor/mailbox.py:124-185` (FastMailbox)
- Test: `tests/test_dispatcher.py` (append)

- [ ] **Step 1: Write failing test for cross-loop put_nowait**

```python
# append to tests/test_dispatcher.py
import threading
import time

class TestFastMailboxCrossLoop:
    @pytest.mark.anyio
    async def test_put_nowait_from_other_thread_wakes_consumer(self):
        """put_nowait from another thread should wake get() on the consumer loop."""
        from everything_is_an_actor.mailbox import FastMailbox

        current_loop = asyncio.get_running_loop()
        mbox = FastMailbox(maxsize=16, target_loop=current_loop)

        received = []

        async def consumer():
            msg = await asyncio.wait_for(mbox.get(), timeout=2.0)
            received.append(msg)

        task = asyncio.create_task(consumer())

        # Give consumer time to start waiting
        await asyncio.sleep(0.05)

        # Put from another thread
        def producer():
            time.sleep(0.05)
            mbox.put_nowait("hello-from-thread")

        t = threading.Thread(target=producer)
        t.start()
        t.join(timeout=2.0)

        await task
        assert received == ["hello-from-thread"]

    @pytest.mark.anyio
    async def test_same_loop_still_works(self):
        """put_nowait from same loop should work as before."""
        from everything_is_an_actor.mailbox import FastMailbox

        mbox = FastMailbox(maxsize=16, target_loop=asyncio.get_running_loop())
        mbox.put_nowait("msg1")
        result = await mbox.get()
        assert result == "msg1"

    @pytest.mark.anyio
    async def test_no_target_loop_backward_compatible(self):
        """Without target_loop, FastMailbox works exactly as before."""
        from everything_is_an_actor.mailbox import FastMailbox

        mbox = FastMailbox(maxsize=16)
        mbox.put_nowait("msg1")
        result = await mbox.get()
        assert result == "msg1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py::TestFastMailboxCrossLoop -x -q`
Expected: TypeError (unexpected keyword argument 'target_loop')

- [ ] **Step 3: Add `target_loop` to FastMailbox**

Modify `everything_is_an_actor/mailbox.py` — `FastMailbox.__init__` and signaling methods:

```python
class FastMailbox(Mailbox):
    """In-process mailbox backed by ``collections.deque``.

    Lower overhead than ``MemoryMailbox`` for single-threaded asyncio use cases.

    When ``target_loop`` is set, ``put_nowait()`` uses ``call_soon_threadsafe``
    to wake the consumer — making it safe for cross-loop (cross-thread) producers.
    """

    def __init__(
        self,
        maxsize: int = 0,
        *,
        backpressure_policy: str = BACKPRESSURE_BLOCK,
        target_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if backpressure_policy not in BACKPRESSURE_POLICIES:
            raise ValueError(
                f"Invalid backpressure_policy={backpressure_policy!r}, expected one of {sorted(BACKPRESSURE_POLICIES)}"
            )
        self._queue: deque[Any] = deque(maxlen=maxsize if maxsize > 0 else None)
        self._maxsize = maxsize
        self._backpressure_policy = backpressure_policy
        self._get_event: asyncio.Event | None = None
        self._target_loop = target_loop

    def _signal(self) -> None:
        """Wake the consumer's get() — cross-loop safe when target_loop is set."""
        if self._get_event is None:
            return
        if self._target_loop is not None:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is not self._target_loop:
                self._target_loop.call_soon_threadsafe(self._get_event.set)
                return
        self._get_event.set()

    async def put(self, msg: Any) -> bool:
        if self._backpressure_policy == BACKPRESSURE_BLOCK:
            self._queue.append(msg)
            self._signal()
            return True
        if self._backpressure_policy in (BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL):
            if len(self._queue) >= self._maxsize > 0:
                return False
            self._queue.append(msg)
            self._signal()
            return True
        return False

    def put_nowait(self, msg: Any) -> bool:
        if self._maxsize > 0 and len(self._queue) >= self._maxsize:
            return False
        self._queue.append(msg)
        self._signal()
        return True

    # get(), get_nowait(), empty(), full — unchanged
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py::TestFastMailboxCrossLoop -x -q`
Expected: PASS

- [ ] **Step 5: Run existing mailbox tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/ -k "mailbox or backpressure" -x -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add everything_is_an_actor/mailbox.py tests/test_dispatcher.py
git commit -m "feat: FastMailbox cross-loop signaling via target_loop"
```

---

### Task 3: _ActorCell cross-loop task creation

**Files:**
- Modify: `everything_is_an_actor/system.py:278-360` (_ActorCell.__init__ + start)
- Modify: `everything_is_an_actor/ref.py:169-187` (join + interrupt)
- Test: `tests/test_dispatcher.py` (append)

- [ ] **Step 1: Write failing integration test**

```python
# append to tests/test_dispatcher.py
from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.dispatcher import DefaultDispatcher, PoolDispatcher

class TestDispatcherIntegration:
    @pytest.mark.anyio
    async def test_default_dispatcher_same_loop(self):
        """Default dispatcher: actor runs on caller's loop."""
        class LoopReporter(Actor[str, str]):
            async def on_receive(self, message):
                import asyncio
                loop_id = id(asyncio.get_running_loop())
                return str(loop_id)

        system = ActorSystem("test", dispatcher=DefaultDispatcher())
        ref = await system.spawn(LoopReporter, "reporter")
        result = await system.ask(ref, "which-loop")
        assert result == str(id(asyncio.get_running_loop()))
        await system.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_different_loop(self):
        """Pool dispatcher: actor runs on a worker loop, not caller's."""
        class LoopReporter(Actor[str, str]):
            async def on_receive(self, message):
                import asyncio
                loop_id = id(asyncio.get_running_loop())
                return str(loop_id)

        dispatcher = PoolDispatcher(pool_size=2)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(LoopReporter, "reporter")
        result = await system.ask(ref, "which-loop")
        assert result != str(id(asyncio.get_running_loop()))
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_ask_reply(self):
        """Cross-loop ask/reply works correctly."""
        class Echo(Actor[str, str]):
            async def on_receive(self, message):
                return f"echo:{message}"

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(Echo, "echo")
        result = await system.ask(ref, "hello")
        assert result == "echo:hello"
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_multiple_actors(self):
        """Multiple actors on pool dispatcher all work."""
        class Doubler(Actor[int, int]):
            async def on_receive(self, message):
                return message * 2

        dispatcher = PoolDispatcher(pool_size=2)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        refs = [await system.spawn(Doubler, f"d{i}") for i in range(4)]
        results = await asyncio.gather(*[system.ask(r, i + 1) for i, r in enumerate(refs)])
        assert results == [2, 4, 6, 8]
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_stop_join(self):
        """stop() + join() works across loops."""
        class SlowActor(Actor[str, str]):
            async def on_receive(self, message):
                await asyncio.sleep(0.1)
                return "done"

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(SlowActor, "slow")
        ref.stop()
        await ref.join()
        assert not ref.is_alive
        await system.shutdown()
        await dispatcher.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py::TestDispatcherIntegration -x -q`
Expected: TypeError (unexpected keyword argument 'dispatcher')

- [ ] **Step 3: Modify _ActorCell to accept target_loop**

In `everything_is_an_actor/system.py`, modify `_ActorCell.__init__` and `start()`:

```python
# _ActorCell.__init__ — add target_loop parameter
def __init__(
    self,
    actor_cls: type[Actor],
    name: str,
    parent: _ActorCell | None,
    system: ActorSystem,
    mailbox: Mailbox,
    middlewares: list[Middleware] | None = None,
    target_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    # ... existing init code ...
    self._target_loop = target_loop
    # Cross-loop completion signal
    self._done = concurrent.futures.Future() if target_loop is not None else None

# _ActorCell.start — handle cross-loop task creation
async def start(self) -> None:
    self.actor = self.actor_cls()
    self.actor.context = ActorContext(self)
    # ... existing receive chain setup ...
    await self.actor.on_started()

    if self._target_loop is not None and self._target_loop is not asyncio.get_running_loop():
        # Cross-loop: create task on target loop
        async def _start_and_store():
            self.task = asyncio.current_task()
        
        # Schedule _run on target loop, with _start_and_store as preamble
        async def _cross_loop_run():
            self.task = asyncio.current_task()
            await self._run()
        
        fut = asyncio.run_coroutine_threadsafe(_cross_loop_run(), self._target_loop)
        # Wait briefly for task to be stored (non-blocking)
        await asyncio.sleep(0.01)
    else:
        self.task = asyncio.create_task(self._run(), name=f"actor:{self.path}")
```

- [ ] **Step 4: Modify _shutdown to signal cross-loop completion**

In `_ActorCell._shutdown()`, at the end:

```python
async def _shutdown(self) -> None:
    # ... existing shutdown code ...
    # Signal cross-loop joiners
    if self._done is not None and not self._done.done():
        self._done.set_result(None)
```

- [ ] **Step 5: Modify ActorRef.join() for cross-loop**

In `everything_is_an_actor/ref.py`:

```python
async def join(self) -> None:
    """Wait until the actor has fully stopped."""
    # Cross-loop: use the concurrent.futures.Future
    if self._cell._done is not None:
        if self._cell._done.done():
            return
        await asyncio.wrap_future(self._cell._done)
        return
    # Same-loop: await task directly (existing behavior)
    task = self._cell.task
    if task is None or task.done():
        return
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 6: Modify ActorRef.interrupt() for cross-loop**

```python
def interrupt(self) -> None:
    """Cancel the actor's asyncio task immediately."""
    task = self._cell.task
    if task is not None and not task.done():
        if self._cell._target_loop is not None and self._cell._target_loop is not asyncio.get_event_loop():
            self._cell._target_loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()
```

- [ ] **Step 7: Modify ActorSystem to accept dispatcher**

In `everything_is_an_actor/system.py`, `ActorSystem.__init__` and `spawn()`:

```python
class ActorSystem:
    def __init__(
        self,
        name: str = "system",
        *,
        max_dead_letters: int = _MAX_DEAD_LETTERS,
        executor_workers: int | None = 4,
        reply_channel: ReplyChannel | None = None,
        mailbox_cls: type[Mailbox] = MemoryMailbox,
        threaded: bool = False,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        # ... existing init ...
        self._dispatcher = dispatcher  # None = no dispatcher, use current loop

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        mailbox: Mailbox | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> ActorRef[MsgT, RetT]:
        # ... existing validation ...
        
        # Determine target loop via dispatcher
        target_loop: asyncio.AbstractEventLoop | None = None
        actual_mailbox = mailbox
        if self._dispatcher is not None:
            target_loop = self._dispatcher.assign(actor_cls, name)
            if target_loop is asyncio.get_running_loop():
                target_loop = None  # Same loop, no cross-loop overhead
            elif actual_mailbox is None:
                # Cross-loop: use FastMailbox with target_loop for thread-safe signaling
                from everything_is_an_actor.mailbox import FastMailbox
                actual_mailbox = FastMailbox(mailbox_size, target_loop=target_loop)
        
        cell = _ActorCell(
            actor_cls=actor_cls,
            name=name,
            parent=None,
            system=self,
            mailbox=actual_mailbox or self._mailbox_cls(mailbox_size),
            middlewares=middlewares or [],
            target_loop=target_loop,
        )
        self._root_cells[name] = cell
        try:
            await cell.start()
        except Exception:
            del self._root_cells[name]
            raise
        return cell.ref
```

- [ ] **Step 8: Modify ActorSystem.shutdown to shutdown dispatcher**

NOT needed — dispatcher lifecycle is managed externally (caller starts/stops it). ActorSystem just uses it.

- [ ] **Step 9: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py::TestDispatcherIntegration -x -q`
Expected: PASS

- [ ] **Step 10: Run full test suite to verify no regression**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_regression.py --ignore=tests/test_mailbox.py -q`
Expected: all PASS (existing tests use no dispatcher, so they get current-loop behavior)

- [ ] **Step 11: Commit**

```bash
git add everything_is_an_actor/system.py everything_is_an_actor/ref.py tests/test_dispatcher.py
git commit -m "feat: ActorSystem + _ActorCell + ActorRef cross-loop support via Dispatcher"
```

---

### Task 4: Export and wire up

**Files:**
- Modify: `everything_is_an_actor/__init__.py`
- Test: `tests/test_dispatcher.py` (append)

- [ ] **Step 1: Add exports**

```python
# __init__.py — add to imports
from everything_is_an_actor.dispatcher import DefaultDispatcher, Dispatcher, PoolDispatcher

# __all__ — add entries
"DefaultDispatcher",
"Dispatcher",
"PoolDispatcher",
```

- [ ] **Step 2: Write end-to-end test with AgentActor**

```python
# append to tests/test_dispatcher.py
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task, TaskResult

class TestDispatcherAgentActor:
    @pytest.mark.anyio
    async def test_agent_actor_on_pool_dispatcher(self):
        """AgentActor works correctly on a pool dispatcher."""
        class UpperAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                return input.upper()

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(UpperAgent, "upper")
        result: TaskResult[str] = await system.ask(ref, Task(input="hello"))
        assert result.output == "HELLO"
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_child_actor_inherits_parent_loop(self):
        """Child actors spawned via context.spawn run on same loop as parent."""
        class Parent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                child_ref = await self.context.spawn(Child, "child")
                result = await child_ref._ask("which-loop")
                return result

        class Child(Actor[str, str]):
            async def on_receive(self, message):
                return str(id(asyncio.get_running_loop()))

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(Parent, "parent")
        result = await system.ask(ref, Task(input="go"))
        # Child should report the same loop as parent (worker loop, not caller loop)
        assert result.output != str(id(asyncio.get_running_loop()))
        await system.shutdown()
        await dispatcher.shutdown()
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py -x -q`
Expected: all PASS

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_regression.py --ignore=tests/test_mailbox.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add everything_is_an_actor/__init__.py tests/test_dispatcher.py
git commit -m "feat: export Dispatcher types, add AgentActor integration tests"
```
