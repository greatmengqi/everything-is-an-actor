"""
极限压力测试（P2）
- 场景 7: 邮箱满载测试
- 场景 8: 长时间稳定性测试
- 场景 9: 故障恢复测试
"""

import asyncio
import time
from typing import List, Tuple, Dict, Any

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.mailbox import (
    MemoryMailbox, FastMailbox,
    BACKPRESSURE_BLOCK, BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL
)
from benchmarks.monitor import SystemMonitor


# ============ 测试用 Actor ============

class SlowActor(Actor):
    """慢速 Actor - 模拟处理延迟"""
    async def on_receive(self, message):
        await asyncio.sleep(0.01)  # 10ms 延迟
        return message


class FlakyActor(Actor):
    """不稳定 Actor - 模拟故障"""
    def __init__(self):
        self.call_count = 0
    
    async def on_receive(self, message):
        self.call_count += 1
        if self.call_count % 10 == 0:
            raise RuntimeError(f"Scheduled failure at call {self.call_count}")
        return message


# ============ 场景 7: 邮箱满载测试 ============

async def scenario_7_mailbox_full_block(
    warmup: bool = False,
    mailbox_size: int = 100,
    producer_count: int = 5,
    messages_per_producer: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 7: 邮箱满载 - 阻塞策略
    
    Args:
        warmup: 是否预热
        mailbox_size: 邮箱大小
        producer_count: 生产者数量
        messages_per_producer: 每个生产者的消息数
    """
    if warmup:
        mailbox_size = 10
        producer_count = 2
        messages_per_producer = 10
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=MemoryMailbox)
    ref = await system.spawn(
        SlowActor, 'slow',
        mailbox_size=mailbox_size,
        backpressure_policy=BACKPRESSURE_BLOCK
    )
    
    total_messages = producer_count * messages_per_producer
    latencies = []
    errors = {'error_count': 0, 'timeout_count': 0}
    
    # 并发生产者
    async def producer(producer_id):
        local_latencies = []
        for j in range(messages_per_producer):
            msg_start = time.perf_counter()
            try:
                await ref.tell(f'msg-{producer_id}-{j}')
                local_latencies.append(time.perf_counter() - msg_start)
            except Exception as e:
                errors['error_count'] += 1
        return local_latencies
    
    # 启动生产者
    tasks = [producer(i) for i in range(producer_count)]
    results = await asyncio.gather(*tasks)
    
    # 等待消息处理完成
    await asyncio.sleep(2.0)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    stats.update(errors)
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_7_mailbox_full_drop(
    warmup: bool = False,
    mailbox_size: int = 100,
    producer_count: int = 5,
    messages_per_producer: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 7: 邮箱满载 - 丢弃策略
    
    Args:
        warmup: 是否预热
        mailbox_size: 邮箱大小
        producer_count: 生产者数量
        messages_per_producer: 每个生产者的消息数
    """
    if warmup:
        mailbox_size = 10
        producer_count = 2
        messages_per_producer = 10
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=MemoryMailbox)
    ref = await system.spawn(
        SlowActor, 'slow',
        mailbox_size=mailbox_size,
        backpressure_policy=BACKPRESSURE_DROP_NEW
    )
    
    total_attempted = producer_count * messages_per_producer
    latencies = []
    accepted_count = 0
    errors = {'error_count': 0, 'timeout_count': 0}
    
    # 并发生产者
    async def producer(producer_id):
        nonlocal accepted_count
        local_latencies = []
        for j in range(messages_per_producer):
            msg_start = time.perf_counter()
            try:
                # await async tell()；BACKPRESSURE_DROP_NEW 时满邮箱静默丢弃，此处记录 attempted
                await ref.tell(f'msg-{producer_id}-{j}')
                accepted_count += 1
                local_latencies.append(time.perf_counter() - msg_start)
            except Exception as e:
                errors['error_count'] += 1
        return local_latencies
    
    # 启动生产者
    tasks = [producer(i) for i in range(producer_count)]
    results = await asyncio.gather(*tasks)
    
    # 等待消息处理完成
    await asyncio.sleep(2.0)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    stats.update(errors)
    stats['dropped_count'] = total_attempted - accepted_count
    
    await system.shutdown()
    
    return accepted_count, latencies, stats


# ============ 场景 8: 长时间稳定性测试 ============

