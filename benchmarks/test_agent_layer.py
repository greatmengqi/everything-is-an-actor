"""
Agent Layer 性能测试（P1）
- 场景 5: AgentActor 任务处理性能
- 场景 6: 流式输出性能
"""

import asyncio
import time
from typing import List, Tuple, Dict, Any

from everything_is_an_actor.agents import AgentActor, AgentSystem, Task
from benchmarks.monitor import SystemMonitor


# ============ 测试用 Agent ============

class SimpleAgent(AgentActor[str, str]):
    """简单 Agent"""
    async def execute(self, input: str) -> str:
        return f"processed: {input}"


class ProgressAgent(AgentActor[str, str]):
    """带进度报告的 Agent"""
    async def execute(self, input: str) -> str:
        await self.emit_progress("starting")
        await asyncio.sleep(0.001)  # 模拟工作
        await self.emit_progress("processing")
        await asyncio.sleep(0.001)
        await self.emit_progress("done")
        return f"done: {input}"


class StreamingAgent(AgentActor[str, List[str]]):
    """流式输出 Agent"""
    async def execute(self, input: str):
        """生成流式输出"""
        for i in range(10):
            yield f"chunk-{i}"


class OrchestratorAgent(AgentActor[str, Any]):
    """编排 Agent - 测试并发原语"""
    
    async def execute(self, input: str):
        # 使用 sequence 并行执行
        results = await self.context.sequence([
            (SimpleAgent, Task(input=f"task-{i}"))
            for i in range(5)
        ])
        return [r.output for r in results]


# ============ 场景 5: AgentActor 任务处理性能 ============

