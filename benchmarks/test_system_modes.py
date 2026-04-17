"""
系统模式对比测试（P1）
- 场景 3: Single vs Multi-Loop 性能对比
- 场景 4: 跨 Loop 通信性能
"""

import asyncio
import time
from typing import List, Tuple, Dict, Any

from everything_is_an_actor import Actor
from everything_is_an_actor.unified_system import ActorSystem
from benchmarks.monitor import SystemMonitor


# ============ 测试用 Actor ============

class EchoActor(Actor):
    """回声 Actor"""
    async def on_receive(self, message):
        return message


class BlockingActor(Actor):
    """模拟阻塞操作的 Actor"""
    async def on_receive(self, message):
        if message == "block":
            # 模拟阻塞操作
            await asyncio.sleep(0.1)
        return message


# ============ 场景 3: Single vs Multi-Loop 性能对比 ============

async def scenario_3_single_loop_baseline(
    warmup: bool = False,
    actor_count: int = 100,
    messages_per_actor: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 3: Single Loop 基准测试
    
    Args:
        warmup: 是否预热
        actor_count: Actor 数量
        messages_per_actor: 每个 Actor 的消息数
    """
    if warmup:
        actor_count = min(actor_count, 10)
        messages_per_actor = min(messages_per_actor, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    # Single Loop 模式
    system = ActorSystem('bench', mode='single')
    
    # 创建多个 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(EchoActor, f'echo-{i}')
        refs.append(ref)
    
    total_messages = actor_count * messages_per_actor
    latencies = []
    
    # 并发发送消息
    async def send_messages(ref, actor_id):
        local_latencies = []
        for j in range(messages_per_actor):
            msg_start = time.perf_counter()
            await ref.ask(f'msg-{actor_id}-{j}', timeout=10.0)
            local_latencies.append(time.perf_counter() - msg_start)
        return local_latencies
    
    tasks = [send_messages(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_3_multi_loop_performance(
    warmup: bool = False,
    num_loops: int = 4,
    actor_count: int = 100,
    messages_per_actor: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 3: Multi-Loop 性能测试
    
    Args:
        warmup: 是否预热
        num_loops: Loop 数量
        actor_count: Actor 数量
        messages_per_actor: 每个 Actor 的消息数
    """
    if warmup:
        actor_count = min(actor_count, 10)
        messages_per_actor = min(messages_per_actor, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    # Multi-Loop 模式
    system = ActorSystem('bench', mode='multi-loop', num_workers=num_loops)
    
    # 创建多个 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(EchoActor, f'echo-{i}')
        refs.append(ref)
    
    total_messages = actor_count * messages_per_actor
    latencies = []
    
    # 并发发送消息
    async def send_messages(ref, actor_id):
        local_latencies = []
        for j in range(messages_per_actor):
            msg_start = time.perf_counter()
            await ref.ask(f'msg-{actor_id}-{j}', timeout=10.0)
            local_latencies.append(time.perf_counter() - msg_start)
        return local_latencies
    
    tasks = [send_messages(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_3_blocking_isolation(
    warmup: bool = False,
    mode: str = 'single',
    blocking_actor_count: int = 5,
    normal_actor_count: int = 20,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 3: 阻塞操作隔离测试
    
    测试阻塞 Actor 是否影响其他 Actor 的性能
    
    Args:
        warmup: 是否预热
        mode: 系统模式 ('single' 或 'multi-loop')
        blocking_actor_count: 阻塞 Actor 数量
        normal_actor_count: 正常 Actor 数量
    """
    if warmup:
        blocking_actor_count = min(blocking_actor_count, 2)
        normal_actor_count = min(normal_actor_count, 5)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mode=mode, num_workers=4 if mode == 'multi-loop' else 1)
    
    # 创建阻塞 Actor
    blocking_refs = []
    for i in range(blocking_actor_count):
        ref = await system.spawn(BlockingActor, f'blocking-{i}')
        blocking_refs.append(ref)
    
    # 创建正常 Actor
    normal_refs = []
    for i in range(normal_actor_count):
        ref = await system.spawn(EchoActor, f'normal-{i}')
        normal_refs.append(ref)
    
    latencies = []
    
    # 启动阻塞操作（后台运行）
    blocking_tasks = [
        asyncio.create_task(ref.ask("block", timeout=5.0))
        for ref in blocking_refs
    ]
    
    # 测量正常 Actor 的响应时间
    start_time = time.perf_counter()
    
    async def test_normal_actor(ref, actor_id):
        local_latencies = []
        for j in range(10):
            msg_start = time.perf_counter()
            await ref.ask(f'msg-{actor_id}-{j}', timeout=5.0)
            local_latencies.append(time.perf_counter() - msg_start)
        return local_latencies
    
    tasks = [test_normal_actor(ref, i) for i, ref in enumerate(normal_refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    # 等待阻塞任务完成
    await asyncio.gather(*blocking_tasks)
    
    total_messages = normal_actor_count * 10
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 场景 4: 跨 Loop 通信性能 ============

async def scenario_4_cross_loop_communication(
    warmup: bool = False,
    num_loops: int = 4,
    messages_per_pair: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 4: 跨 Loop 通信性能测试
    
    测试不同 Loop 之间的 Actor 通信性能
    
    Args:
        warmup: 是否预热
        num_loops: Loop 数量
        messages_per_pair: 每对 Actor 之间的消息数
    """
    if warmup:
        messages_per_pair = min(messages_per_pair, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mode='multi-loop', num_workers=num_loops)
    
    # 创建多对 Actor（每个 Loop 一对）
    pairs = []
    for i in range(num_loops):
        actor_a = await system.spawn(EchoActor, f'actor-{i}a')
        actor_b = await system.spawn(EchoActor, f'actor-{i}b')
        pairs.append((actor_a, actor_b))
    
    total_messages = num_loops * 2 * messages_per_pair
    latencies = []
    
    # 跨 Loop 通信：actor_a -> actor_b
    async def cross_loop_test(actor_a, actor_b, loop_id):
        local_latencies = []
        
        # A -> B
        for j in range(messages_per_pair):
            msg_start = time.perf_counter()
            await actor_b.ask(f'from-a-{loop_id}-{j}', timeout=10.0)
            local_latencies.append(time.perf_counter() - msg_start)
        
        # B -> A
        for j in range(messages_per_pair):
            msg_start = time.perf_counter()
            await actor_a.ask(f'from-b-{loop_id}-{j}', timeout=10.0)
            local_latencies.append(time.perf_counter() - msg_start)
        
        return local_latencies
    
    tasks = [cross_loop_test(a, b, i) for i, (a, b) in enumerate(pairs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 运行所有模式对比测试 ============

async def run_system_mode_tests():
    """运行所有系统模式对比测试"""
    from benchmarks.stress_test_framework import StressTestRunner
    
    runner = StressTestRunner()
    
    print("\n" + "="*60)
    print("系统模式对比测试（P1）")
    print("="*60)
    
    # 测试 1: Single Loop 基准
    print("\n[3.1] Single Loop 基准测试")
    for actor_count in [10, 100, 1000]:
        await runner.run_scenario(
            f"single_loop_N={actor_count}",
            scenario_3_single_loop_baseline,
            actor_count=actor_count,
            messages_per_actor=100,
        )
    
    # 测试 2: Multi-Loop 性能
    print("\n[3.2] Multi-Loop 性能测试")
    for num_loops in [2, 4, 8]:
        await runner.run_scenario(
            f"multi_loop_workers={num_loops}",
            scenario_3_multi_loop_performance,
            num_loops=num_loops,
            actor_count=100,
            messages_per_actor=100,
        )
    
    # 测试 3: 阻塞隔离对比
    print("\n[3.3] 阻塞操作隔离对比")
    await runner.run_scenario(
        "blocking_isolation_single",
        scenario_3_blocking_isolation,
        mode='single',
    )
    await runner.run_scenario(
        "blocking_isolation_multi_loop",
        scenario_3_blocking_isolation,
        mode='multi-loop',
    )
    
    # 测试 4: 跨 Loop 通信
    print("\n[4.1] 跨 Loop 通信性能")
    for num_loops in [2, 4, 8]:
        await runner.run_scenario(
            f"cross_loop_N={num_loops}",
            scenario_4_cross_loop_communication,
            num_loops=num_loops,
            messages_per_pair=100,
        )
    
    # 保存结果
    runner.save_results("system_mode_results.json")
    
    # 生成报告
    report = runner.generate_report()
    report_file = runner.output_dir / "system_mode_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存到: {report_file}")
    
    # 打印汇总
    runner.print_summary()
    
    return runner


if __name__ == "__main__":
    asyncio.run(run_system_mode_tests())