async def scenario_8_long_running(
    warmup: bool = False,
    duration_sec: int = 60,
    actor_count: int = 50,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 8: 长时间稳定性测试
    
    Args:
        warmup: 是否预热
        duration_sec: 运行时长（秒）
        actor_count: Actor 数量
    """
    if warmup:
        duration_sec = 5
        actor_count = 5
    
    monitor = SystemMonitor(interval=1.0)
    await monitor.start()
    
    system = ActorSystem('bench')
    
    # 创建 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(SlowActor, f'actor-{i}')
        refs.append(ref)
    
    latencies = []
    total_messages = 0
    errors = {'error_count': 0, 'timeout_count': 0}
    
    # 持续运行
    start_time = time.perf_counter()
    
    async def continuous_load(ref, actor_id):
        nonlocal total_messages
        local_latencies = []
        msg_id = 0
        
        while time.perf_counter() - start_time < duration_sec:
            msg_start = time.perf_counter()
            try:
                await ref.ask(f'msg-{actor_id}-{msg_id}', timeout=5.0)
                local_latencies.append(time.perf_counter() - msg_start)
                total_messages += 1
                msg_id += 1
            except asyncio.TimeoutError:
                errors['timeout_count'] += 1
            except Exception as e:
                errors['error_count'] += 1
        
        return local_latencies
    
    # 启动持续负载
    tasks = [continuous_load(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    stats.update(errors)
    
    # 检测内存泄漏
    if len(monitor.samples) > 10:
        initial_memory = statistics.mean(s['memory_mb'] for s in monitor.samples[:10])
        final_memory = statistics.mean(s['memory_mb'] for s in monitor.samples[-10:])
        memory_growth = (final_memory - initial_memory) / initial_memory * 100
        stats['memory_growth_percent'] = memory_growth
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 场景 9: 故障恢复测试 ============

async def scenario_9_fault_recovery(
    warmup: bool = False,
    actor_count: int = 10,
    messages_per_actor: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 9: 故障恢复测试
    
    测试 Actor 崩溃后的恢复能力
    
    Args:
        warmup: 是否预热
        actor_count: Actor 数量
        messages_per_actor: 每个 Actor 的消息数
    """
    if warmup:
        actor_count = 3
        messages_per_actor = 20
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench')
    
    # 创建不稳定 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(FlakyActor, f'flaky-{i}')
        refs.append(ref)
    
    total_attempted = actor_count * messages_per_actor
    latencies = []
    successful_count = 0
    errors = {'error_count': 0, 'timeout_count': 0}
    
    # 发送消息
    async def send_messages(ref, actor_id):
        nonlocal successful_count
        local_latencies = []
        
        for j in range(messages_per_actor):
            msg_start = time.perf_counter()
            try:
                await ref.ask(f'msg-{actor_id}-{j}', timeout=5.0)
                local_latencies.append(time.perf_counter() - msg_start)
                successful_count += 1
            except Exception as e:
                errors['error_count'] += 1
        
        return local_latencies
    
    tasks = [send_messages(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    stats.update(errors)
    stats['success_rate'] = successful_count / total_attempted * 100
    
    await system.shutdown()
    
    return successful_count, latencies, stats


async def scenario_9_cascading_failure(
    warmup: bool = False,
    depth: int = 5,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 9: 级联故障测试
    
    测试多层 Actor 的故障传播
    
    Args:
        warmup: 是否预热
        depth: 层级深度
    """
    if warmup:
        depth = 2
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench')
    
    # 创建层级 Actor
    refs = []
    for i in range(depth):
        ref = await system.spawn(FlakyActor, f'level-{i}')
        refs.append(ref)
    
    latencies = []
    total_messages = 0
    errors = {'error_count': 0, 'timeout_count': 0}
    
    # 层层调用
    for level, ref in enumerate(refs):
        for j in range(20):
            msg_start = time.perf_counter()
            try:
                await ref.ask(f'msg-level{level}-{j}', timeout=5.0)
                total_messages += 1
                latencies.append(time.perf_counter() - msg_start)
            except Exception as e:
                errors['error_count'] += 1
    
    monitor.stop()
    stats = monitor.get_stats()
    stats.update(errors)
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 运行所有极限压力测试 ============

async def run_stress_tests():
    """运行所有极限压力测试"""
    from benchmarks.stress_test_framework import StressTestRunner
    import statistics
    
    runner = StressTestRunner()
    
    print("\n" + "="*60)
    print("极限压力测试（P2）")
    print("="*60)
    
    # 测试 1: 邮箱满载 - 阻塞策略
    print("\n[7.1] 邮箱满载 - 阻塞策略")
    await runner.run_scenario(
        "mailbox_full_block",
        scenario_7_mailbox_full_block,
        mailbox_size=100,
        producer_count=5,
        messages_per_producer=100,
    )
    
    # 测试 2: 邮箱满载 - 丢弃策略
    print("\n[7.2] 邮箱满载 - 丢弃策略")
    await runner.run_scenario(
        "mailbox_full_drop",
        scenario_7_mailbox_full_drop,
        mailbox_size=100,
        producer_count=5,
        messages_per_producer=100,
    )
    
    # 测试 3: 长时间稳定性（快速版）
    print("\n[8.1] 长时间稳定性测试（60秒）")
    await runner.run_scenario(
        "long_running_60s",
        scenario_8_long_running,
        duration_sec=60,
        actor_count=50,
    )
    
    # 测试 4: 故障恢复
    print("\n[9.1] 故障恢复测试")
    await runner.run_scenario(
        "fault_recovery",
        scenario_9_fault_recovery,
        actor_count=10,
        messages_per_actor=100,
    )
    
    # 测试 5: 级联故障
    print("\n[9.2] 级联故障测试")
    await runner.run_scenario(
        "cascading_failure",
        scenario_9_cascading_failure,
        depth=5,
    )
    
    # 保存结果
    runner.save_results("stress_test_results.json")
    
    # 生成报告
    report = runner.generate_report()
    report_file = runner.output_dir / "stress_test_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存到: {report_file}")
    
    # 打印汇总
    runner.print_summary()
    
    return runner


if __name__ == "__main__":
    asyncio.run(run_stress_tests())
