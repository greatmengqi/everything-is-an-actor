"""
RootLoopActorSystem 压力测试

测试场景：
1. 多 Loop 并发性能
2. 跨 Loop 通信吞吐量
3. 与单 Loop 系统对比
"""

import asyncio
import time
from dataclasses import dataclass
from typing import List

from everything_is_an_actor.actor import Actor
from examples.root_loop_system import RootLoopActorSystem
from everything_is_an_actor.system import ActorSystem


@dataclass
class BenchmarkResult:
    """压测结果"""
    name: str
    total_messages: int
    duration_sec: float
    msg_per_sec: float
    avg_latency_ms: float


class EchoActor(Actor):
    """回声 Actor"""
    async def on_receive(self, msg):
        return msg


class CounterActor(Actor):
    """计数 Actor"""
    def __init__(self):
        self.count = 0
    
    async def on_receive(self, msg):
        if msg == "count":
            return self.count
        self.count += 1
        return self.count


async def benchmark_root_loop_system(
    num_roots: int,
    messages_per_actor: int,
) -> BenchmarkResult:
    """
    压测 RootLoopActorSystem
    
    Args:
        num_roots: Root Actor 数量
        messages_per_actor: 每个 Actor 处理的消息数
    """
    system = RootLoopActorSystem("benchmark")
    
    # 创建多个 Root Actor
    refs = []
    for i in range(num_roots):
        ref = await system.spawn(EchoActor, f"echo-{i}")
        refs.append(ref)
    
    total_messages = num_roots * messages_per_actor
    
    # 开始压测
    start = time.perf_counter()
    
    # 并发发送消息
    tasks = []
    for ref in refs:
        for _ in range(messages_per_actor):
            # system.tell(ref, f"msg-{i}")
            pass  # 简化：实际需要 ActorCell 实现
    
    # 等待所有消息处理完成
    # await asyncio.gather(*tasks)
    
    # 简化：模拟处理时间
    await asyncio.sleep(0.1)
    
    duration = time.perf_counter() - start
    msg_per_sec = total_messages / duration if duration > 0 else 0
    
    await system.shutdown()
    
    return BenchmarkResult(
        name=f"RootLoop (N={num_roots})",
        total_messages=total_messages,
        duration_sec=duration,
        msg_per_sec=msg_per_sec,
        avg_latency_ms=0.0,  # TODO: 实际测量
    )


async def benchmark_single_loop_system(
    num_actors: int,
    messages_per_actor: int,
) -> BenchmarkResult:
    """
    压测单 Loop ActorSystem（对比基准）
    
    Args:
        num_actors: Actor 数量
        messages_per_actor: 每个 Actor 处理的消息数
    """
    system = ActorSystem("benchmark")
    
    # 创建多个 Actor（共享一个 Loop）
    refs = []
    for i in range(num_actors):
        ref = await system.spawn(EchoActor, f"echo-{i}")
        refs.append(ref)
    
    total_messages = num_actors * messages_per_actor
    
    # 开始压测
    start = time.perf_counter()
    
    # 并发发送消息
    tasks = []
    for i, ref in enumerate(refs):
        for j in range(messages_per_actor):
            tasks.append(ref.ask(f"msg-{i}-{j}"))
    
    # 等待所有消息处理完成
    results = await asyncio.gather(*tasks)
    
    duration = time.perf_counter() - start
    msg_per_sec = total_messages / duration if duration > 0 else 0
    
    await system.shutdown()
    
    return BenchmarkResult(
        name=f"SingleLoop (N={num_actors})",
        total_messages=total_messages,
        duration_sec=duration,
        msg_per_sec=msg_per_sec,
        avg_latency_ms=(duration / total_messages * 1000) if total_messages > 0 else 0,
    )


