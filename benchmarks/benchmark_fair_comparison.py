"""
公平对比测试

确保两个系统使用相同的测试方法
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


async def benchmark_actor_system_internal():
    """
    ActorSystem：在 Loop 内部测试（公平对比）
    """
    print("\n" + "=" * 60)
    print("ActorSystem（Loop 内部测试）")
    print("=" * 60)
    
    system = ActorSystem("test")
    ref = await system.spawn(EchoActor, "echo")
    
    # 获取 Actor 的 Cell
    cell = system._root_cells["echo"]
    
    for num_msgs in [1000, 10000, 50000]:
        async def test():
            # 直接在 Loop 内发送消息
            from everything_is_an_actor.ref import _Envelope, _ReplyMessage
            
            results = []
            for i in range(num_msgs):
                # 直接调用 cell 的处理逻辑
                result = await cell._receive_chain(None, f"msg-{i}")
                results.append(result)
            return results
        
        start = time.perf_counter()
        results = await test()
        duration = time.perf_counter() - start
        
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def benchmark_root_loop_internal():
    """
    RootLoopActorSystem：在 Loop 内部测试
    """
    print("\n" + "=" * 60)
    print("RootLoopActorSystem（Loop 内部测试）")
    print("=" * 60)
    
    system = RootLoopActorSystem("test")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [1000, 10000, 50000]:
        async def test():
            from examples.root_loop_system import _Envelope
            
            # 创建回复队列
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
        future = asyncio.run_coroutine_threadsafe(test(), ref.loop)
        results = future.result(timeout=60.0)
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def benchmark_actor_system_normal():
    """
    ActorSystem：正常 ask（有完整流程）
    """
    print("\n" + "=" * 60)
    print("ActorSystem（正常 ask）")
    print("=" * 60)
    
    system = ActorSystem("test")
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


async def benchmark_root_loop_normal():
    """
    RootLoopActorSystem：正常 ask（有跨线程）
    """
    print("\n" + "=" * 60)
    print("RootLoopActorSystem（正常 ask，跨线程）")
    print("=" * 60)
    
    system = RootLoopActorSystem("test")
    ref = await system.spawn(EchoActor, "echo")
    
    for num_msgs in [1000, 10000, 50000]:
        start = time.perf_counter()
        
        futures = [system.ask(ref, f"msg-{i}") for i in range(num_msgs)]
        results = [f.result(timeout=60.0) for f in futures]
        
        duration = time.perf_counter() - start
        msg_per_sec = num_msgs / duration
        latency_us = (duration / num_msgs) * 1_000_000
        
        print(f"  {num_msgs:>6} msgs: {duration*1000:>8.1f}ms, {msg_per_sec:>10.0f} msg/sec, {latency_us:.2f}us")
    
    await system.shutdown()


async def main():
    print("=" * 60)
    print("公平对比测试")
    print("=" * 60)
    
    await benchmark_actor_system_normal()
    await benchmark_root_loop_normal()
    
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
