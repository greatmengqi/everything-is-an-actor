"""
基础性能测试（P0）
- 场景 1: 单 Actor tell/ask 吞吐量
- 场景 2: 多 Actor 并发性能
"""

import asyncio
import time
from typing import List, Tuple, Dict, Any

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.mailbox import MemoryMailbox, FastMailbox, ThreadedMailbox
from benchmarks.monitor import SystemMonitor


# ============ 测试用 Actor ============

class SyncEchoActor(Actor):
    """同步回声 Actor"""
    def receive(self, message):
        return message


class AsyncEchoActor(Actor):
    """异步回声 Actor"""
    async def on_receive(self, message):
        return message


# ============ 场景 1: 单 Actor tell/ask 吞吐量 ============

async def scenario_1_single_actor_tell(
    warmup: bool = False,
    mailbox_cls=FastMailbox,
    actor_cls=AsyncEchoActor,
    message_count: int = 100000,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 1: 单 Actor tell 吞吐量测试
    
    Args:
        warmup: 是否预热
        mailbox_cls: 邮箱类型
        actor_cls: Actor 类型
        message_count: 消息数量
    """
    if warmup:
        message_count = min(message_count, 1000)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=mailbox_cls)
    ref = await system.spawn(actor_cls, 'echo', mailbox_size=message_count + 1000)
    
    latencies = []
    start_time = time.perf_counter()
    
    # 发送消息
    for i in range(message_count):
        msg_start = time.perf_counter()
        await ref.tell(f'msg-{i}')
        latencies.append(time.perf_counter() - msg_start)
    
    # 等待消息处理完成（通过 ask 一个特殊消息来确认）
    await ref.ask('__sync__', timeout=10.0)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return message_count, latencies, stats


async def scenario_1_single_actor_ask(
    warmup: bool = False,
    mailbox_cls=FastMailbox,
    actor_cls=AsyncEchoActor,
    message_count: int = 10000,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 1: 单 Actor ask 吞吐量测试
    
    Args:
        warmup: 是否预热
        mailbox_cls: 邮箱类型
        actor_cls: Actor 类型
        message_count: 消息数量
    """
    if warmup:
        message_count = min(message_count, 100)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=mailbox_cls)
    ref = await system.spawn(actor_cls, 'echo')
    
    latencies = []
    
    # 发送消息并等待回复
    for i in range(message_count):
        msg_start = time.perf_counter()
        await ref.ask(f'msg-{i}', timeout=5.0)
        latencies.append(time.perf_counter() - msg_start)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return message_count, latencies, stats


# ============ 场景 2: 多 Actor 并发性能 ============