async def scenario_5_single_agent_ask(
    warmup: bool = False,
    agent_cls=SimpleAgent,
    task_count: int = 10000,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 5: 单 Agent ask 性能测试
    
    Args:
        warmup: 是否预热
        agent_cls: Agent 类型
        task_count: 任务数量
    """
    if warmup:
        task_count = min(task_count, 100)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    ref = await system.spawn(agent_cls, 'test-agent')
    
    latencies = []
    
    # 发送任务
    for i in range(task_count):
        task = Task(input=f"input-{i}")
        msg_start = time.perf_counter()
        result = await ref.ask(task, timeout=10.0)
        latencies.append(time.perf_counter() - msg_start)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return task_count, latencies, stats


async def scenario_5_concurrent_agents(
    warmup: bool = False,
    agent_count: int = 100,
    tasks_per_agent: int = 10,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 5: 多 Agent 并发性能测试
    
    Args:
        warmup: 是否预热
        agent_count: Agent 数量
        tasks_per_agent: 每个 Agent 的任务数
    """
    if warmup:
        agent_count = min(agent_count, 10)
        tasks_per_agent = min(tasks_per_agent, 5)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    
    # 创建多个 Agent
    refs = []
    for i in range(agent_count):
        ref = await system.spawn(SimpleAgent, f'agent-{i}')
        refs.append(ref)
    
    total_tasks = agent_count * tasks_per_agent
    latencies = []
    
    # 并发发送任务
    async def send_tasks(ref, agent_id):
        local_latencies = []
        for j in range(tasks_per_agent):
            task = Task(input=f"input-{agent_id}-{j}")
            msg_start = time.perf_counter()
            await ref.ask(task, timeout=10.0)
            local_latencies.append(time.perf_counter() - msg_start)
        return local_latencies
    
    tasks = [send_tasks(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for local_latencies in results:
        latencies.extend(local_latencies)
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_tasks, latencies, stats


async def scenario_5_orchestration_sequence(
    warmup: bool = False,
    task_count: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 5: sequence 并发原语性能测试
    
    Args:
        warmup: 是否预热
        task_count: 任务数量
    """
    if warmup:
        task_count = min(task_count, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    ref = await system.spawn(OrchestratorAgent, 'orchestrator')
    
    latencies = []
    
    # 发送任务
    for i in range(task_count):
        task = Task(input=f"input-{i}")
        msg_start = time.perf_counter()
        await ref.ask(task, timeout=10.0)
        latencies.append(time.perf_counter() - msg_start)
    
    # 每个 task 内部有 5 个子任务
    total_messages = task_count * 5
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_5_event_streaming(
    warmup: bool = False,
    task_count: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 5: 事件流性能测试
    
    Args:
        warmup: 是否预热
        task_count: 任务数量
    """
    if warmup:
        task_count = min(task_count, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    
    latencies = []
    total_events = 0
    
    # 使用 run() API 测试事件流
    for i in range(task_count):
        msg_start = time.perf_counter()
        
        event_count = 0
        async for event in system.run(ProgressAgent, f"input-{i}"):
            event_count += 1
        
        total_events += event_count
        latencies.append(time.perf_counter() - msg_start)
    
    # 每个 task 产生 3 个进度事件 + 1 个完成事件
    total_messages = total_events
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 场景 6: 流式输出性能 ============

async def scenario_6_streaming_output(
    warmup: bool = False,
    task_count: int = 100,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 6: 流式输出性能测试
    
    Args:
        warmup: 是否预热
        task_count: 任务数量
    """
    if warmup:
        task_count = min(task_count, 10)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    ref = await system.spawn(StreamingAgent, 'streaming-agent')
    
    latencies = []
    total_chunks = 0
    
    # 测试流式输出
    for i in range(task_count):
        task = Task(input=f"input-{i}")
        msg_start = time.perf_counter()
        
        chunk_count = 0
        async for item in ref.ask_stream(task):
            chunk_count += 1
        
        total_chunks += chunk_count
        latencies.append(time.perf_counter() - msg_start)
    
    # 每个 task 产生 10 个 chunks + 1 个 result
    total_messages = total_chunks
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


async def scenario_6_concurrent_streams(
    warmup: bool = False,
    stream_count: int = 10,
    **kwargs
) -> Tuple[int, List[float], Dict[str, Any]]:
    """
    场景 6: 并发流式输出测试
    
    Args:
        warmup: 是否预热
        stream_count: 并发流数量
    """
    if warmup:
        stream_count = min(stream_count, 3)
    
    monitor = SystemMonitor()
    await monitor.start()
    
    system = AgentSystem('bench')
    
    # 创建多个流式 Agent
    refs = []
    for i in range(stream_count):
        ref = await system.spawn(StreamingAgent, f'stream-{i}')
        refs.append(ref)
    
    latencies = []
    total_chunks = 0
    
    # 并发消费流
    async def consume_stream(ref, stream_id):
        task = Task(input=f"input-{stream_id}")
        start = time.perf_counter()
        
        chunk_count = 0
        async for item in ref.ask_stream(task):
            chunk_count += 1
        
        return time.perf_counter() - start, chunk_count
    
    tasks = [consume_stream(ref, i) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks)
    
    for latency, chunk_count in results:
        latencies.append(latency)
        total_chunks += chunk_count
    
    total_messages = total_chunks
    
    monitor.stop()
    stats = monitor.get_stats()
    
    await system.shutdown()
    
    return total_messages, latencies, stats


# ============ 运行所有 Agent Layer 测试 ============

async def run_agent_layer_tests():
    """运行所有 Agent Layer 性能测试"""
    from benchmarks.stress_test_framework import StressTestRunner
    
    runner = StressTestRunner()
    
    print("\n" + "="*60)
    print("Agent Layer 性能测试（P1）")
    print("="*60)
    
    # 测试 1: 单 Agent ask 性能
    print("\n[5.1] 单 Agent ask 性能")
    for task_count in [1000, 5000, 10000]:
        await runner.run_scenario(
            f"single_agent_ask_N={task_count}",
            scenario_5_single_agent_ask,
            agent_cls=SimpleAgent,
            task_count=task_count,
        )
    
    # 测试 2: 多 Agent 并发
    print("\n[5.2] 多 Agent 并发性能")
    for agent_count in [10, 50, 100]:
        await runner.run_scenario(
            f"multi_agent_N={agent_count}",
            scenario_5_concurrent_agents,
            agent_count=agent_count,
            tasks_per_agent=10,
        )
    
    # 测试 3: sequence 并发原语
    print("\n[5.3] sequence 并发原语性能")
    await runner.run_scenario(
        "orchestration_sequence",
        scenario_5_orchestration_sequence,
        task_count=100,
    )
    
    # 测试 4: 事件流
    print("\n[5.4] 事件流性能")
    await runner.run_scenario(
        "event_streaming",
        scenario_5_event_streaming,
        task_count=100,
    )
    
    # 测试 5: 流式输出
    print("\n[6.1] 流式输出性能")
    for task_count in [50, 100, 500]:
        await runner.run_scenario(
            f"streaming_output_N={task_count}",
            scenario_6_streaming_output,
            task_count=task_count,
        )
    
    # 测试 6: 并发流
    print("\n[6.2] 并发流式输出")
    for stream_count in [5, 10, 20]:
        await runner.run_scenario(
            f"concurrent_streams_N={stream_count}",
            scenario_6_concurrent_streams,
            stream_count=stream_count,
        )
    
    # 保存结果
    runner.save_results("agent_layer_results.json")
    
    # 生成报告
    report = runner.generate_report()
    report_file = runner.output_dir / "agent_layer_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存到: {report_file}")
    
    # 打印汇总
    runner.print_summary()
    
    return runner


if __name__ == "__main__":
    asyncio.run(run_agent_layer_tests())
