# Rust Agent Runtime — Design Document

> AI Agent 分布式运行时。Serverless 上的有状态 agent 调度与通信基础设施。

## 1. 定位

**一句话**：Rust 实现的 AI Agent 分布式运行时——调度和通信基础设施。

**不是什么**：
- 不是通用 actor 框架（不跟 Akka/Actix 竞争）
- 不是 AI agent 编排引擎（不管 agent 怎么 think/act/observe）
- 不是 workflow engine（不管步骤编排）

**是什么**：
- 一个分布式 actor 运行时，为 AI agent 的调度和通信提供基础设施
- Actor 模型是实现手段，对最终用户不可见
- 运行时对 agent 是黑盒——不碰状态，不懂语义

### 两条路线

本项目有两种落地方式，共享同一个 Rust runtime core（Phase 0-2），在 Phase 3 之后分叉：

**路线 A — 企业私有化**

定位：企业内部 AI agent 基础设施，不依赖外部供应商。

| 维度 | 选择 |
|------|------|
| 许可证 | 内部使用，不涉及开源许可 |
| SDK | 只做 Python（PyO3 直接绑定，不需要 gRPC） |
| 通用性 | 专为 AI agent 场景深度定制 |
| 集成 | 直接对接内部 RPC / 消息队列 / 监控 |
| 部署 | 嵌入式 library 模式 |
| scope | 小，1-2 人 + AI 辅助可行 |

动机：关键基础设施不能依赖外部供应商。Restate（BSL）生产环境需要授权、不可深度定制、法务合规风险。Dapr（Go）性能天花板、无 supervision、无背压。

**路线 B — 开源产品**

定位：开放的、多语言的、可私有化部署的 Durable Objects，专为 AI Agent 场景设计。

| 维度 | 选择 |
|------|------|
| 许可证 | Apache 2.0 / MIT |
| SDK | Python / Java / Go / Rust native |
| 通用性 | 通用 agent runtime，服务各种场景 |
| 集成 | 标准接口（gRPC） |
| 部署 | 嵌入式 + sidecar + 独立集群 |
| scope | 大，需要更多人力和生态建设 |

动机：市场上不存在真正开源的、Rust 实现的、有 supervision + 背压的分布式 actor runtime。Restate 和 Akka 都是 BSL。

### 竞品定位（详见 §9）

**共同的差异化切入点**（两条路线共享）：
1. Rust 实现——无 GC，可预测延迟，高 actor 密度
2. Supervision 树 + Stream 背压——Dapr / Restate / Durable Objects 都没有
3. 为 AI Agent 的 I/O pattern 优化（高延迟外部调用、突发消息、长尾 session）

**路线 B 额外的差异化**：
4. **真正开源**（Apache 2.0 / MIT）——Restate 和 Akka 都是 BSL
5. 多语言 SDK
6. 可私有化部署，不绑定云厂商

## 2. 运行时职责边界

### 运行时管的

| 职责 | 说明 |
|------|------|
| **寻址** | actor 在哪个节点，location-transparent |
| **投递** | 消息从 A 到 B，可靠送达 |
| **生命周期** | 虚拟 actor 激活 / 钝化 / 重启 |
| **调度** | 谁先跑、跑在哪、跑多久 |
| **背压** | 慢消费者保护，防止 mailbox 无限堆积 |
| **故障恢复** | supervision 策略、节点故障后 actor 迁移 |
| **可观测性** | 分布式 tracing、mailbox 深度、调度延迟 |

### 运行时不管的

| 不管 | 由谁管 |
|------|--------|
| Agent 内部状态 | 业务代码 |
| 推理 / 规划逻辑 | 业务代码 |
| 工具调用编排 | 业务代码 |
| 状态持久化 | 业务代码（运行时只提供 on_activate/on_deactivate 钩子） |
| 动态拓扑恢复 | 业务代码（声明式 actor 由代码重建，虚拟 actor 按需激活） |
| 消息序列化格式 | SDK 层 |

## 3. 核心设计决策

### 已定

**3.1 消息是 bytes，不是泛型**

运行时不理解消息内容。消息 = `Vec<u8>` + metadata（sender, trace_id, correlation_id）。
序列化/反序列化是 SDK 层的职责。

理由：
- 运行时不需要泛型，避免了 Rust 中"统一 ActorRef 泛型"的经典难题
- ActorRef 就是一个地址，没有类型参数
- 多语言 SDK 可以各自选择序列化格式（protobuf / msgpack / JSON）

**3.2 虚拟 Actor 模型（Orleans 风格）**

两种 actor 共存：

| 类型 | 生命周期 | 恢复方式 | 用途 |
|------|---------|---------|------|
| **声明式 actor** | 代码写死，跟进程同生死 | 进程启动时代码重建 | 基础设施（Gateway、Router、RateLimiter） |
| **虚拟 actor** | 按需激活，空闲钝化 | 不恢复，消息来了自动激活 | 业务实体（ChatSession、AgentTask） |

虚拟 actor 的关键设计决策（参考 Orleans）：

- **扁平结构**：虚拟 actor 之间没有父子关系，按 ID 寻址，互相平等
- **不做动态拓扑恢复**：进程重启后不尝试恢复上一世的 actor 树。虚拟 actor 靠消息触发重新激活，声明式 actor 靠代码重建
- **状态恢复是业务的事**：运行时只保证 on_activate（on_started）和 on_deactivate（on_stopped）被调用。在这两个钩子里加载/保存什么状态，由业务决定
- **Supervision 限于单次激活内**：actor 内部 on_receive 异常 → supervision 策略决定重启/停止。不跨进程重启恢复 supervision 树