async def scenario_2_multi_actor_concurrent(
    warmup: bool = False,
    mailbox_cls=FastMailbox,
    actor_cls=AsyncEchoActor,
    actor_count: int = 100,
    messages_per_actor: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 2: 多 Actor 并发性能测试
    
    Args:
        warmup: 是否预热
        mailbox_cls: 邮箱类型
        actor_cls: Actor 类型
        actor_count: Actor 数量
        messages_per_actor: 每个 Actor 的消息数
    """
    if warmup:
        actor_count = min(actor_count, 10)
        messages_per_actor = min(messages_per_actor, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=mailbox_cls)
    
    # 创建多个 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(actor_cls, f'echo-{i}', mailbox_size=messages_per_actor + 100)
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
    
    # 并发执行
    tasks = [send_messages(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    # 合并延迟数据
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_2_multi_actor_tell(
    warmup: bool = False,
    mailbox_cls=FastMailbox,
    actor_cls=AsyncEchoActor,
    actor_count: int = 100,
    messages_per_actor: int = 1000,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 2: 多 Actor tell 性能测试
    
    Args:
        warmup: 是否预热
        mailbox_cls: 邮箱类型
        actor_cls: Actor 类型
        actor_count: Actor 数量
        messages_per_actor: 每个 Actor 的消息数
    """
    if warmup:
        actor_count = min(actor_count, 10)
        messages_per_actor = min(messages_per_actor, 100)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = ActorSystem('bench', mailbox_cls=mailbox_cls)
    
    # 创建多个 Actor
    refs = []
    for i in range(actor_count):
        ref = await system.spawn(actor_cls, f'echo-{i}', mailbox_size=messages_per_actor * 2)
        refs.append(ref)
    
    total_messages = actor_count * messages_per_actor
    latencies = []
    
    # 批量发送消息（不等待）
    start_time = time.perf_counter()
    
    for i, ref in enumerate(refs):
        for j in range(messages_per_actor):
            msg_start = time.perf_counter()
            await ref.tell(f'msg-{i}-{j}')
            latencies.append(time.perf_counter() - msg_start)
    
    # 等待所有消息处理完成
    await asyncio.sleep(0.5)
    
    # 同步确认
    await asyncio.gather(*[ref.ask('__sync__', timeout=10.0) for ref in refs])
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 运行所有基础测试 ============

async def run_basic_performance_tests():
    """运行所有基础性能测试"""
    from benchmarks.stress_test_framework import StressTestRunner
    
    runner = StressTestRunner()
    
    print("\n" + "="*60)
    print("基础性能测试（P0）")
    print("="*60)
    
    # 测试 1: 单 Actor tell - 不同邮箱
    print("\n[1.1] 单 Actor tell - 邮箱对比")
    for mailbox_cls, name in [(MemoryMailbox, 'Memory'), (FastMailbox, 'Fast'), (ThreadedMailbox, 'Threaded')]:
        await runner.run_scenario(
            f"tell_{name}_AsyncActor",
            scenario_1_single_actor_tell,
            mailbox_cls=mailbox_cls,
            actor_cls=AsyncEchoActor,
            message_count=100000,
        )
    
    # 测试 2: 单 Actor tell - 同步 vs 异步
    print("\n[1.2] 单 Actor tell - 同步 vs 异步")
    for actor_cls, name in [(SyncEchoActor, 'Sync'), (AsyncEchoActor, 'Async')]:
        await runner.run_scenario(
            f"tell_FastMailbox_{name}Actor",
            scenario_1_single_actor_tell,
            mailbox_cls=FastMailbox,
            actor_cls=actor_cls,
            message_count=100000,
        )
    
    # 测试 3: 单 Actor ask - 不同邮箱
    print("\n[1.3] 单 Actor ask - 邮箱对比")
    for mailbox_cls, name in [(MemoryMailbox, 'Memory'), (FastMailbox, 'Fast'), (ThreadedMailbox, 'Threaded')]:
        await runner.run_scenario(
            f"ask_{name}_AsyncActor",
            scenario_1_single_actor_ask,
            mailbox_cls=mailbox_cls,
            actor_cls=AsyncEchoActor,
            message_count=10000,
        )
    
    # 测试 4: 多 Actor 并发 - 不同规模
    print("\n[2.1] 多 Actor 并发 ask - 规模对比")
    for actor_count in [10, 100, 1000]:
        await runner.run_scenario(
            f"multi_actor_ask_N={actor_count}",
            scenario_2_multi_actor_concurrent,
            mailbox_cls=FastMailbox,
            actor_cls=AsyncEchoActor,
            actor_count=actor_count,
            messages_per_actor=100,
        )
    
    # 测试 5: 多 Actor tell
    print("\n[2.2] 多 Actor tell - 规模对比")
    for actor_count in [10, 100, 1000]:
        await runner.run_scenario(
            f"multi_actor_tell_N={actor_count}",
            scenario_2_multi_actor_tell,
            mailbox_cls=FastMailbox,
            actor_cls=AsyncEchoActor,
            actor_count=actor_count,
            messages_per_actor=1000,
        )
    
    # 保存结果
    runner.save_results("basic_performance_results.json")
    
    # 生成报告
    report = runner.generate_report()
    report_file = runner.output_dir / "basic_performance_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存到: {report_file}")
    
    # 打印汇总
    runner.print_summary()
    
    return runner


if __name__ == "__main__":
    asyncio.run(run_basic_performance_tests())
