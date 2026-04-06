# Rust + Python Actor Runtime 设计方案

## 一句话

Rust 做引擎（mailbox、调度、IO），Python 做方向盘（agent 逻辑），generator 做传动轴（零开销暂停/恢复）。

---

## 动机

### Python 的瓶颈

当前 `everything_is_an_actor` 是纯 Python 实现。Python 的限制：

- **GIL** — 单进程无法利用多核做 CPU-bound 工作
- **asyncio 单线程** — 所有 actor 共享一个事件循环
- **每条消息开销高** — 动态类型、对象分配、GC 压力
- **无法控制内存布局** — cache miss 严重

### Rust 能给什么

- **tokio 多线程** — 真并行，吃满所有核
- **lock-free mailbox** — crossbeam channel，纳秒级消息分发
- **零 GC** — 可预测延迟，无停顿
- **内存布局可控** — cache-friendly

### 为什么不是 Rust + Scala

Scala 原型（`proto/rust-scala-demo`）验证了 continuation 模型可行，但暴露了根本问题：

1. **Rust → pipe → JVM → replay → pipe → Rust**，比纯 Python 还慢（毫秒级 vs 微秒级）
2. **O(N²) replay** — Scala 没有原生 continuation，只能 throw Suspend + 从头重跑
3. **用户写 Python** — 多一层 Scala 没有收益

Python generator 是语言原生的 continuation：`yield` 暂停，`gen.send()` 恢复，O(1)，不需要 replay。

---

## 架构

```
┌──────────────────────────────────────────────┐
│           Rust Runtime (PyO3 + tokio)        │
│                                              │
│  Mailbox (lock-free) → Scheduler (work-      │
│  stealing) → Supervision → Effect Executor   │
│                                              │
│  - 消息路由：纳秒级                            │
│  - IO 执行：tokio HTTP，真并行                 │
│  - Supervision：retry, circuit breaker        │
└──────────────────┬───────────────────────────┘
                   │ PyO3 (in-process, 零 IPC)
┌──────────────────▼───────────────────────────┐
│           Python Agent Layer                 │
│                                              │
│  class MyActor(Actor):                       │
│      def receive(self, msg):                 │
│          result = yield self.ask("llm", msg) │
│          return result                       │
│                                              │
│  generator = Free monad                      │
│  yield = liftF                               │
│  runtime = interpreter（自然变换）             │
└──────────────────────────────────────────────┘
```

### 职责划分

| 层 | 职责 | 语言 | 性能要求 |
|---|------|------|---------|
| Runtime | mailbox、调度、路由、supervision、IO 执行 | Rust (tokio) | 纳秒级 |
| Agent | 业务逻辑、prompt 构造、结果处理 | Python (generator) | 微秒级（不是瓶颈） |
| Bridge | in-process 函数调用 | PyO3 | 零 IPC |

### GIL 为什么不是问题

Python 代码只在 `yield` 和 `gen.send()` 之间运行（微秒级纯计算）。IO（HTTP 调 LLM）在 Rust tokio 里执行，不持有 GIL。1000 个 agent 并发时，GIL 总占用 ~1ms，IO 占用 ~秒级。

---

## Agent API

### Actor 定义

```python
class Translator(Actor):
    def receive(self, msg):
        result = yield self.ask("llm", f"Translate: {msg}")
        return result
```

### 生命周期

```python
class ChatBot(Actor):
    def on_started(self):
        yield self.save("status", "active")

    def receive(self, msg):
        history = yield self.load("chat_history")
        response = yield self.ask("llm", f"{history}\nUser: {msg}")
        yield self.save("chat_history", f"{history}\n{msg}\n{response}")
        yield self.tell("audit", f"processed:{msg}")
        return response

    def on_stopped(self):
        yield self.tell("monitor", "goodbye")

    def on_restart(self, error):
        yield self.save("crash_log", str(error))
```

### Actor 基类

```python
class Actor:
    # ── 通信 ──
    def ask(self, target, msg) -> Effect       # 同步调用，等结果
    def tell(self, target, msg) -> Effect      # fire-and-forget
    def ask_all(self, *pairs) -> Effect        # 并行调用，等全部

    # ── 状态 ──
    def load(self, key) -> Effect              # 读
    def save(self, key, value) -> Effect       # 写

    # ── 流式 ──
    def emit(self, item) -> Effect             # 推送流式输出

    # ── 身份（runtime 注入）──
    actor_id: str
    actor_ref: ActorRef
```