不做动态拓扑恢复的原因：
- 动态拓扑恢复需要 journal 或 event sourcing，与"运行时不管状态"矛盾
- 盲目 replay spawn 序列可能比不恢复更危险（任务可能已完成、依赖关系可能变化）
- Orleans 验证了扁平模型在大规模虚拟 actor 场景下完全可行（Halo、Azure PlayFab）

ActorContext.spawn() 保留给**临时性子 actor**（跑完即销毁，不需要跨重启恢复）。长期存在的 actor 全部走 VirtualActorRegistry。

**注册表后端可插拔**：默认内存（进程内有效），可替换为持久化后端（Redis/DB），用于：
- 进程重启后查询"哪些虚拟 actor 存在过"（`known_ids()`）
- 主动推送场景：定时任务、广播通知需要知道激活过哪些 actor

**3.3 分布式优先设计**

从分布式开始设计，单机是特例（所有 actor 碰巧在同一个节点）。

ActorRef 内部区分 local / remote：

```
Local  → 直接投递到 mailbox（零拷贝，Arc 传递）
Remote → 序列化 → 网络传输 → 反序列化 → 投递
```

调用方无感知，location-transparent。

Actor 放置协调使用轻量级 Raft 共识（`openraft`），per shard range。
激活请求先过 Raft leader 确认分配，保证虚拟 actor 唯一性。
Raft 只在 shard 分配和再平衡时参与，不在每条消息的 hot path 上。

**3.4 虚拟 Actor 生命周期**

虚拟 actor 按需激活、空闲钝化：

```
收到消息 → 激活 actor（on_activate）→ 处理消息 → 等待下一条消息
→ 空闲超时 → 钝化（on_deactivate）→ 释放资源
→ 再次收到消息 → 重新激活 → ...
```

这是 Orleans/Dapr 的成熟模型。
更细粒度的 serverless 执行模型（I/O 边界挂起/恢复）作为 Phase 5 探索方向，
需要先评估是否引入 journal 机制（会增加运行时对状态的介入）。

**3.5 运行时用 Rust 实现**

理由：
- 可预测延迟：无 GC pause，p99 消息投递 < 100μs（本机）
- Actor 密度：单节点支撑 10 万级虚拟 actor，大部分空闲
- 故障恢复速度：节点挂了，秒级在其他节点重新激活
- 资源隔离：per-actor budget 调度，work-stealing scheduler
- 部署形态灵活：嵌入式 / sidecar / 独立集群

### 待定

**3.6 消息投递语义**

| 选项 | 说明 | 代价 |
|------|------|------|
| **at-least-once**（倾向） | 消息可能重复，业务自己幂等 | 低。SDK 层可提供 @idempotent 辅助去重 |
| exactly-once | 运行时层做 dedup（消息 ID + 已处理集合） | 高。每条消息多一次存储查询，影响 hot path 延迟 |

倾向 at-least-once 为默认，exactly-once 为可选高级特性。

**3.7 Actor 并发模型**

| 选项 | 说明 | 代价 |
|------|------|------|
| **串行**（经典 actor） | 一次处理一条消息，简单安全 | 并行工具调用时 actor 被阻塞 |
| 可配置并发度 | actor 可同时处理 N 条消息 | 内部状态一致性由业务保证 |

AI agent 场景的特殊性：agent 可能并行调用多个工具，串行模型下 actor 在等工具返回时无法处理新消息。
需要进一步讨论并行工具调用是建模为"actor 内部并发"还是"spawn 子 actor"。

**3.8 传输层**

| 选项 | 说明 | 代价 |
|------|------|------|
| gRPC（倾向） | 所有语言有现成 client，SDK 铺开快 | 延迟多 1-2ms overhead |
| 自研二进制协议 | 延迟低、控制力强 | 每种语言 SDK 都要实现传输层 |

用户是业务开发者和 ML 工程师，SDK 生态铺开速度可能比极致延迟更重要。
倾向 gRPC 起步，后续可加 fast path 自研协议做优化。

## 4. 架构分层

```
Layer 4 — Language SDK
    Python (PyO3)  |  Java (gRPC)  |  Go (gRPC)  |  Rust (native)
    序列化/反序列化、类型安全的 ActorRef wrapper、agent 抽象 API

Layer 3 — Actor API
    Virtual Actor（auto-activate/deactivate）
    Spawned Actor（explicit lifecycle）
    Supervision（local + remote strategy tree）
    Stream Pipeline（backpressure-aware）

Layer 2 — Distribution
    Cluster Membership（SWIM protocol，故障检测）
    Sharding（consistent hash，actor 放置）
    Registry（CRDT，最终一致的 actor 注册表）
    Transport（gRPC / 可替换）

Layer 1 — Runtime Core
    Scheduler（work-stealing，per-actor budget）
    Mailbox（lock-free MPSC，bounded，背压策略）
    Tracing（zero-cost distributed spans）
```

### Layer 1 — Runtime Core

Rust 重写的核心价值层。

**Scheduler**：
- Work-stealing 调度器，为 actor 定制
- Per-actor budget：单次调度最多处理 N 条消息，然后 yield
- 防止 chatty actor 饿死其他 actor

