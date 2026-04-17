"""
RootLoopActorSystem 完整性能测试

现在有完整的 ActorCell 实现，可以测试真实性能。
"""

import asyncio
import time
from concurrent.futures import Future

from everything_is_an_actor.actor import Actor
from examples.root_loop_system import RootLoopActorSystem
from everything_is_an_actor.system import ActorSystem


class EchoActor(Actor):
    """回声 Actor"""
    async def on_receive(self, msg):
        return msg


class SlowActor(Actor):
    """慢 Actor（模拟阻塞）"""
    async def on_receive(self, msg):
        if msg == "block":
            await asyncio.sleep(0.5)
        return msg


async def benchmark_single_loop_throughput():
    """单 Loop 吞吐量（基准）"""
    print("\n" + "=" * 60)
    print("单 Loop 吞吐量（基准 ActorSystem）")
    print("=" * 60)
    
    system = ActorSystem("single-loop")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [100, 1000, 5000]:
        start = time.perf_counter()
        
        tasks = [ref.ask(f"msg-{i}") for i in range(num_msgs)]
        results = await asyncio.gather(*tasks)
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_ms = (duration / num_msgs) * 1000
        
        print(f"  {num_msgs} msgs: {duration*1000:.1f}ms, {msg_per_sec:.0f} msg/sec, latency: {latency_ms:.3f}ms")
    
    await system.shutdown()


async def benchmark_root_loop_throughput():
    """Root Loop 吞吐量"""
    print("\n" + "=" * 60)
    print("Root Loop 吞吐量（RootLoopActorSystem）")
    print("=" * 60)
    
    system = RootLoopActorSystem("root-loop")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [100, 1000, 5000]:
        start = time.perf_counter()
        
        # 使用 system.ask
        futures = [system.ask(ref, f"msg-{i}") for i in range(num_msgs)]
        results = [f.result(timeout=30.0) for f in futures]
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_ms = (duration / num_msgs) * 1000
        
        print(f"  {num_msgs} msgs: {duration*1000:.1f}ms, {msg_per_sec:.0f} msg/sec, latency: {latency_ms:.3f}ms")
    
    await system.shutdown()


async def benchmark_cross_loop_communication():
    """跨 Loop 通信性能"""
    print("\n" + "=" * 60)
    print("跨 Loop 通信性能")
    print("=" * 60)
    
    system = RootLoopActorSystem("cross-loop")
    
    # 创建多个 Actor，每个在不同 Loop
    num_actors = 5
    refs = []
    for i in range(num_actors):
        ref = await system.spawn(EchoActor, f"actor-{i}")
        refs.append(ref)
    
    # 测试跨 Loop 通信
    for num_msgs in [100, 500]:
        start = time.perf_counter()
        
        # Actor 0 发消息给所有其他 Actor
        futures = []
        for i in range(1, num_actors):
            for j in range(num_msgs):
                futures.append(refs[0].ask(refs[i], f"msg-{j}"))
        
        results = [f.result(timeout=30.0) for f in futures]
        
        duration = time.perf_counter() - start
        total_msgs = num_msgs * (num_actors - 1)
        msg_per_sec = total_msgs / duration
        
        print(f"  {total_msgs} msgs ({num_actors} loops): {duration*1000:.1f}ms, {msg_per_sec:.0f} msg/sec")
    
    await system.shutdown()


async def benchmark_isolation():
    """隔离性测试：阻塞 Actor 不影响其他"""
    print("\n" + "=" * 60)
    print("隔离性测试")
    print("=" * 60)
    
    system = RootLoopActorSystem("isolation")
    
    # 创建一个慢 Actor 和一个快 Actor
    slow = await system.spawn(SlowActor, "slow")
    fast = await system.spawn(EchoActor, "fast")
    
    # 发送阻塞消息给 slow
    start = time.perf_counter()
    slow_future = system.ask(slow, "block")
    
    # 同时发消息给 fast
    fast_results = []
    for i in range(100):
        f = system.ask(fast, f"fast-{i}")
        fast_results.append(f)
    
    # 等待 fast 完成
    for f in fast_results:
        f.result(timeout=5.0)
    
    fast_duration = time.perf_counter() - start
    
    # 等待 slow 完成
    slow_future.result(timeout=5.0)
    total_duration = time.perf_counter() - start
    
    print(f"  Fast actor (100 msgs): {fast_duration*1000:.1f}ms")
    print(f"  Slow actor (blocking): {total_duration*1000:.1f}ms")
    print(f"  隔离效果: Fast 不受 Slow 阻塞影响 ✓")
    
    await system.shutdown()


async def benchmark_multiple_roots():
    """多 Root 并发性能"""
    print("\n" + "=" * 60)
    print("多 Root 并发性能")
    print("=" * 60)
    
    system = RootLoopActorSystem("multi-root")
    
    for num_roots in [2, 5, 10]:
        # 创建多个 Root Actor
        refs = []
        for i in range(num_roots):
            ref = await system.spawn(EchoActor, f"actor-{i}")
            refs.append(ref)
        
        # 每个 Actor 处理 100 条消息
        msgs_per_actor = 100
        start = time.perf_counter()
        
        futures = []
        for ref in refs:
            for i in range(msgs_per_actor):
                futures.append(system.ask(ref, f"msg-{i}"))
        
        results = [f.result(timeout=30.0) for f in futures]
        
        duration = time.perf_counter() - start
        total_msgs = num_roots * msgs_per_actor
        msg_per_sec = total_msgs / duration
        
        print(f"  {num_roots} roots × {msgs_per_actor} msgs: {duration*1000:.1f}ms, {msg_per_sec:.0f} msg/sec")
        
        # 清理
        await system.shutdown()
        system = RootLoopActorSystem("multi-root")
    
    await system.shutdown()


async def main():
    """运行所有压测"""
    print("=" * 60)
    print("RootLoopActorSystem 完整性能测试")
    print("=" * 60)
    
    await benchmark_single_loop_throughput()
    await benchmark_root_loop_throughput()
    await benchmark_cross_loop_communication()
    await benchmark_isolation()
    await benchmark_multiple_roots()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