### 渐进复杂度

```python
# Level 1: 最简 — 一个 ask
class Translator(Actor):
    def receive(self, msg):
        result = yield self.ask("llm", f"Translate: {msg}")
        return result

# Level 2: 有状态
class TodoList(Actor):
    def receive(self, msg):
        raw = yield self.load("todos")
        todos = raw.split("\n") if raw else []
        cmd, *args = msg.split(" ", 1)
        if cmd == "add":
            todos.append(args[0])
            yield self.save("todos", "\n".join(todos))
            return f"Added (total: {len(todos)})"
        elif cmd == "list":
            return "\n".join(todos) or "Empty"

# Level 3: 流式输出
class CodeReviewer(Actor):
    def receive(self, msg):
        yield self.emit("Analyzing...")
        structure = yield self.ask("llm", f"Analyze: {msg}")
        yield self.emit("Checking bugs...")
        bugs = yield self.ask("llm", f"Find bugs: {msg}")
        return f"## Structure\n{structure}\n\n## Bugs\n{bugs}"

# Level 4: 并行
class PriceCompare(Actor):
    def receive(self, msg):
        yield self.emit(f"Querying 3 suppliers...")
        results = yield self.ask_all(
            ("supplier_a", f"price:{msg}"),
            ("supplier_b", f"price:{msg}"),
            ("supplier_c", f"price:{msg}"),
        )
        best = yield self.ask("llm", f"Cheapest? {results}")
        return best

# Level 5: 组合（状态 + 并行 + 流式 + 容错 + 生命周期）
class Recruiter(Actor):
    def on_started(self):
        yield self.save("eval_count", "0")

    def receive(self, msg):
        count = int((yield self.load("eval_count")) or "0")
        yield self.emit(f"Evaluation #{count + 1}: {msg}")

        assessments = yield self.ask_all(
            ("tech_eval", f"Evaluate: {msg}"),
            ("culture_fit", "Check fit"),
            ("background", "Verify"),
        )

        yield self.emit("Generating recommendation...")
        recommendation = yield self.ask("llm", f"Hire? {assessments}")

        yield self.save("eval_count", str(count + 1))
        yield self.tell("hr", f"Done: {msg}")
        return recommendation
```

### 系统使用

```python
system = ActorSystem()

system.spawn("translator", Translator)
system.spawn("reviewer", CodeReviewer)

ref = system.actor_ref("translator")
result = await ref.ask("你好世界")
stream = ref.ask_stream("你好世界")    # AsyncIterator
ref.tell("fire and forget")

system.shutdown()
```

---

## Python / Rust 边界

### 谁拥有什么

```
Rust 拥有                          Python 拥有
──────────                         ──────────
ActorSystem                        Actor 类定义
├── actor 注册表 (id → 元数据)      ├── receive() generator
├── mailbox (per-actor, lock-free)  ├── on_started() generator
├── scheduler (work-stealing)       ├── on_stopped() generator
├── state store (HashMap)           └── on_restart() generator
├── supervision policy
└── effect executor (tokio)
```

### 跨边界的调用

只有两个方向的调用，全部通过 PyO3 in-process 完成：

**Rust → Python（驱动 generator）：**

```
1. Rust 从 mailbox 取出消息 (target_actor_id, msg)
2. Rust 通过 PyO3 调用 actor.receive(msg)，拿到 generator 对象
3. Rust 调用 gen.__next__()，拿到第一个 Effect
4. Rust 执行 effect（在 tokio 里，不持有 GIL）
5. Rust 调用 gen.send(result)，拿到下一个 Effect 或 StopIteration(return_value)
6. 重复 4-5 直到 generator 结束
```

**Python → Rust（零调用）：**

```
Python 不直接调用 Rust。
self.ask(target, msg) 是纯 Python 方法，只构造 Effect 对象并 yield。
Effect 对象通过 generator 协议（yield/send）传给 Rust。
```

### 跨边界的数据类型

Rust 需要读懂的 Python 对象：

```python
# Python 侧定义，Rust 侧通过 PyO3 读取字段
class Effect:
    kind: str          # "ask" | "tell" | "load" | "save" | "emit" | "ask_all"
    target: str        # actor id（ask/tell 用）
    payload: str       # 消息内容
    pairs: list[tuple] # ask_all 用

# Rust 侧只需要读 kind + 对应字段，不需要理解 payload 内容
```

Rust 注入到 Python 的数据：