**Mailbox**：
- Lock-free MPSC channel（crossbeam 或自研）
- Bounded + 可配置背压策略（block / drop-oldest / drop-newest / fail）
- 本机 actor 间零拷贝（Arc 传递）

**Tracing**：
- 基于 `tracing` crate，编译期决定是否 emit
- 每条消息自动携带 trace_id → parent_span_id
- 生产环境关掉 = 零运行时开销

### Layer 2 — Distribution

**Cluster Membership**：
- SWIM 协议（Consul 使用的同款），O(log N) 故障检测收敛
- 节点加入/离开/故障自动发现

**Actor 放置**：
- 虚拟 Actor：按 actor ID consistent hash 到 shard，shard 分配到节点
- 显式 Actor：指定节点或由 scheduler 选择

**Registry + 放置协调**：
- 轻量级 Raft 共识（`openraft`），per shard range
- Actor 激活请求先过 Raft leader 确认，保证虚拟 actor 唯一性
- 不在消息 hot path 上——只在 shard 分配和再平衡时参与
- 节点集群需奇数个（3/5/7），少数派故障仍可用

### Layer 3 — Actor API

对 SDK 层暴露的编程模型。

**Virtual Actor**：
```
trait VirtualActor {
    fn on_activate(&mut self, ctx: &ActorContext)    // 激活时调用
    fn on_receive(&mut self, ctx: &ActorContext, msg: Bytes) -> Bytes  // 处理消息
    fn on_deactivate(&mut self, ctx: &ActorContext)  // 钝化时调用
}
```

**Supervision**：
- Directive: Resume / Stop / Restart / Escalate
- OneForOne / AllForOne 策略
- 跨节点 supervision：子 actor 所在节点故障 → 父 actor 收到 NodeDown 消息 → 触发策略

### Layer 4 — Language SDK

用户直接接触的 API。

Python SDK 示例（目标体验）：
```python
from agent_runtime import agent, message

@agent(id_field="session_id")
class ChatAgent:
    def on_activate(self):
        self.history = load_history(self.session_id)  # 业务自己管状态

    def on_message(self, msg: bytes) -> bytes:
        user_input = decode(msg)
        response = call_llm(self.history, user_input)  # 业务自己调 LLM
        self.history.append((user_input, response))
        return encode(response)

    def on_deactivate(self):
        save_history(self.session_id, self.history)   # 业务自己存状态
```

Actor 模型对用户完全透明。用户看到的是"一个有生命周期钩子的 agent class"。

## 5. 关键性能指标

| 指标 | 目标 | 说明 |
|------|------|------|
| 本机消息投递 p99 | < 100μs | 运行时存在的意义 |
| 跨节点消息投递 p99 | < 5ms | 含序列化 + 网络 |
| 单节点 actor 密度 | 100K+ | 大部分空闲的虚拟 actor |
| 虚拟 actor 激活延迟 | < 1ms（本机） | 从收到消息到 on_activate 开始执行 |
| 节点故障恢复 | < 5s | 故障检测 + actor 迁移到新节点 |
| 空闲 actor 内存占用 | < 1KB/actor | 钝化后只保留 ID + 元数据 |

## 6. 实施路线

### 共享阶段（Phase 0-2）

两条路线共享同一个 Rust runtime core。

```
Phase 0 — 保留 Python 版（当前仓库）
  作为 API 参考实现和行为测试 oracle
  验证过的 API 设计和抽象分层直接复用

Phase 1 — Rust Runtime Core（Layer 1）
  实现 scheduler + mailbox + tracing
  用 criterion.rs 做 benchmark，验证 > 10x 性能收益
  交付物：单节点 actor 运行时 + Rust native API

Phase 2 — Distribution（Layer 2）
  实现 SWIM membership + consistent hash sharding + openraft 共识
  实现网络传输层
  交付物：多节点集群 + 虚拟 actor 放置
```

### Phase 3+ 分叉

**路线 A — 企业私有化（scope 小，可行性高）**

```
Phase 3A — Python SDK（PyO3 直接绑定）
  不需要 gRPC，嵌入式 library 模式
  对接内部 RPC / 消息队列 / 监控
  交付物：Python @agent 装饰器 + 内部基础设施集成

Phase 4A — 生产化
  Supervision 跨节点支持
  对接内部 tracing / metrics 系统
  内部 agent 迁移到新 runtime
  交付物：内部 AI agent 基础设施上线
```

**路线 B — 开源产品（scope 大，需要更多投入）**

```
Phase 3B — Python SDK + gRPC 接口
  PyO3 绑定 + gRPC service 两种接入方式
  交付物：Python 开发者可用 @agent 装饰器写 agent

Phase 4B — 生产化 + 多语言
  Supervision 跨节点支持
  Exactly-once 可选特性
  监控 dashboard（Grafana 模板）
  Java / Go SDK
  交付物：多语言生产可用

Phase 5B — Serverless 集成（探索方向）
  与 K8s / Knative / 自研 FaaS 平台对接
  Auto-scaling：根据 actor 活跃度自动扩缩节点
  评估是否引入 journal 机制实现 I/O 边界挂起
```

### 工期估算（路线 A，1-2 人 + AI 辅助）

经过两轮对抗性审查后的修正估算：

**里程碑 1：单节点 + PyO3 SDK（先交付，证明价值）**

