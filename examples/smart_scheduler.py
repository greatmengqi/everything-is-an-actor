"""
智能 Loop 调度器（概念原型）

⚠️ 这是独立的概念验证，尚未集成到生产系统中。
展示三层调度思路：静态分析 → 动态调整 → 模式学习。

如需多 loop 调度，生产环境请使用：
    from everything_is_an_actor.core.unified_system import ActorSystem, ActorSystemMode
    system = ActorSystem(mode=ActorSystemMode.MULTI_LOOP)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from everything_is_an_actor.core.system import _ActorCell

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class LoopStats:
    """事件循环统计信息。"""
    index: int
    actor_count: int = 0
    msg_per_sec: float = 0.0
    cpu_percent: float = 0.0
    cross_loop_comm_rate: float = 0.0  # 跨 Loop 通信比例
    actors: List[str] = field(default_factory=list)


@dataclass
class ActorCommStats:
    """Actor 通信统计。"""
    name: str
    loop_index: int
    
    # 通信目标 → 次数
    outbound: Dict[str, int] = field(default_factory=dict)
    inbound: Dict[str, int] = field(default_factory=dict)
    
    # 跨 Loop 通信次数
    cross_loop_count: int = 0
    total_count: int = 0
    
    @property
    def cross_loop_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.cross_loop_count / self.total_count


# ============================================================================
# 智能调度器
# ============================================================================

class SmartLoopScheduler:
    """
    智能 Loop 调度器。
    
    三层调度：
    1. 静态分析（创建时）
    2. 动态调整（运行时）
    3. 模式学习（长期优化）
    """
    
    def __init__(
        self,
        num_loops: int,
        rebalance_interval: float = 60.0,  # 60秒检查一次
        load_threshold: float = 1.5,  # 负载不均阈值
    ):
        self.num_loops = num_loops
        self.rebalance_interval = rebalance_interval
        self.load_threshold = load_threshold
        
        # Actor → Loop 映射
        self._actor_loops: Dict[str, int] = {}
        
        # Loop 负载
        self._loop_loads: List[int] = [0] * num_loops
        
        # 通信统计
        self._comm_stats: Dict[str, ActorCommStats] = {}
        
        # 分组信息
        self._groups: Dict[str, Set[str]] = defaultdict(set)
        
        # 父子关系
        self._parent_child: Dict[str, str] = {}  # child → parent
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 运行时任务
        self._rebalance_task: Optional[asyncio.Task] = None
    
    # ========================================================================
    # Layer 1: 静态分析（创建时决策）
    # ========================================================================
    
    def assign_loop(
        self,
        actor_name: str,
        parent: Optional[str] = None,
        group: Optional[str] = None,
        communicates_with: Optional[Set[str]] = None,
    ) -> int:
        """
        分配 Loop 给 Actor。
        
        优先级：
        1. 父子关系（权重 10）→ 必须同 Loop
        2. 分组关系（权重 8）→ 应该同 Loop
        3. 通信模式（权重 6）→ 聚类到同 Loop
        4. 负载均衡（权重 4）→ 分散到低负载 Loop
        
        返回：Loop 索引
        """
        with self._lock:
            # 策略 1：父子继承（最高优先级）
            if parent and parent in self._actor_loops:
                loop_index = self._actor_loops[parent]
                self._bind_actor(actor_name, loop_index)
                self._parent_child[actor_name] = parent
                return loop_index
            
            # 策略 2：分组分配
            if group:
                # 查找组内其他 actor 的 loop
                group_actors = self._groups.get(group, set())
                if group_actors:
                    # 组内已有 actor，使用相同的 loop
                    sample_actor = next(iter(group_actors))
                    loop_index = self._actor_loops[sample_actor]
                    self._bind_actor(actor_name, loop_index)
                    self._groups[group].add(actor_name)
                    return loop_index
                else:
                    # 组内第一个 actor，选择负载最低的 loop
                    loop_index = self._find_least_loaded_loop()
                    self._bind_actor(actor_name, loop_index)
                    self._groups[group].add(actor_name)
                    return loop_index
            
            # 策略 3：通信模式聚类
            if communicates_with:
                loop_index = self._cluster_by_communication(
                    actor_name,
                    communicates_with,
                )
                self._bind_actor(actor_name, loop_index)
                return loop_index
            
            # 策略 4：负载均衡
            loop_index = self._find_least_loaded_loop()
            self._bind_actor(actor_name, loop_index)
            return loop_index
    
    def _cluster_by_communication(
        self,
        actor_name: str,
        communicates_with: Set[str],
    ) -> int:
        """
        基于通信目标聚类。
        
        找出与哪些 actor 通信最频繁，分配到相同的 loop。
        """
        # 统计每个 loop 的通信权重
        loop_weights: Dict[int, int] = defaultdict(int)
        
        for target in communicates_with:
            if target in self._actor_loops:
                target_loop = self._actor_loops[target]
                # 权重 = 通信频率（从历史数据获取）
                weight = self._get_comm_frequency(actor_name, target)
                loop_weights[target_loop] += weight
        
        if loop_weights:
            # 选择权重最高的 loop
            best_loop = max(loop_weights.items(), key=lambda x: x[1])[0]
            return best_loop
        
        # 没有通信历史，负载均衡
        return self._find_least_loaded_loop()
    
    def _find_least_loaded_loop(self) -> int:
        """找到负载最低的 Loop。"""
        return self._loop_loads.index(min(self._loop_loads))
    
    def _bind_actor(self, actor_name: str, loop_index: int):
        """绑定 Actor 到 Loop。"""
        self._actor_loops[actor_name] = loop_index
        self._loop_loads[loop_index] += 1
        
        # 初始化通信统计
        if actor_name not in self._comm_stats:
            self._comm_stats[actor_name] = ActorCommStats(
                name=actor_name,
                loop_index=loop_index,
            )
    
    # ========================================================================
    # Layer 2: 动态调整（运行时优化）
    # ========================================================================
    
    async def start_rebalance_loop(self):
        """启动定期重平衡任务。"""
        self._rebalance_task = asyncio.create_task(self._rebalance_loop())
    
    async def _rebalance_loop(self):
        """定期检查并调整 Actor 分布。"""
        while True:
            await asyncio.sleep(self.rebalance_interval)
            
            try:
                await self._check_and_rebalance()
            except Exception as e:
                # 记录错误，但不中断循环
                print(f"Rebalance error: {e}")
    
    async def _check_and_rebalance(self):
        """检查并执行重平衡。"""
        stats = self._collect_loop_stats()
        
        # 计算平均负载
        avg_load = sum(s.actor_count for s in stats) / len(stats)
        
        if avg_load == 0:
            return  # 没有 actor
        
        # 找出过载和空闲的 loop
        overloaded = [
            s for s in stats
            if s.actor_count > avg_load * self.load_threshold
        ]
        underloaded = [
            s for s in stats
            if s.actor_count < avg_load / self.load_threshold
        ]
        
        if not overloaded or not underloaded:
            return  # 负载均衡，无需调整
        
        # 执行迁移
        for src in overloaded:
            for dst in underloaded:
                await self._migrate_actors(src, dst)
    
    def _collect_loop_stats(self) -> List[LoopStats]:
        """收集各 Loop 的统计信息。"""
        stats = []
        
        for i in range(self.num_loops):
            actors = [
                name for name, loop in self._actor_loops.items()
                if loop == i
            ]
            
            # 计算跨 Loop 通信率
            cross_loop_total = 0
            comm_total = 0
            for actor_name in actors:
                if actor_name in self._comm_stats:
                    s = self._comm_stats[actor_name]
                    cross_loop_total += s.cross_loop_count
                    comm_total += s.total_count
            
            cross_loop_rate = (
                cross_loop_total / comm_total if comm_total > 0 else 0.0
            )
            
            stats.append(LoopStats(
                index=i,
                actor_count=len(actors),
                cross_loop_comm_rate=cross_loop_rate,
                actors=actors,
            ))
        
        return stats
    
    async def _migrate_actors(
        self,
        src: LoopStats,
        dst: LoopStats,
        max_migrations: int = 3,
    ):
        """
        迁移 Actor 从 src Loop 到 dst Loop。
        
        选择标准：
        1. 跨 Loop 通信率低（迁移后影响小）
        2. 不是其他 actor 的父节点（避免破坏父子关系）
        3. 不在重要分组中
        """
        # 筛选可迁移的 actor
        candidates = []
        
        for actor_name in src.actors:
            if actor_name not in self._comm_stats:
                continue
            
            s = self._comm_stats[actor_name]
            
            # 排除条件
            if actor_name in self._parent_child.values():
                # 是其他 actor 的父节点，不迁移
                continue
            
            if any(
                actor_name in self._groups.get(g, set())
                and len(self._groups[g]) > 1
                for g in self._groups
            ):
                # 在重要分组中，不迁移
                continue
            
            # 计算迁移得分（跨 Loop 通信率越低越好）
            score = 1.0 - s.cross_loop_rate
            candidates.append((actor_name, score))
        
        # 按得分排序
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # 执行迁移
        for actor_name, _ in candidates[:max_migrations]:
            await self._migrate_actor(actor_name, dst.index)
    
    async def _migrate_actor(self, actor_name: str, new_loop: int):
        """
        迁移单个 Actor。
        
        注意：这是一个简化的实现，实际需要：
        1. 停止 actor
        2. 保存状态
        3. 在新 loop 重建
        4. 恢复状态
        """
        with self._lock:
            old_loop = self._actor_loops.get(actor_name)
            if old_loop is None:
                return
            
            # 更新映射
            self._actor_loops[actor_name] = new_loop
            self._loop_loads[old_loop] -= 1
            self._loop_loads[new_loop] += 1
            
            # 更新通信统计
            if actor_name in self._comm_stats:
                self._comm_stats[actor_name].loop_index = new_loop
            
            logger.info("Migrated %s from loop %d to %d", actor_name, old_loop, new_loop)
    
    # ========================================================================
    # Layer 3: 通信模式学习
    # ========================================================================
    
    def record_communication(
        self,
        from_actor: str,
        to_actor: str,
        is_cross_loop: bool,
    ):
        """
        记录一次通信（用于模式学习）。
        
        参数：
        - from_actor: 发送方
        - to_actor: 接收方
        - is_cross_loop: 是否跨 Loop
        """
        with self._lock:
            # 更新发送方统计
            if from_actor not in self._comm_stats:
                self._comm_stats[from_actor] = ActorCommStats(
                    name=from_actor,
                    loop_index=self._actor_loops.get(from_actor, 0),
                )
            
            sender_stats = self._comm_stats[from_actor]
            sender_stats.outbound[to_actor] = \
                sender_stats.outbound.get(to_actor, 0) + 1
            sender_stats.total_count += 1
            
            if is_cross_loop:
                sender_stats.cross_loop_count += 1
            
            # 更新接收方统计
            if to_actor not in self._comm_stats:
                self._comm_stats[to_actor] = ActorCommStats(
                    name=to_actor,
                    loop_index=self._actor_loops.get(to_actor, 0),
                )
            
            receiver_stats = self._comm_stats[to_actor]
            receiver_stats.inbound[from_actor] = \
                receiver_stats.inbound.get(from_actor, 0) + 1
    
    def _get_comm_frequency(self, from_actor: str, to_actor: str) -> int:
        """获取两个 actor 之间的通信频率。"""
        if from_actor in self._comm_stats:
            return self._comm_stats[from_actor].outbound.get(to_actor, 0)
        return 0
    
    def get_communication_clusters(self) -> List[Set[str]]:
        """
        基于通信模式聚类 actor。
        
        返回：应该分配到同一 Loop 的 actor 组
        """
        # 构建通信图
        from collections import defaultdict
        
        graph: Dict[str, Set[str]] = defaultdict(set)
        
        for actor_name, stats in self._comm_stats.items():
            # 高频通信目标
            for target, count in stats.outbound.items():
                if count > stats.total_count * 0.1:  # > 10% 通信
                    graph[actor_name].add(target)
                    graph[target].add(actor_name)
        
        # 找连通分量（简化版聚类）
        visited = set()
        clusters = []
        
        for actor in graph:
            if actor in visited:
                continue
            
            # BFS 找连通分量
            cluster = set()
            queue = [actor]
            
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                
                visited.add(current)
                cluster.add(current)
                
                for neighbor in graph[current]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            
            if len(cluster) > 1:
                clusters.append(cluster)
        
        return clusters
    
    # ========================================================================
    # 工具方法
    # ========================================================================
    
    def remove_actor(self, actor_name: str):
        """移除 Actor（停止时调用）。"""
        with self._lock:
            if actor_name in self._actor_loops:
                loop_index = self._actor_loops.pop(actor_name)
                self._loop_loads[loop_index] -= 1

            # 从分组中移除
            for group in self._groups.values():
                group.discard(actor_name)

            # 从父子关系中移除
            self._parent_child.pop(actor_name, None)

            # 清理通信统计，防止无界内存增长
            self._comm_stats.pop(actor_name, None)
    
    def get_loop_for_actor(self, actor_name: str) -> Optional[int]:
        """获取 Actor 所在的 Loop。"""
        return self._actor_loops.get(actor_name)
    
    def print_stats(self):
        """打印统计信息。"""
        stats = self._collect_loop_stats()
        
        print("\n=== Loop Stats ===")
        for s in stats:
            print(f"Loop {s.index}:")
            print(f"  Actors: {s.actor_count}")
            print(f"  Cross-loop comm rate: {s.cross_loop_comm_rate:.2%}")
            print(f"  Sample actors: {s.actors[:5]}")
        
        # 通信聚类
        clusters = self.get_communication_clusters()
        if clusters:
            print("\n=== Communication Clusters ===")
            for i, cluster in enumerate(clusters):
                print(f"Cluster {i}: {cluster}")


# ============================================================================
# 使用示例
# ============================================================================

"""
# 创建调度器
scheduler = SmartLoopScheduler(num_loops=4)