```python
# Rust 在 spawn 时注入
actor.actor_id = "translator_1"     # str
actor.actor_ref = ActorRef(...)     # PyO3 封装的 Rust 对象
```

### 状态：两层并存

Actor 有两种状态，各管各的：

```python
class ChatBot(Actor):
    def on_started(self):
        self.message_count = 0                     # 内存状态：self 属性
        self.history = yield self.load("history")  # 持久化状态：从存储恢复

    def receive(self, msg):
        self.message_count += 1                     # 直接改，零开销
        response = yield self.ask("llm", msg)
        yield self.save("history", self.history)    # 写入持久化
        return response
```

| | `self.xxx` | `yield self.load/save` |
|---|---|---|
| 存在哪 | Python 堆内存（actor 实例属性） | Rust 管理（可接持久化后端） |
| 读写开销 | 零（普通属性访问） | 一次 yield 往返 |
| 进程重启后 | 丢失 | 可恢复 |
| 用途 | 计数器、缓存、临时状态 | 需要持久化/跨重启的业务状态 |

**`self.xxx` 是日常状态手段，`load/save` 是持久化通道。**

持久化路径：

```
actor: yield self.save("key", "value")
  → Effect(kind="save", key="key", value="value")
  → yield 给 Rust
  → Rust 写入 state store（默认 HashMap，可换 Redis/DB）
  → gen.send("") 确认

actor: yield self.load("key")
  → Effect(kind="load", key="key")
  → yield 给 Rust
  → Rust 从 state store 读取，按 (actor_id, key) 隔离
  → gen.send(value) 返回
```

### 流式输出（Emit）的路径

```
actor: yield self.emit("progress...")
  → Effect(kind="emit", payload="progress...")
  → Rust 收到后：
    1. 推送到该 actor 的 stream channel（给调用方消费）
    2. 调用 gen.send("") 继续 generator
  → 不阻塞，不等消费者
```

### 完整的一次消息处理

```
                  Rust                                    Python
                  ────                                    ──────
1. mailbox.recv() → (actor_id="chat", msg="Hi")
2.                                          ──→  gen = actor.receive("Hi")
3.                                          ──→  op = next(gen)
4.                                          ←──  Effect(kind="load", key="history")
5. state.get("chat:history") → ""
6.                                          ──→  op = gen.send("")
7.                                          ←──  Effect(kind="ask", target="llm", payload="User: Hi")
8. tokio::spawn HTTP POST to LLM           (释放 GIL，Python 可服务其他 actor)
   ...等待 LLM 响应...
   response = "Hello!"
9.                                          ──→  op = gen.send("Hello!")
10.                                         ←──  Effect(kind="save", key="history", value="User: Hi\nHello!")
11. state.insert("chat:history", ...)
12.                                         ──→  op = gen.send("")
13.                                         ←──  StopIteration("Hello!")
14. 返回 "Hello!" 给调用方
```

### Supervision 跨边界

```
actor receive() 中 Python 抛异常：
  → Rust 通过 PyO3 捕获 Python exception
  → Rust 根据 supervision policy 决定：
    - Restart: 调用 actor.on_restart(error)，然后重新调用 receive(msg)
    - Stop: 调用 actor.on_stopped()，从注册表移除
    - Escalate: 通知 parent actor

effect 执行失败（如 HTTP 超时）：
  → Rust 自己处理重试（不回 Python）
  → 重试耗尽后，gen.throw(EffectError(...)) 通知 Python
  → 或按 policy 直接 stop/restart
```

---

## 核心机制：Generator 作为 Continuation

### 执行流程

```
Python                          Rust
──────                          ────
gen = actor.receive(msg)
op = next(gen)                  # → Ask("llm", msg)
                                execute_effect(op)  # tokio HTTP
result = gen.send(response)     # ← "Hello World"
                                # → 如果是新的 yield，继续循环
                                # → 如果是 return，完成
```

### 与 Scala replay 方案对比

| | Scala (replay) | Python (generator) |
|---|---|---|
| 暂停 | throw Suspend | yield |
| 恢复 | 从头重跑，跳过已执行 step | gen.send()，O(1) |
| 复杂度 | O(N²) | O(1) |
| 可序列化 | 可以（ContState） | 不可以 |
| 正确性 | 依赖 replay 路径确定性（无保障） | 栈帧原生保持，无此问题 |

### 范畴论对应

| 概念 | 实现 |
|------|------|
| Free monad | Python generator |
| liftF | yield self.ask(...) |
| Interpreter（自然变换） | Rust runtime 的 effect executor |
| Effect 指令集 | ask / tell / load / save / emit / ask_all |