| 任务 | 工期 | 风险点 |
|------|------|--------|
| Scheduler（per-actor budget，tokio 上层） | 5-7d | tokio 不提供 actor 粒度调度，需自建一层 |
| Mailbox（4 种背压策略 + stash） | 4-6d | drop-oldest 需 ring buffer，不是配置项是不同实现 |
| ActorRef + 消息投递（本机） | 2-3d | bytes 消息 + metadata，相对简单 |
| Supervision 树 | 5-7d | Rust ownership 下的 parent-child 树 + 竞态处理 |
| Virtual Actor 生命周期 | 3-5d | 激活/钝化/空闲检测/timer |
| Tracing 集成 | 1-2d | tracing crate 直接集成 |
| PyO3 绑定 + async 桥接 | 8-15d | 三层运行时桥接（tokio ↔ pyo3 ↔ asyncio）、GIL 管理、重入 |
| 集成测试 + benchmark | 3-5d | 用 Python 版测试用例做行为对齐 |
| **里程碑 1 总计** | **31-50d = 6-10 周** | |

**里程碑 2：分布式（里程碑 1 验证后再投入）**

| 任务 | 工期 | 风险点 |
|------|------|--------|
| Transport 层（gRPC / 自研） | 5-7d | |
| SWIM 集成（foca） | 3-5d | |
| openraft 集成（单 group 管理 shard 分配） | 10-15d | Multi-group 是大坑，先用单 group 简化 |
| Shard 分配 + 再平衡 + actor 迁移 | 10-19d | 迁移过程中的消息路由是核心难点 |
| Remote ActorRef + per-sender 有序投递 | 8-12d | |
| 跨节点 Supervision | 7-10d | |
| 分布式测试 + 故障注入 | 7-14d | 至少需要基本的分区注入框架 |
| **里程碑 2 总计** | **50-82d = 10-16 周** | |

**总计：路线 A 全量 = 16-26 周（4-6 个月）**

### 对抗性审查发现的工期风险

| 风险 | 说明 | 影响 |
|------|------|------|
| **Scheduler 不是现成的** | tokio work-stealing 是 task 粒度，per-actor budget 需自建 | +3-5d |
| **Mailbox 4 种策略是 4 种实现** | crossbeam 只支持 block 和 fail，drop-oldest/newest 要自己做 | +2-3d |
| **Supervision ownership 地狱** | Arc + Weak + async + parent-child 生命周期在 Rust 下复杂度高 | +3-5d |
| **PyO3 async 三层桥接** | tokio ↔ pyo3-asyncio ↔ asyncio，GIL 管理、错误传播、重入 | +5-10d |
| **openraft Multi-group** | 官方 example 是单 group，per-shard-range 多 group 需自建 | +5-10d |
| **分布式逻辑 bug** | Rust 消除内存安全类 bug（~30%），逻辑并发问题（死锁、竞态、消息丢失）仍在 | 不可预估 |
| **Graceful shutdown** | 系统级优雅停机协议（drain mailbox、等待 on_deactivate、跨节点通知）未设计 | +3-5d |
| **消息版本兼容** | Day 1 消息格式需预留版本字段，否则后期改成本极高 | 设计决策 |

### 建议策略

1. **先交付里程碑 1**（单节点 + PyO3 SDK，~7 周中位数），用 benchmark 证明 10x+ 性能收益
2. 里程碑 1 证明价值后，再投入里程碑 2（分布式）
3. 路线 A 的交付物是路线 B 的子集，不会浪费
4. 先走路线 A 内部验证，再决定是否开源（路线 B）

## 7. 基础原语

整个系统由七块积木构成，所有上层能力都是它们的组合：

| 积木 | 定义 | 职责 |
|------|------|------|
| **Actor** | 计算单元。收消息、做事、可能发出新消息。内部状态外部不可见 | 封装状态和行为 |
| **Message** | `Vec<u8>` + metadata。发送方和接收方自己约定序列化格式 | 通信载体 |
| **Ref** | Actor 的地址标签。可能指向本机，可能指向远端，调用方无感 | 寻址和 location-transparency |
| **Future** | 一张欠条。ask 发出去，结果还没回来，Future 持有这个"待兑现的结果" | 异步请求-响应、组合并发 |
| **Stream** | 带背压的管道。消息从一端流向另一端，流速由接收方控制 | 持续数据流、流量控制 |
| **Timer** | 闹钟。超时、延迟、定时。"N 秒没消息就钝化"、"ask 超时 30 秒" | 时间驱动的生命周期控制 |
| **Supervision** | 容错策略。actor 挂了，父 actor 决定重启、停止还是上报 | 故障恢复 |

### 组合方式

- **单个 agent** = 一个 Actor
- **agent 问 agent** = Actor A 发 Message 给 Actor B 的 Ref，拿回一个 Future
- **并行调工具** = 同时发出多个 ask，多个 Future 通过 `join` 合并，全部完成才兑现
- **agent 管道** = 用 Stream 将 Actor A → B → C 串联，C 消费慢时压力自动传回 A
- **容错** = Actor 挂了，Supervision 决定重启或放弃。节点挂了，actor 在其他节点重新激活
- **分布式** = 同样七块积木，Ref 屏蔽了本机/远端差异。运行时帮你跨网络投递 Message
- **超时控制** = 每个 Future 绑定 Timer，LLM 调用超时自动取消。空闲 actor 通过 Timer 触发钝化

### 已知风险与应对

