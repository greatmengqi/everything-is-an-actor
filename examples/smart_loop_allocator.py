"""
智能 Loop 分配策略：最小化跨 Loop 通信
"""

import asyncio
import threading
from typing import Optional


class SmartLoopAllocator:
    """智能事件循环分配器。"""

    def __init__(self, num_loops: int = 4):
        self.num_loops = num_loops
        self._loops: list[asyncio.AbstractEventLoop] = []
        self._threads: list[threading.Thread] = []

        # Actor → Loop 映射
        self._actor_loops: dict[str, int] = {}

        # Loop 负载（actor 数量）
        self._loop_load: list[int] = [0] * num_loops

        # 启动 N 个事件循环
        for i in range(num_loops):
            loop = asyncio.new_event_loop()
            self._loops.append(loop)

            t = threading.Thread(
                target=loop.run_forever,
                name=f"loop-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def assign_loop(
        self,
        actor_name: str,
        parent: Optional[str] = None,
        group: Optional[str] = None,
    ) -> int:
        """
        为 actor 分配 loop。

        策略优先级：
        1. 父 actor 的 loop（父子高频通信）
        2. 同组 actor 的 loop（组内高频通信）
        3. 负载最低的 loop（均衡）
        """
        # 策略 1：继承父 actor 的 loop
        if parent and parent in self._actor_loops:
            loop_index = self._actor_loops[parent]
            self._actor_loops[actor_name] = loop_index
            self._loop_load[loop_index] += 1
            return loop_index

        # 策略 2：同组 actor 的 loop
        if group:
            # 找同组的其他 actor
            for name, idx in self._actor_loops.items():
                if name.startswith(f"{group}/"):
                    self._actor_loops[actor_name] = idx
                    self._loop_load[idx] += 1
                    return idx

        # 策略 3：负载最低的 loop
        loop_index = self._loop_load.index(min(self._loop_load))
        self._actor_loops[actor_name] = loop_index
        self._loop_load[loop_index] += 1
        return loop_index

    def get_loop(self, actor_name: str) -> asyncio.AbstractEventLoop:
        """获取 actor 所在的事件循环。"""
        index = self._actor_loops.get(actor_name)
        if index is None:
            raise ValueError(f"Actor {actor_name} not found")
        return self._loops[index]

    def remove_actor(self, actor_name: str):
        """Actor 停止时更新负载。"""
        if actor_name in self._actor_loops:
            index = self._actor_loops.pop(actor_name)
            self._loop_load[index] -= 1


# ========================================================================
# 使用示例
# ========================================================================

"""
# 场景 1：父子 actor 自动同 loop

system = SmartLoopAllocator(num_loops=4)

# 创建父 actor
parent_loop = allocator.assign_loop("agent-1")

# 创建子 actor（自动继承父 actor 的 loop）
child_loop = allocator.assign_loop("agent-1/tool-1", parent="agent-1")
# ↑ child_loop == parent_loop

# 结果：父子通信零开销


# 场景 2：同组 actor 同 loop

# 组 A
allocator.assign_loop("group-A/worker-1", group="group-A")  # → Loop 0
allocator.assign_loop("group-A/worker-2", group="group-A")  # → Loop 0
allocator.assign_loop("group-A/worker-3", group="group-A")  # → Loop 0

# 组 B
allocator.assign_loop("group-B/worker-1", group="group-B")  # → Loop 1
allocator.assign_loop("group-B/worker-2", group="group-B")  # → Loop 1

# 结果：组内通信零开销，组间通信跨 loop


# 场景 3：无关联 actor 均衡分配

allocator.assign_loop("standalone-1")  # → Loop 0（负载最低）
allocator.assign_loop("standalone-2")  # → Loop 1（负载最低）
allocator.assign_loop("standalone-3")  # → Loop 2（负载最低）
allocator.assign_loop("standalone-4")  # → Loop 3（负载最低）
"""


# ========================================================================
# 通信开销对比
# ========================================================================

COMMUNICATION_OVERHEAD = """
┌────────────────────────────────────────────────────────────┐
│                    通信开销对比                             │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  同 Loop 通信：                                             │
│    await ref.tell(msg)                                     │
│    ↓                                                       │
│    直接调用 _cell.enqueue()                                 │
│    开销：~1μs                                               │
│                                                            │
│  跨 Loop 通信：                                             │
│    await ref.tell(msg)                                     │
│    ↓                                                       │
│    asyncio.run_coroutine_threadsafe(                       │
│        _cell.enqueue(msg),                                 │
│        target_loop                                         │
│    )                                                       │
│    ↓                                                       │
│    await asyncio.wrap_future(future)                       │
│    开销：~50μs                                              │
│                                                            │
│  比例：跨 Loop 慢 50 倍                                      │
└────────────────────────────────────────────────────────────┘
"""


# ========================================================================
# 最佳实践
# ========================================================================

"""
1. 父子 actor 自动同 loop（已实现）

   class AgentActor(Actor):
       async def on_receive(self, msg):
           # 子 actor 自动继承父 actor 的 loop
           child = await self.context.spawn(ToolActor, "tool")
           # ↑ 同 loop，零开销


2. 高频通信的 actor 显式分组

   # 创建组
   system.spawn("pipeline/step-1", Step1, group="pipeline")
   system.spawn("pipeline/step-2", Step2, group="pipeline")
   system.spawn("pipeline/step-3", Step3, group="pipeline")
   # ↑ 同 loop，流水线通信零开销


3. 低频跨组通信可接受

   # 组间通信频率低，50μs 可接受
   result = await other_group_actor.ask(msg)


4. 避免过度跨 loop

   # ❌ 错误：每个 actor 都跨 loop 通信
   for i in range(100):
       actor = system.spawn(f"worker-{i}", Worker)
       await actor.ask(task)  # 每次都跨 loop

   # ✅ 正确：同组 actor 批量处理
   for i in range(25):
       for j in range(4):
           actor = system.spawn(f"group-{i}/worker-{j}", Worker, group=f"group-{i}")
"""