# 场景 1：父子 actor（自动同 Loop）
parent_loop = scheduler.assign_loop("parent", parent=None)
child_loop = scheduler.assign_loop("child", parent="parent")
assert parent_loop == child_loop  # ✓ 同 Loop

# 场景 2：分组 actor（同组同 Loop）
scheduler.assign_loop("pipeline/step-1", group="pipeline")
scheduler.assign_loop("pipeline/step-2", group="pipeline")
scheduler.assign_loop("pipeline/step-3", group="pipeline")
# ✓ 都在同一 Loop

# 场景 3：基于通信模式
scheduler.assign_loop(
    "orchestrator",
    communicates_with={"worker-1", "worker-2", "worker-3"},
)
# ✓ 会分配到与 worker 通信最多的 Loop

# 运行时监控
scheduler.print_stats()

# 输出：
# === Loop Stats ===
# Loop 0:
#   Actors: 5
#   Cross-loop comm rate: 12.5%
#   Sample actors: ['parent', 'child', ...]
# Loop 1:
#   Actors: 3
#   Cross-loop comm rate: 45.2%
#   ...
#
# === Communication Clusters ===
# Cluster 0: {'orchestrator', 'worker-1', 'worker-2'}
# Cluster 1: {'service-a', 'service-b'}
"""