| 风险 | 场景 | 应对 | 优先级 |
|------|------|------|--------|
| **Future 悬挂** | 跨节点 ask，对端 node 整体故障，Future 永远不兑现 | 所有 Future 强制带 timeout，无默认无限等待 | Day 1 |
| **消息乱序** | 跨节点消息经网络传输可能乱序，agent 逻辑通常依赖顺序 | 运行时保证 per-sender 有序投递 | Day 1 |
| **背压死锁** | A→B 和 B→A 双向 Stream 都满，互相等对方消费 | 禁止双向 Stream，或其中一条用 tell + drop 策略打破循环 | Phase 2 |
| **激活风暴** | 突发流量导致大量虚拟 actor 同时激活，打爆资源 | 激活限流：同一时刻最多并发激活 N 个，其余排队 | Phase 2 |
| **Timer-Supervision 竞争** | Timer 到期触发钝化，同时 Supervision 正在 restart | 明确优先级：Supervision 优先于 Timer，restart 过程中暂停 Timer | 语义定义 |

## 8. 高级模型（Layer 3-4 演进方向）

七块积木解决"消息怎么流转"。以下三个高级模型解决"怎么保证流转是正确的"——它们不影响 Layer 1-2 的 runtime 设计，是 Layer 3-4 的演进方向。

### 8.1 Session Types — 编译期通信协议校验

**问题**：当前 actor 之间发什么消息、该回什么，全靠文档约定。发错了运行时才报错。

**方案**：把通信协议写进类型系统。

```
协议定义：
  A 发 Request → B 回 Accept 或 Reject
  如果 Accept → A 发 Data → B 回 Ack
```

违反协议 = 编译不过。不可能出现"B 还没回 Accept，A 就发了 Data"。

**对 agent 场景的价值**：agent 间的交互模式（请求-响应、流式输出、工具调用协议）可以在类型层面强制保证，不再是运行时报错。

**成熟度**：可小范围使用。Rust 生态有 `session-types` crate 原型。适合在 Layer 3 的 agent 间协议定义中引入。

### 8.2 Choreography — 全局协议编译成局部代码

**问题**：每个 actor 单独编程，全局流程正确性靠开发者脑内保证。多 agent 协作容易出现死锁、遗漏分支。

**方案**：写一份全局协议描述，编译器自动拆成每个 actor 的局部代码。

```
全局视角（开发者写的）：
  User -> Gateway: input
  Gateway -> SafetyAgent: check(input)
  SafetyAgent -> Gateway: result
  if result.safe:
      Gateway -> ReplyAgent: generate(input)
      ReplyAgent -> Gateway: response
      Gateway -> User: response
  else:
      Gateway -> User: rejected
```

编译器生成 Gateway、SafetyAgent、ReplyAgent 三个 actor 的代码。全局协议可验证，保证无死锁。

**对 agent 场景的价值**：多 agent 协作流程声明式描述，不用手写编排逻辑。

**成熟度**：学术阶段，无生产级实现。但思路可借鉴到 SDK 层——提供 DSL 描述多 agent 协议，编译生成 actor 编排代码。

### 8.3 Effect System — 可声明、可替换的副作用

**问题**：actor 内部的副作用（发消息、spawn、定时）和业务逻辑耦合，难以测试和替换。

**方案**：所有副作用声明为 effect，由外部 handler 解释执行。

```
fn agent_logic() -> Effect<[Send, Receive, Spawn, Timer], Response> {
    let input = perform Receive;
    let result = perform Send(llm_ref, input);
    perform Timer::after(30.seconds());
    respond(result)
}
```

测试时换一个 mock handler，不改逻辑代码。

**与当前 Python 版的关系**：Python 版的 Free Monad（SpawnF / TellF / AskF + LiveInterpreter / MockInterpreter）已经在做这件事。Effect System 是这条路的类型安全终极形态。

**成熟度**：Rust 生态有 `effing-mad` 等实验实现。Rust 版可用 trait + handler 模式近似实现，不依赖语言层面的 effect system 支持。

### 高级模型引入时机

| 模型 | 引入阶段 | 应用层 |
|------|---------|--------|
| Session Types | Phase 3（SDK 层） | agent 间协议定义 |
| Effect System | Phase 3（SDK 层） | agent 逻辑的测试和替换 |
| Choreography | Phase 4+（SDK 层 DSL） | 多 agent 协作编排 |

## 9. 竞品分析与设计对比

### 9.1 业界全景

按威胁程度排序：

**直接竞争对手：**

| 项目 | 语言 | 核心模型 | 成熟度 | 威胁程度 |
|------|------|---------|--------|---------|
| **Restate** | Rust | Virtual Objects + durable execution + 多语言 SDK | 早期生产级，有生产用户 | **最高** |
| **Dapr** | Go (sidecar) + 多语言 SDK | 虚拟 actor，gRPC 通信，100+ 组件 | 生产级，CNCF 孵化 | 高 |
| **Cloudflare DO** | JS/Rust (Wasm) | serverless 虚拟 actor | 生产级 | 中（绑定平台） |

**相邻赛道：**

| 项目 | 语言 | 核心模型 | 成熟度 | 关系 |
|------|------|---------|--------|------|
| **Akka/Pekko** | Scala/Java (JVM) | typed actor + cluster sharding + streams | 生产级，十年积累 | 最好的教科书，非直接竞争 |
| **Orleans** | .NET | 虚拟 actor 鼻祖，Grain Directory | 生产级，微软十年打磨 | 虚拟 actor 设计参考 |
| **Temporal** | Go | 持久化执行 | 生产级 | 相邻赛道，workflow 粒度 |
| **Proto.Actor** | Go + .NET + Kotlin | 多语言 actor，gRPC transport | 生产可用 | 无虚拟 actor |
| **Ray** | Python | 分布式计算框架 | 生产级 | 面向批计算，非消息驱动 |