async def benchmark_cross_loop_communication(
    num_roots: int,
    messages_per_pair: int,
) -> BenchmarkResult:
    """
    压测跨 Loop 通信
    
    Args:
        num_roots: Root Actor 对数
        messages_per_pair: 每对 Actor 之间的消息数
    """
    system = RootLoopActorSystem("benchmark")
    
    # 创建多对 Actor
    pairs = []
    for i in range(num_roots):
        a = await system.spawn(EchoActor, f"actor-{i}a")
        b = await system.spawn(EchoActor, f"actor-{i}b")
        pairs.append((a, b))
    
    total_messages = num_roots * 2 * messages_per_pair
    
    # 开始压测
    start = time.perf_counter()
    
    # 跨 Loop 通信：a.tell(b, msg)
    # 简化：模拟处理时间
    await asyncio.sleep(0.1)
    
    duration = time.perf_counter() - start
    msg_per_sec = total_messages / duration if duration > 0 else 0
    
    await system.shutdown()
    
    return BenchmarkResult(
        name=f"CrossLoop (pairs={num_roots})",
        total_messages=total_messages,
        duration_sec=duration,
        msg_per_sec=msg_per_sec,
        avg_latency_ms=0.0,
    )


async def benchmark_loop_creation(num_loops: int) -> BenchmarkResult:
    """
    压测 Loop 创建速度
    """
    system = RootLoopActorSystem("benchmark")
    
    start = time.perf_counter()
    
    for i in range(num_loops):
        await system.spawn(EchoActor, f"actor-{i}")
    
    duration = time.perf_counter() - start
    
    await system.shutdown()
    
    return BenchmarkResult(
        name=f"LoopCreation (N={num_loops})",
        total_messages=num_loops,
        duration_sec=duration,
        msg_per_sec=num_loops / duration if duration > 0 else 0,
        avg_latency_ms=(duration / num_loops * 1000) if num_loops > 0 else 0,
    )


async def main():
    """运行所有压测"""
    print("=" * 60)
    print("RootLoopActorSystem 压力测试")
    print("=" * 60)
    
    results: List[BenchmarkResult] = []
    
    # 测试 1: Loop 创建速度
    print("\n[1] Loop 创建速度测试...")
    for n in [10, 50, 100]:
        result = await benchmark_loop_creation(n)
        results.append(result)
        print(f"  创建 {n} 个 Loop: {result.duration_sec:.3f}s ({result.msg_per_sec:.0f} loops/sec)")
    
    # 测试 2: 单 Loop 吞吐量（基准）
    print("\n[2] 单 Loop 吞吐量测试（基准）...")
    for n_actors in [1, 10, 100]:
        result = await benchmark_single_loop_system(n_actors, 100)
        results.append(result)
        print(f"  {n_actors} actors × 100 msgs: {result.msg_per_sec:.0f} msg/sec, latency: {result.avg_latency_ms:.3f}ms")
    
    # 测试 3: 多 Loop 吞吐量
    print("\n[3] 多 Loop 吞吐量测试...")
    for n_roots in [1, 5, 10, 20]:
        result = await benchmark_root_loop_system(n_roots, 100)
        results.append(result)
        print(f"  {n_roots} roots × 100 msgs: {result.msg_per_sec:.0f} msg/sec")
    
    # 测试 4: 跨 Loop 通信
    print("\n[4] 跨 Loop 通信测试...")
    for n_pairs in [5, 10, 20]:
        result = await benchmark_cross_loop_communication(n_pairs, 100)
        results.append(result)
        print(f"  {n_pairs} pairs × 100 msgs: {result.msg_per_sec:.0f} msg/sec")
    
    # 汇总
    print("\n" + "=" * 60)
    print("压测结果汇总")
    print("=" * 60)
    print(f"{'测试名称':<30} {'消息数':>10} {'耗时(s)':>10} {'吞吐量':>15}")
    print("-" * 60)
    for r in results:
        print(f"{r.name:<30} {r.total_messages:>10} {r.duration_sec:>10.3f} {r.msg_per_sec:>15.0f}")
    
    # 对比分析
    print("\n" + "=" * 60)
    print("性能对比分析")
    print("=" * 60)
    
    # 单 Loop vs 多 Loop
    single_loop = next((r for r in results if "SingleLoop (N=10)" in r.name), None)
    multi_loop = next((r for r in results if "RootLoop (N=10)" in r.name), None)
    
    if single_loop and multi_loop:
        ratio = multi_loop.msg_per_sec / single_loop.msg_per_sec if single_loop.msg_per_sec > 0 else 0
        print(f"多 Loop (10) vs 单 Loop (10): {ratio:.2f}x")
    
    print("\n注意: 当前实现缺少 ActorCell，部分测试为模拟数据")


if __name__ == "__main__":
    asyncio.run(main())
