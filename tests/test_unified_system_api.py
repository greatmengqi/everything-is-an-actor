"""
测试统一 ActorSystem API
"""

import asyncio
import time

import pytest

from everything_is_an_actor.actor import Actor
from everything_is_an_actor.unified_system import ActorSystem, ActorSystemMode


class EchoActor(Actor):
    """回声 Actor"""
    async def on_receive(self, msg):
        return msg


class CounterActor(Actor):
    """计数 Actor"""
    def __init__(self):
        self.count = 0
    
    async def on_receive(self, msg):
        if msg == "get":
            return self.count
        self.count += 1
        return self.count


class BlockingActor(Actor):
    """阻塞 Actor"""
    async def on_receive(self, msg):
        if msg == "block":
            await asyncio.sleep(1.0)
            return "blocked"
        return msg


class FastActor(Actor):
    """快速 Actor"""
    async def on_receive(self, msg):
        return f"fast: {msg}"


@pytest.mark.asyncio
async def test_single_loop_spawn_and_ask():
    """测试 single 模式"""
    system = ActorSystem(mode=ActorSystemMode.SINGLE)
    
    ref = await system.spawn(EchoActor, "echo")
    result = await system.ask(ref, "hello")
    
    assert result == "hello"
    
    await system.shutdown()


@pytest.mark.asyncio
async def test_multi_loop_spawn_and_ask():
    """测试 multi-loop 模式"""
    system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP)
    
    ref = await system.spawn(EchoActor, "echo")
    result = await system.ask(ref, "hello")
    
    assert result == "hello"
    
    await system.shutdown()


@pytest.mark.asyncio
async def test_multi_loop_isolation():
    """测试 multi-loop 隔离性"""
    system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP)
    
    blocker = await system.spawn(BlockingActor, "blocker")
    fast = await system.spawn(FastActor, "fast")
    
    # 发送阻塞消息
    await system.tell(blocker, "block")

    # Fast actor 应该快速响应
    start = time.perf_counter()
    result = await system.ask(fast, "ping", timeout=5.0)
    duration = time.perf_counter() - start
    
    assert duration < 0.1, f"Expected fast response, got {duration}s"
    assert result == "fast: ping"
    
    await system.shutdown()


@pytest.mark.asyncio
async def test_multi_loop_child_actor():
    """测试 multi-loop 子 Actor"""
    system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP)
    
    parent = await system.spawn(EchoActor, "parent")
    child = await system.spawn_child(parent, EchoActor, "child")
    
    # 子 Actor 应该共享父节点的 Loop
    assert child.loop is parent.loop
    
    result = await system.ask(child, "hello")
    assert result == "hello"
    
    await system.shutdown()


@pytest.mark.asyncio
async def test_auto_mode():
    """测试自动模式"""
    system = ActorSystem(mode=ActorSystemMode.AUTO)

    # 应该自动选择模式
    assert system.mode in (ActorSystemMode.SINGLE, ActorSystemMode.MULTI_LOOP)

    ref = await system.spawn(EchoActor, "echo")
    result = await system.ask(ref, "hello")

    assert result == "hello"

    await system.shutdown()


@pytest.mark.asyncio
async def test_timeout_zero_immediate_fail():
    """测试 timeout=0 立即超时"""
    system = ActorSystem(mode=ActorSystemMode.SINGLE)

    ref = await system.spawn(BlockingActor, "blocker")

    # 发送阻塞消息
    await system.tell(ref, "block")

    # timeout=0 应该立即超时，而不是使用默认 timeout
    with pytest.raises(Exception):  # TimeoutError or similar
        await system.ask(ref, "ping", timeout=0)

    await system.shutdown()


@pytest.mark.asyncio
async def test_timeout_zero_point_zero_immediate_fail():
    """测试 timeout=0.0 立即超时"""
    system = ActorSystem(mode=ActorSystemMode.SINGLE)

    ref = await system.spawn(BlockingActor, "blocker")

    # 发送阻塞消息
    await system.tell(ref, "block")

    # timeout=0.0 应该立即超时，而不是使用默认 timeout
    with pytest.raises(Exception):  # TimeoutError or similar
        await system.ask(ref, "ping", timeout=0.0)

    await system.shutdown()


@pytest.mark.asyncio
async def test_timeout_none_uses_default():
    """测试 timeout=None 使用默认超时"""
    system = ActorSystem(mode=ActorSystemMode.SINGLE)

    ref = await system.spawn(EchoActor, "echo")

    # timeout=None 应该使用默认超时
    result = await system.ask(ref, "hello", timeout=None)
    assert result == "hello"

    await system.shutdown()


@pytest.mark.asyncio
async def test_explicit_timeout_not_overridden():
    """测试显式超时值不会被覆盖"""
    system = ActorSystem(mode=ActorSystemMode.SINGLE)

    ref = await system.spawn(EchoActor, "echo")

    # 各种显式超时值都应该被保留
    for timeout in [0.1, 1.0, 5.0]:
        result = await system.ask(ref, "hello", timeout=timeout)
        assert result == "hello"

    await system.shutdown()


if __name__ == "__main__":
    asyncio.run(test_single_loop_spawn_and_ask())
    asyncio.run(test_multi_loop_spawn_and_ask())
    asyncio.run(test_multi_loop_isolation())
    asyncio.run(test_multi_loop_child_actor())
    asyncio.run(test_auto_mode())
    asyncio.run(test_timeout_zero_immediate_fail())
    asyncio.run(test_timeout_zero_point_zero_immediate_fail())
    asyncio.run(test_timeout_none_uses_default())
    asyncio.run(test_explicit_timeout_not_overridden())
    print("All tests passed!")