**Wasm 路线（潜在降维打击）：**

| 项目 | 说明 | 威胁 |
|------|------|------|
| **Spin**（Fermyon） | Wasm serverless 平台，Component Model 天然多语言 | 若 Wasm 生态成熟，不需要每种语言单独写 SDK |
| **wasmCloud** | Wasm actor 平台，分布式，lattice 架构 | 与本项目定位高度重叠 |

**Rust 生态：**

| 项目 | 状态 | 说明 |
|------|------|------|
| **Ractor** | 活跃 | Erlang 风格，有 supervision，无分布式 |
| **Kameo** | 活跃 | 轻量 actor，tokio 上层，无分布式 |
| **Lunatic** | 半停滞 | WASM runtime + Erlang 模型，野心大但团队小 |
| **Bastion** | 停滞 | Erlang 风格容错，已无人维护 |

### 9.2 Restate — 最直接的竞争对手

Restate 做的事跟本项目高度重叠，必须搞清楚��差异化。

**许可证与商业模式**：
- 许可证：BUSL-1.1（Business Source License）——源码公开可看，但不是真正的开源
- 非生产环境：免费使用
- 生产环境：需要 Restate 授权（或等 4 年后自动转为 GPL）
- 不能拿 Restate 代码做竞品云服务
- 商业化：Restate Cloud 托管服务（免费 tier + 付费 tier + 企业版）
- 与 Akka/Lightbend 走的同一条路——source-available + cloud service

**Restate 已有的**：
- Rust 实现的 runtime
- 虚拟 actor（叫 Virtual Objects），按 key 寻址，自动激活
- 多语言 SDK（TypeScript / Java / Go / Rust / Python）
- Durable execution：用 journal 记录每一步副作用，挂了从 journal 回放恢复
- 已解决 serverless 执行模型的状态持久化问题
- 有生产用户

**Restate 没有的**：
- Supervision 树（actor 没有父子层级容错）
- Stream 背压（不是消息驱动的流式管道）
- 嵌入式部署模式（Restate 是独立 server）
- AI agent 场景的专门优化
- 真正的开源许可（BSL 限制了商业使用和二次分发）

**本项目的结构性机会**：

真正 Apache 2.0 / MIT 开源的、Rust 实现的分布式 actor runtime，市场上不存在：
- Restate：BSL 许可，有商业限制
- Akka：BSL 许可，社区分裂
- Dapr：Apache 2.0，但 Go 实现，无 supervision / 背压
- Orleans：MIT，但绑死 .NET

本项目若选择 Apache 2.0 + Rust + supervision + 背压，这个组合在市场上是唯一的。

**路线选择**：
- ~~A）在 Restate 上层做 AI agent 优化~~（BSL 许可限制了这条路的商业化可能性）
- B）从零做 runtime，差异化在：真正开源 + supervision + 背压 + 嵌入式部署 + AI 场景优化

### 9.3 与 Akka 的核心分歧

Akka 是 actor 模型在工业界最完整的实现。本项目继承了 Akka 验证过的理念，但在三个根本点上走了不同的路。

**分歧一：谁管 actor 状态**

| | Akka | 本项目 |
|-|------|--------|
| 方式 | 运行时管。Akka Persistence 内建 event sourcing，状态恢复是框架职责 | 运行时不管。只提供 on_activate/on_deactivate 钩子，状态存取是业务的事 |
| 代价 | 框架更重，但用户更省心 | 运行时更薄，但复杂度推给业务 |
| 原因 | 本项目定位是调度和通信基础设施，不碰业务状态 |

**分歧二：消息类型**

| | Akka | 本项目 |
|-|------|--------|
| 方式 | 有类型。`ActorRef[Command]` 编译期知道 actor 接受什么消息 | bytes。运行时只看到 `Vec<u8>`，类型安全在 SDK 层 |
| 代价 | 更安全但类型系统绑死在 Scala/Java 泛型里 | 运行时层无类型安全，换来多语言自由度 |
| 原因 | 多语言 SDK 是核心需求，运行时层不能绑定任何语言的类型系统 |

**分歧三：actor 放置协调**

| | Akka | 本项目 |
|-|------|--------|
| 方式 | 中心化。ShardCoordinator 跑在 Cluster Singleton 上 | 轻量级 Raft 共识（per shard range），用 `openraft` 实现 |
| 代价 | 强一致但有单点瓶颈 | 需要奇数节点，多一次网络往返 |
| 原因 | 原 CRDT 方案无法保证虚拟 actor 唯一性（见 §9.9 设计修正），改用 Raft |

### 9.4 从 Akka 继承的设计

| 继承什么 | 说明 |
|---------|------|
| Supervision 树 | OneForOne / AllForOne / Escalate 语义，十年验证 |
| Location-transparent Ref | ActorRef 屏蔽本机/远端差异 |
| Stream 背压思路 | demand-based flow control 的理念（不搬整个 graph DSL） |
| Passivation 机制 | 空闲 actor 自动钝化释放资源 |
| Stash 机制 | actor 暂时处理不了的消息先缓存，就绪后再处理 |