---

## 设计决策记录

### 已决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 语言 | Python | 用户写 Python |
| Runtime 语言 | Rust (tokio + PyO3) | 多核并行、lock-free、零 GC |
| 暂停/恢复机制 | generator (yield) | O(1) 恢复，语言原生，无需 replay |
| Agent 定义形式 | 类（extends Actor） | 需要生命周期（on_started/on_stopped） |
| Effect 语法 | self.ask() / self.tell() / ... | 自身方法，非裸指令对象 |
| IO 执行位置 | Rust | Python 不持有 GIL 做 IO，不阻塞其他 agent |
| 可序列化 continuation | 放弃 | 换取 O(1) 恢复和零 replay 开销 |
| Scala 层 | 移除 | 用户写 Python，Scala 无额外收益 |

### 已知限制

| 限制 | 影响 | 缓解措施 |
|------|------|---------|
| Generator 不可序列化 | 进程挂了，执行中的 agent 丢失 | 接受进程级容错；持久化靠 state（load/save） |
| Python sub-interpreter 不成熟 | 未来多 GIL 优化路径受限 | 当前 GIL 开销可忽略（微秒级） |

---

## 风险与对抗分析

以下风险来自对抗性审查，按严重程度排序。

### R1: GIL 吞吐上限 — 未验证 [严重]

**声称**：Python 只在 yield/send 间运行微秒级逻辑，GIL 不是问题。

**挑战**：每个 effect 步骤需要完整的 GIL acquire → Python 帧执行 → PyO3 边界转换 → GIL release 周期。文档中 "1000 agents ~1ms GIL total" 是估算，不是实测。

**真实风险**：
- 单次 GIL acquire/release ~200ns，Python 帧执行 ~500ns-1μs
- 1 万 agent × 每秒 10 次 yield = 10 万次 GIL 交接/s → 聚合 ~70-100ms/s GIL 占用
- 这是否构成瓶颈取决于 workload，不能靠推算断言"不是问题"

**验证方式**：原型 benchmark——N 个 tokio task 并发驱动 Python generator，测量 GIL 竞争下的实际吞吐和尾延迟。

**结论**：GIL 在 IO-heavy AI agent 场景（每秒 yield 次数低）大概率不是问题，但在 chatty actor 场景（高频短消息）可能成为瓶颈。**需要 benchmark 数据定量，不能定性断言。**

### R2: Generator 生命周期边缘情况 [严重]

**声称**：Generator 天然是 continuation，yield/send 就够了。

**挑战**：Generator 的完整语义比 yield/send 复杂得多：

| 场景 | 需要处理 | 后果 |
|------|---------|------|
| actor 被 stop 时 generator 正在执行 | `gen.close()` 触发 `GeneratorExit` | finally 块必须正确执行清理 |
| supervision restart | 旧 generator 必须先 close 再创建新的 | 不 close 会泄漏资源 |
| effect 执行失败后通知 Python | `gen.throw(EffectError)` | actor 代码需要 try/except 处理 |
| generator 内部 yield 后永不 resume | GC finalization 时机不确定 | 资源泄漏风险 |
| 并发 send() | generator 不可重入 | 必须保证同一 generator 串行访问 |

**对策**：

```
取消流程（actor 被 stop）：
  1. Rust 标记 actor 为 stopping
  2. 如果 generator 正在等待 effect 结果 → 取消 effect → gen.throw(ActorStopped)
  3. generator 的 finally 块执行清理
  4. Rust 调用 actor.on_stopped() generator 并驱动完成
  5. 从注册表移除

重启流程（supervision restart）：
  1. gen.close() 关闭旧 generator
  2. 创建新 Actor 实例（或复用实例、重置状态）
  3. 驱动 actor.on_restart(error) generator
  4. 重新投递原消息（如果策略要求）

并发保护：
  每个 actor 的 generator 由唯一的处理循环串行驱动，
  mailbox 负责排队，不存在并发 send() 的可能。
```

### R3: "比 asyncio 快" 是条件性的 [中等]

**声称**：Rust runtime 比纯 Python asyncio 快。

**挑战**：这取决于 workload 类型。

| 场景 | Rust+PyO3 vs asyncio | 原因 |
|------|---------------------|------|
| IO-heavy（LLM 调用，秒级） | Rust 更快 | 多核真并行 IO，asyncio 单线程 |
| Chatty（高频短消息，μs 级） | **可能更慢** | FFI 边界开销 > asyncio 协程切换 |
| CPU-bound 计算 | Rust 更快 | 不受 GIL 限制 |
| 少量 actor（<100） | 差异可忽略 | 瓶颈在 IO，不在 runtime |

