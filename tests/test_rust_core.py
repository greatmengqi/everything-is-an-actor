"""Rust core module tests."""

import pytest

# Skip if rust_core is not available (not built via maturin)
rust_core_available = False
try:
    from rust_core import RustSystem
    rust_core_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not rust_core_available,
    reason="rust_core not installed (run 'cd rust_core && maturin develop' first)"
)


class TestRustSystem:
    """Test RustSystem core functionality."""

    def test_system_creation(self):
        """Test RustSystem can be created."""
        from rust_core import RustSystem

        system = RustSystem()
        assert system is not None

    def test_spawn_returns_path(self):
        """Test spawn returns the actor path."""
        from rust_core import RustSystem

        system = RustSystem()
        result = system.spawn("/test/spawn", 100, lambda x: x)
        assert result == "/test/spawn"
        system.stop_actor("/test/spawn")

    def test_tell_returns_bool(self):
        """Test tell returns boolean success indicator."""
        from rust_core import RustSystem

        system = RustSystem()
        system.spawn("/test/tell", 100, lambda x: x)

        result = system.tell("/test/tell", "hello")
        assert isinstance(result, bool)

        system.stop_actor("/test/tell")

    def test_tell_nonexistent_returns_false(self):
        """Test tell to nonexistent actor returns False."""
        from rust_core import RustSystem

        system = RustSystem()
        result = system.tell("/nonexistent/actor", "hello")
        assert result is False

    def test_is_alive_after_spawn(self):
        """Test actor is alive after spawn."""
        from rust_core import RustSystem

        system = RustSystem()
        system.spawn("/test/alive", 100, lambda x: x)

        assert system.is_alive("/test/alive") is True
        system.stop_actor("/test/alive")

    def test_is_alive_after_stop(self):
        """Test actor is not alive after stop."""
        from rust_core import RustSystem

        system = RustSystem()
        system.spawn("/test/dead", 100, lambda x: x)
        system.stop_actor("/test/dead")

        assert system.is_alive("/test/dead") is False

    def test_is_alive_nonexistent(self):
        """Test is_alive returns False for nonexistent actor."""
        from rust_core import RustSystem

        system = RustSystem()
        assert system.is_alive("/nonexistent") is False

    def test_stop_actor_returns_true(self):
        """Test stop_actor returns True when actor exists."""
        from rust_core import RustSystem

        system = RustSystem()
        system.spawn("/test/stop", 100, lambda x: x)

        result = system.stop_actor("/test/stop")
        assert result is True

    def test_stop_actor_nonexistent_returns_false(self):
        """Test stop_actor returns False for nonexistent actor."""
        from rust_core import RustSystem

        system = RustSystem()
        result = system.stop_actor("/nonexistent")
        assert result is False

    def test_multiple_actors_individually(self):
        """Test managing multiple actors in separate systems."""
        from rust_core import RustSystem

        # Test 3 actors in sequence to avoid resource issues
        for i in range(3):
            system = RustSystem()
            path = f"/test/multi/{i}"
            system.spawn(path, 100, lambda x: x)
            assert system.is_alive(path) is True
            system.stop_actor(path)

    def test_actor_processes_messages(self):
        """Test actor actually processes messages via tell."""
        from rust_core import RustSystem

        results = []

        def handler(msg):
            results.append(msg)

        system = RustSystem()
        system.spawn("/test/process", 100, handler)

        # Send messages
        for i in range(5):
            system.tell("/test/process", f"msg-{i}")

        # Give time for processing
        import time
        time.sleep(0.5)

        # Verify messages were processed
        assert len(results) == 5
        assert results == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]

        system.stop_actor("/test/process")