### 9.5 Akka 的教训（反面参考）

| 教训 | Akka 踩的坑 | 本项目如何避免 |
|------|------------|---------------|
| 类型安全要 Day 1 | Classic API 用 `Any` 做消息类型，后补 Typed API 导致生态分裂 | bytes + SDK 层类型安全，从 Day 1 明确分层 |
| 开源协议不能变 | Lightbend 把 Akka 从 Apache 2.0 改成 BSL，社区 fork 出 Pekko | Day 1 定好 Apache 2.0 或 MIT，不留后门 |
| Dispatcher 配置不能暴露给用户 | Akka 的 fork-join / pinned / thread-pool 配置是出了名的复杂 | work-stealing scheduler 统一处理，用户无需配置 |
| 热更新不能后补 | Rolling Update + Coordinated Shutdown + 消息版本化是后期补的 | Day 1 设计消息版本兼容策略 |

### 9.6 Rust 的结构性简化

Akka 的很多复杂度是在补偿 JVM 的缺陷。Rust 天然消除了 GC 相关的那一层。

| Akka 要自己造的 | Rust 天然有的 | 消除的复杂度 |
|----------------|-------------|-------------|
| Dispatcher 配置体系 | tokio work-stealing runtime | 一个 runtime 统一调度 |
| `Future` combinators（Scala Future 包装） | `Future` + `FutureExt` 零成本组合 | 语言原生，不是库包装 |
| Akka Serialization 框架 | `serde` 生态 | 成熟且支持零拷贝 |
| Mailbox 实现（考虑 GC 压力） | lock-free MPSC channel（crossbeam） | 无 GC，确定性内存回收 |
| Stash（额外消息缓冲区） | `VecDeque` + 所有权转移 | 无 GC root 管理 |
| Phi accrual 故障检测（被 GC pause 干扰） | 同样的算法，无 GC pause 干扰 | 故障检测信号干净，不会误判 |
| ActorRef 序列化 | ActorRef = ID + 地址，`Copy` 语义 | 零开销传递 |

**需要诚实承认的**：Rust 不能消除 actor runtime 的核心复杂度——调度公平性（per-actor budget 需要自己在 tokio 上��实现）、背压传播（`futures::Stream` 不等于 Akka Streams 的 graph DSL + demand signaling）、跨节点背压（需要 application 层流控）。这些不管用什么语言都要做。

Akka 生产环境最常见的事故：GC pause → cluster 误判节点下线 → 大规模 actor 迁移 → 雪崩。Rust 没有 GC，这整个故障链不存在。但 Rust 仍有延迟尖刺（allocator 碎片、page fault），频率和幅度远低�� GC pause。

### 9.7 与 Dapr 的对比

Dapr 的架构决策与本项目高度重合，是最直接的架构参考。

**共同选择**：
- 多语言 SDK via gRPC
- 虚拟 actor 自动激活/钝化
- 运行时不管 actor 内部状态
- 可私有化部署

**Dapr 被低估的优势**：
- **100+ 组件生态**：pub/sub、state store、secret store、binding。本项目只做调度和通信，用户还需自己集成消息队列、状态存储、配置中心
- **运维成熟度**：Helm chart、operator、dashboard、mTLS、distributed tracing。从"能跑"到"生产级"的距离，Dapr ���了 5 年
- **社区和文档**：CNCF 孵化，大量用户经验和 troubleshooting 指南

**本项目的���异化**：

| 维度 | Dapr | 本项目 |
|------|------|--------|
| 运行时语言 | Go（sidecar 模式，每个 pod 多一个进程） | Rust（可嵌入 library 模式，零额外进程） |
| AI 场景���化 | 无。通用设计 | 为高延迟 I/O、流式响应、突发激活优化 |
| 背压 | actor 间通信无背压机制 | Stream 原语内建背压 |
| Supervision | 无 | ��整的 supervision 树 |
| 部署模式 | 仅 sidecar | 嵌入式（library）+ sidecar + 独立集群 |
| 延迟 | sidecar 每次多一跳 localhost gRPC | library 模式零额外网络开销 |

### 9.8 Akka vs Dapr 直接对比

两个完全不同的设计哲学。Akka 是嵌入式框架，Dapr 是 sidecar 基础设施。

| 维度 | Akka | Dapr |
|------|------|------|
| 架构模式 | library，嵌入进程 | sidecar，独立进程 |
| 语言 | JVM only | 任意语言 via gRPC |
| actor 类型 | 强类型 `ActorRef[M]` | 弱类型，HTTP/gRPC 调用 |
| 状态管理 | 内建 event sourcing（Persistence） | 可插拔 state store（Redis/Cosmos/Postgres） |
| 背压 | Akka Streams，完整的 Reactive Streams 实现 | 无 |
| Supervision | 完整的树状策略 | 无。actor 挂了就挂了 |
| 消息投递 | at-most-once（默认）/ at-least-once（Persistence） | at-least-once |
| 集群管理 | 自建（Cluster + Gossip 协议） | 依赖外部（K8s / Consul / etcd） |
| 部署 | 进程即集群节点 | K8s pod + sidecar container |
| 学习曲线 | 陡峭 | 平缓 |
| 延迟 | 低（进程内直接调用） | 高（多一跳 localhost gRPC） |

核心取舍：**性能和控制力 vs 多语言和简单性**。

