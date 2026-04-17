"""
单 Root 无跨线程通信性能测试

测试场景：
- 单 Root Actor
- 所有消息在同一个 Loop 内处理
- 没有 run_coroutine_threadsafe 开销
"""

import asyncio
import time

from everything_is_an_actor.actor import Actor
from examples.root_loop_system import RootLoopActorSystem
from everything_is_an_actor.system import ActorSystem


class EchoActor(Actor):
    """回声 Actor"""
    async def on_receive(self, msg):
        return msg


async def benchmark_single_loop():
    """单 Loop 基准"""
    print("\n" + "=" * 60)
    print("单 Loop ActorSystem（基准）")
    print("=" * 60)
    
    system = ActorSystem("single-loop")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [1000, 10000, 50000]:
        start = time.perf_counter()
        
        tasks = [ref.ask(f"msg-{i}") for i in range(num_msgs)]
        results = await asyncio.gather(*tasks)
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def benchmark_single_root_no_cross_loop():
    """
    单 Root，无跨 Loop 通信
    
    关键：消息在同一个 Loop 内处理，没有跨线程开销
    """
    print("\n" + "=" * 60)
    print("单 Root RootLoopActorSystem（无跨线程）")
    print("=" * 60)
    
    system = RootLoopActorSystem("single-root")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [1000, 10000, 50000]:
        # 直接在 Loop 内发送消息
        async def send_messages():
            from examples.root_loop_system import _Envelope
            
            # 创建回复队列列表
            reply_queues = []
            for i in range(num_msgs):
                reply_queue = asyncio.Queue()
                env = _Envelope(
                    payload=f"msg-{i}",
                    sender_path=None,
                    reply_queue=reply_queue,
                )
                ref._cell.enqueue(env)
                reply_queues.append(reply_queue)
            
            # 等待所有回复
            results = []
            for q in reply_queues:
                results.append(await q.get())
            return results
        
        start = time.perf_counter()
        
        # 在 Loop 内执行
        future = asyncio.run_coroutine_threadsafe(
            send_messages(),
            ref.loop,
        )
        results = future.result(timeout=60.0)
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def benchmark_single_root_with_cross_loop():
    """单 Root，有跨线程通信（对比）"""
    print("\n" + "=" * 60)
    print("单 Root RootLoopActorSystem（跨线程通信）")
    print("=" * 60)
    
    system = RootLoopActorSystem("single-root-cross")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [1000, 10000, 50000]:
        start = time.perf_counter()
        
        # 使用 system.ask（跨线程）
        futures = [system.ask(ref, f"msg-{i}") for i in range(num_msgs)]
        results = [f.result(timeout=60.0) for f in futures]
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def main():
    print("=" * 60)
    print("单 Root 性能对比")
    print("=" * 60)
    
    await benchmark_single_loop()
    await benchmark_single_root_no_cross_loop()
    await benchmark_single_root_with_cross_loop()
    
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