**结论**：对目标场景（AI agent，IO-heavy）有优势。但文档不应无条件声称"更快"，应明确适用条件。

### R4: Backpressure 模型未定义 [中等]

当前设计中 mailbox 是 lock-free channel，但未定义：

- **mailbox 满了怎么办？** 无界队列 → 内存溢出；有界队列 → 发送方阻塞还是丢弃？
- **ask_all 扇出控制**：`ask_all` 100 个 actor 同时请求 → 100 个并发 HTTP → 对下游造成压力

**对策**：

```
mailbox 策略（spawn 时配置）：
  - bounded(n): 满时 tell 返回 MailboxFull 错误，ask 阻塞等待
  - unbounded: 默认，适用于低频场景
  - dropping(n): 满时丢弃最旧消息（适用于 metrics/日志类 actor）

ask_all 限流：
  - 可选 concurrency_limit 参数：ask_all(*pairs, max_concurrent=10)
  - 默认不限，用户按需配置
```

### R5: 跨边界错误分类 [中等]

两种完全不同的错误混在一起：

| 错误来源 | 例子 | 应该谁处理 |
|---------|------|-----------|
| **Python 业务异常** | `ValueError`, `KeyError` | Supervision（restart/stop） |
| **Effect 执行失败** | HTTP 超时，目标 actor 不存在 | Rust 重试，耗尽后通知 Python |
| **框架异常** | PyO3 转换失败，generator 协议错误 | Rust 日志 + stop actor |

**对策**：

```
Rust 侧分类：
  1. gen.send() 抛 Python 异常 → 业务异常 → 走 supervision
  2. effect 执行失败 → Rust 按 policy 重试 → 耗尽后 gen.throw(EffectError)
  3. PyO3 错误（类型转换等）→ 框架 bug → 日志 + stop + 告警
```

### R6: 重启幂等性 [低]

`on_restart(error)` 本身是 generator，可以 yield effect。如果 on_restart 中的 effect 也失败了：

```python
def on_restart(self, error):
    yield self.save("crash_count", str(self.crashes + 1))  # ← 这个 save 也失败了呢？
```

**对策**：`on_restart` 中的 effect 失败 → 日志记录 → 继续重启流程（不递归重试）。on_restart 是尽力而为，不是可靠保证。与现有 Python 层 `on_stopped` 的设计一致：有 timeout，重试一次，再失败就放弃。

### R7: Sub-interpreter 路径 [低]

文档提到 Python 3.12+ sub-interpreter 作为未来绕 GIL 的路径。实际情况：

- PyO3 对 sub-interpreter 支持处于早期阶段
- 大量第三方库（numpy、httpx 等）不兼容 sub-interpreter
- Python 3.13 free-threading（no-GIL）是更可能的路径，但同样不成熟

**对策**：不依赖 sub-interpreter。当前设计在 GIL 约束下已经可工作（IO-heavy 场景）。future-proof 靠的是 Rust 侧做重活，不是 Python 侧绕 GIL。

---

## 待验证假设（原型 Checklist）

在进入完整实现前，需要一个最小原型验证以下假设：

| # | 假设 | 验证方式 | 通过标准 |
|---|------|---------|---------|
| 1 | PyO3 能驱动 Python generator（next/send/throw/close） | 100 行 Rust 原型 | 所有 generator 操作正确执行 |
| 2 | 多 tokio task 并发抢 GIL 的吞吐可接受 | N=1000 并发 generator benchmark | 吞吐 > 10 万 effect/s |
| 3 | GIL 释放期间（IO 执行中）其他 generator 可被服务 | 并发 IO + generator 交替测试 | 无死锁，无饥饿 |
| 4 | generator close/throw 在 supervision 场景下正确清理 | 模拟 actor stop/restart 场景 | 无资源泄漏，finally 块执行 |

**原型规模**：~100 行 Rust + ~30 行 Python，预计 1-2 天。

---

## 与现有代码的关系

- **`everything_is_an_actor/`** — 现有 Python actor runtime，换 Rust 引擎，保留 API 精神
- **`proto/rust-scala-demo/`** — Scala 原型，验证了 continuation 模型可行，方向转为 Python generator
- **`rust_core/`** — 已有的 Rust mailbox 探索，可复用