- Akka 选了前者——完整的 actor 语义，代价是绑死 JVM 且学习曲线陡峭
- Dapr 选了后者——任何语言都能用，代价是丢掉了 supervision、背压、强类型

### 9.9 本项目的定位：取两者之长

| 能力 | Akka | Dapr | Restate | 本项目 |
|------|------|------|---------|--------|
| Supervision 树 | 有 | 无 | 无 | 有 |
| Stream 背压 | 有 | 无 | 无 | 有 |
| 多语言 SDK | 无 | 有 | 有 | 有 |
| 简单 API | 无 | 有 | 有 | 有 |
| 虚拟 Actor | 有 | 有 | 有 | 有 |
| Durable Execution | 有（Persistence） | 无 | 有（journal） | 可选（见下文） |
| 无 GC 延迟 | 无（JVM） | 无（Go） | 有（Rust） | 有（Rust） |
| 嵌入式部署 | 有 | 无 | 无 | 有 |
| AI 场景优化 | 无 | 无 | 无 | 有 |

### 9.10 对抗性审查后的设计修正

经过对抗性审查（adversarial review），原设计存在三个矛盾，现做出修正：

**修正一：CRDT registry → 轻量级 Raft（openraft）**

原方案：CRDT registry 做 actor 放置，去中心化，最终一致。

问题：CRDT 提供最终一致性，但虚拟 actor 放置需要唯一性保证。最终一致意味着两个节点可能同时激活同一个 actor，导致状态分叉。原方案用 "lease + fencing token" 兜底，但 fencing token 需要存储层配合校验——而设计声称"运行时不碰业务状态"，矛盾。

修正：改用 `openraft` 实现 per-shard-range 的轻量级 Raft 共识。actor 激活请求先过 Raft，leader 确认分配后才激活��代价是多一次网络往返和奇数节点要求，但保证唯一性。Raft 只在 shard 分配和再平衡时参与，不在每条消息的 hot path 上。

**修正二：Serverless 执行模型降级**

原方案：actor 在 I/O 边界挂起，释放计算资源���每段 CPU 工作是一次 serverless 调用。

问题："挂起释放资源"和"运行时不管状态"自相矛盾——挂起后内存中的状态怎么办？序列化到外部存储代价巨大，不序列化��没真正释放资源。Restate 解决了这个问题，但它用了 journal 深度介入状态管理。

修正：Phase 1-4 只做虚拟 actor 的激活/钝化模型（空闲超时后钝化释放资源��来消息时重新激活）。这是 Orleans/Dapr 的成熟模型，不涉及 I/O 边界挂起。Phase 5 的 serverless 深度集成降级为探索方向，需要先评估：是自己实现 journal（增加运行时职责范围），还是与 Restate/Temporal 集成。

**修正三：补充 Rust 不能消除的复杂度**

原表述："Rust 消除了对抗 JVM 的那整层复杂度"。

修正：Rust 消除了 GC 相关的复杂度（确实显著），但 actor runtime 的核心复杂度——调度公平性、背压传播、跨节点一致性——��因语言改变。§9.6 已更新，诚实标注 Rust 消除的和不能消除的。

## 10. 开放问题

1. **并行工具调用的建模**：actor 内部并发 vs spawn 子 actor？前者简单但破坏串行语义，后者保持纯粹但 overhead 更高。
2. **Exactly-once 的实现层级**：运行时层 dedup 还是 SDK 层辅助？影响 hot path 性能。
3. **状态钝化的存储接口**：运行时要不要提供可插拔的 state store 抽象，还是完全交给业务？提供抽象能降低业务复杂度，但增加运行时的职责范围。
4. **多集群 / 跨 region**：Phase 2 只考虑单集群。跨 region 的 actor 寻址和消息路由是否在 scope 内？
5. **消息优先级**：是否需要优先级队列？AI agent 场景中，取消信号应该优先于普通消息处理。
6. **Restate 的定位**：Restate 使用 BSL 许可，路线 A（上层做）有商业化限制。路线 B（从零做）已确定为主方向，差异化在真正开源 + supervision + 背压 + AI 优化。需持续跟踪 Restate 的功能演进。
7. **热更新 / 滚动升级**：不同版本节点间的消息格式兼容性、actor 行为变更的协调方式。
8. **Multi-tenancy**：多租户场���下的 actor 资源隔离（CPU / 内存 / 消息速率）、命名空间隔离、安全边界。
9. **安全模型**：actor 间访问控制、传输层加密（mTLS）、消息认证（防伪造 ActorRef）。
10. **分布式测试策略**：Python 版是单机的，无法验证分布式行为。需要 Jepsen / deterministic simulation testing。
11. **故障模式声明**：��要明确文档化的 failure mode——如 actor 处理完消息但钝化前节点挂了，状态丢失；at-least-once 下业务需自保幂等。

## 11. 从 Python 版继承的设计验证

当前 Python 实现（everything_is_an_actor）已验证：

- Virtual Actor 的激活/钝化模型可行
- Supervision 策略（OneForOne / AllForOne）在 agent 场景有效
- Middleware pipeline 的可组合性
- 背压策略（block / drop / fail）的 API 设计
- SDK 层的 agent 抽象（@agent 装饰器 + on_receive/on_activate/on_deactivate）

这些 API 设计直接复用到 Rust 版的 Layer 3 和 Layer 4。
Rust 重写的是 Layer 1 和 Layer 2——性能和分布式能力，不是 API 设计。
