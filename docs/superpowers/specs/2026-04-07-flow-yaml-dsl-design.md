# Flow YAML DSL

YAML 表示的 Flow ADT。每个 YAML 关键词精确对应一个 ADT variant，解析成 Flow 语法树后由 Interpreter 执行。

## 设计原则

- YAML 关键词 1:1 对应 Flow ADT variant，不发明新语义
- 可序列化的 variant 才进 YAML
- 含 callable 的 variant 有可序列化的 agent 替代版本（Notify/Tap/Guard），原版留在 Python
- 数据传递规则和代码完全一致，不加 as/input/$input 等 YAML 特有的路由语法

## 新增 ADT variant

现有含 callable 的 variant 不可序列化。新增 agent 化的替代版本，用 Flow（通常是 agent）替代 callable：

| 现有 variant（含 callable） | 新增 variant（可序列化） | 区别 |
|---------------------------|----------------------|------|
| `_DivertTo(source, side, when: Callable)` | `_Notify(source, side)` | 去掉 when，始终 fire-and-forget |
| `_AndThen(source, callback: Callable)` | `_Tap(source, side)` | agent 替代 callback |
| `_Filter(source, predicate: Callable)` | `_Guard(source, check)` | agent 替代 predicate，返回 bool |

另新增广播组合子：

| 缺失 | 新增 | 说明 |
|------|------|------|
| 无广播并行（同一 input 给所有 flow） | `broadcast(*flows)` | `pure(dup) + zip_all` 的封装 |

### 新增代码

```python
# flow/flow.py

@dataclass(frozen=True)
class _Notify(Flow[I, O]):
    """Fire-and-forget to side flow. Always fires, no predicate.
    Serializable alternative to _DivertTo."""
    source: Flow[I, O]
    side: Flow[O, Any]

@dataclass(frozen=True)
class _Tap(Flow[I, O]):
    """Synchronous side-effect via agent. Value passes through unchanged.
    Serializable alternative to _AndThen."""
    source: Flow[I, O]
    side: Flow[O, Any]

@dataclass(frozen=True)
class _Guard(Flow[I, O]):
    """Guard via agent. Agent returns bool — True passes, False raises.
    Serializable alternative to _Filter."""
    source: Flow[I, O]
    check: Flow[O, bool]
```

```python
# flow/combinators.py

def broadcast(*flows: Flow[I, O]) -> Flow[I, tuple[O, ...]]:
    """Same input to all flows, collect all outputs as tuple.
    All must succeed."""
    k = len(flows)
    if k < 2:
        raise ValueError("broadcast() requires at least 2 flows")
    return pure(lambda x, _k=k: (x,) * _k).flat_map(zip_all(*flows))
```

```python
# flow/interpreter.py

case _Notify(source=source, side=side):
    result = await self._interpret(source, input)
    ComposableFuture.eager(self._interpret(side, result))  # 后台，不等
    return result

case _Tap(source=source, side=side):
    result = await self._interpret(source, input)
    await self._interpret(side, result)  # 同步调，但丢弃 side 的输出
    return result

case _Guard(source=source, check=check):
    result = await self._interpret(source, input)
    passed = await self._interpret(check, result)
    if not passed:
        raise FlowFilterError(result)
    return result
```

```python
# flow/serialize.py — 新增序列化

case _Notify(source=source, side=side):
    return {"type": "Notify", "source": to_dict(source), "side": to_dict(side)}

case _Tap(source=source, side=side):
    return {"type": "Tap", "source": to_dict(source), "side": to_dict(side)}

case _Guard(source=source, check=check):
    return {"type": "Guard", "source": to_dict(source), "check": to_dict(check)}
```

### Flow 方法链 API

```python
class Flow(Generic[I, O]):
    # 现有
    def divert_to(self, side, when) -> Flow:     # 含 callable，不可序列化
    def and_then(self, callback) -> Flow:          # 含 callable，不可序列化
    def filter(self, predicate) -> Flow:           # 含 callable，不可序列化

    # 新增
    def notify(self, side: Flow[O, Any]) -> Flow[I, O]:
        """Fire-and-forget to side flow. Serializable."""
        return _Notify(source=self, side=side)

    def tap(self, side: Flow[O, Any]) -> Flow[I, O]:
        """Sync side-effect via agent. Serializable."""
        return _Tap(source=self, side=side)

    def guard(self, check: Flow[O, bool]) -> Flow[I, O]:
        """Guard via agent. Serializable."""
        return _Guard(source=self, check=check)
```

## ADT variant → YAML 完整映射

### 可 YAML 化

| ADT variant | YAML 语法 | 输入行为 | 输出行为 |
|-------------|-----------|---------|---------|
| `_Agent(cls, timeout)` | `agent: Name` | 接收上一步 output | execute 返回值 |
| `_FlatMap(first, next)` | `steps:` 列表 | 链式：O₁ = I₂ | 最后一步的 output |
| `_Zip(left, right)` | `each:` | 拆 tuple | `(left_out, right_out)` |
| `_ZipAll(flows)` | `each:` (N 项) | 拆 tuple/list | 结果 list |
| `broadcast(*flows)` | `all:` | **同一个 input** 广播 | 结果 tuple |
| `_Race(flows)` | `fastest of:` | **同一个 input** | 最快的 output |
| `at_least(n, flows)` | `best N of:` | **同一个 input** | `QuorumResult` |
| `_Branch(source, mapping)` | `route:` | 按类型路由 | 匹配分支的 output |
| `_FallbackTo(source, fallback)` | `if fails:` | 同一个 input | 成功方的 output |
| `_RecoverWith(source, handler)` | `recover:` | Exception 作为 input | handler 的 output |
| `_Loop(body, max_iter)` | `repeat:` + `max:` | Continue → 下轮 input | Done 的值 |
| `_Notify(source, side)` | `notify:` | source output 给 side（后台） | source output 不变 |
| `_Tap(source, side)` | `tap:` | source output 给 side（同步） | source output 不变 |
| `_Guard(source, check)` | `guard:` | source output 给 check agent | source output 不变（通过）或异常（拒绝） |

### 不可 YAML 化（含 callable，留在 Python）

| ADT variant | 可序列化替代 |
|-------------|------------|
| `_DivertTo(when: Callable)` | `_Notify`（去掉 when） |
| `_AndThen(callback: Callable)` | `_Tap`（agent 替代 callback） |
| `_Filter(predicate: Callable)` | `_Guard`（agent 替代 predicate） |
| `_Pure(f: Callable)` | 无替代——纯变换留在 Python |
| `_Map(f: Callable)` | 无替代——纯变换留在 Python |
| `_BranchOn(predicate: Callable)` | 用 `_Branch` + 类型路由替代 |
| `_Recover(handler: Callable)` | 用 `_RecoverWith` + agent 替代 |

## 数据传递规则

**和代码完全一致，无 YAML 特有规则。**

### flat_map（steps 列表）

```
A 的 output (类型 M) 就是 B 的 input (类型 M)
```

```yaml
steps:
  - agent: A     # I → M
  - agent: B     # M → O
```

### zip / zip_all（each）

```
输入必须是 tuple/list，拆开给各 flow
interpreter: left_input, right_input = input
```

```yaml
steps:
  - agent: Splitter       # I → (Part1, Part2)
  - each:
      - agent: ProcessA   # Part1 → ResultA
      - agent: ProcessB   # Part2 → ResultB
  - agent: Merger          # (ResultA, ResultB) → O
```

### broadcast（all）

```
同一个 input 给所有 flow，收集全部结果
内部: pure(lambda x: (x,)*k).flat_map(zip_all(*flows))
```

```yaml
steps:
  - agent: Researcher         # I → ResearchData
  - all:
      - agent: AnalystA       # ResearchData → Analysis
      - agent: AnalystB       # ResearchData → Analysis（同一个 input）
  - agent: Summarizer          # (Analysis, Analysis) → O
```

### race（fastest of）

```
所有 flow 拿同一个 input
interpreter: self._interpret(f, input) for f in flows
```

```yaml
steps:
  - fastest of:
      - agent: Google
      - agent: Bing
      - agent: DuckDuckGo
  - agent: Analyzer
```

### at_least（best N of）

```
所有 flow 拿同一个 input，至少 N 个成功
```

```yaml
steps:
  - best 2 of:
      - agent: AnalystA
      - agent: AnalystB
      - agent: AnalystC
  - agent: Summarizer      # QuorumResult → O
```

### branch（route）

```
source output 按 isinstance 匹配分支
```

```yaml
steps:
  - agent: Classifier
  - route:
      TechQuery: TechWriter
      CreativeQuery: CreativeWriter
      default: GeneralWriter
```

### fallback（if fails）

```
source 失败时，fallback 拿原始 input
```

```yaml
- agent: Writer
  if fails: BackupWriter
```

### notify（旁路）

```
source output 给 side（后台 fire-and-forget），主路径不受影响
```

```yaml
- agent: Writer
  notify: AuditLogger
```

### tap（同步副作用）

```
source output 给 side（同步调用），丢弃 side 输出，主路径返回 source output
```

```yaml
- agent: Writer
  tap: ProgressTracker
```

### guard（守卫）

```
source output 给 check agent，check 返回 True 则继续，False 则抛 FlowFilterError
```

```yaml
- agent: Writer
  guard: QualityChecker
```

### loop（repeat）

```
body 返回 Continue(val) → val 作为下轮 input
body 返回 Done(val) → 最终 output
```

```yaml
- repeat:
    agent: Refiner
  max: 10
```

## 完整 YAML 语法

```yaml
flow: PipelineName
steps:
  # 单 agent
  - agent: AgentName
  - agent: AgentName
    max: 30s

  # 顺序（嵌套 steps = 子 flat_map 链）
  - steps:
      - agent: A
      - agent: B

  # 分发并行（zip —— 拆 input tuple）
  - each:
      - agent: A               # input[0]
      - agent: B               # input[1]

  # 广播并行（broadcast —— 同一个 input）
  - all:
      - agent: A
      - agent: B

  # 竞争（race）
  - fastest of:
      - agent: A
      - agent: B

  # 法定人数（at_least）
  - best 2 of:
      - agent: A
      - agent: B
      - agent: C

  # 分支（branch）
  - route:
      TypeA: AgentForA
      TypeB: AgentForB
      default: AgentDefault

  # 兜底（fallback_to）
  - agent: Primary
    if fails: Backup

  # 异常恢复（recover_with）
  - agent: Risky
    recover: ErrorHandler

  # 循环（loop）
  - repeat:
      agent: Refiner
    max: 10

  # 旁路通知（notify —— fire-and-forget）
  - agent: Writer
    notify: AuditLogger

  # 同步副作用（tap）
  - agent: Writer
    tap: ProgressTracker

  # 守卫（guard —— agent 返回 bool）
  - agent: Writer
    guard: QualityChecker

  # 子 flow 引用
  - flow: OtherPipeline

  # 修饰可叠加
  - agent: Writer
    if fails: BackupWriter
    notify: AuditLogger
    guard: QualityChecker
    max: 60s
```

## 解析规则

```python
def parse(node) -> Flow:
    match node:
        case {"agent": name, **opts}:
            flow = _Agent(cls=registry[name], timeout=parse_timeout(opts))
            if "if fails" in opts:
                flow = _FallbackTo(source=flow, fallback=parse({"agent": opts["if fails"]}))
            if "recover" in opts:
                flow = _RecoverWith(source=flow, handler=parse({"agent": opts["recover"]}))
            if "notify" in opts:
                flow = _Notify(source=flow, side=parse({"agent": opts["notify"]}))
            if "tap" in opts:
                flow = _Tap(source=flow, side=parse({"agent": opts["tap"]}))
            if "guard" in opts:
                flow = _Guard(source=flow, check=parse({"agent": opts["guard"]}))
            return flow

        case {"steps": items}:
            flows = [parse(item) for item in items]
            return reduce(lambda a, b: _FlatMap(first=a, next=b), flows)

        case {"each": items}:
            flows = [parse(item) for item in items]
            if len(flows) == 2:
                return _Zip(left=flows[0], right=flows[1])
            return _ZipAll(flows=tuple(flows))

        case {"all": items}:
            flows = [parse(item) for item in items]
            return broadcast(*flows)

        case {"fastest of": items}:
            return _Race(flows=tuple(parse(item) for item in items))

        case {"best N of": items}:
            return at_least(n, *[parse(item) for item in items])

        case {"route": mapping}:
            ...

        case {"repeat": body, **opts}:
            return _Loop(body=parse(body), max_iter=opts.get("max", 10))

        case {"flow": name}:
            return flow_registry[name]
```

## 混合使用

YAML 定义可序列化骨架，Python 补充含 callable 的部分：

```python
# YAML 定义结构
pipeline = Flow.from_yaml("research_pipeline.yaml", registry=agent_registry)

# Python 补充 callable 部分
pipeline = (
    pipeline
    .map(lambda result: result.summary)       # 纯变换（不可序列化）
    .filter(lambda r: len(r) > 100)           # 过滤（不可序列化）
    .divert_to(agent(Logger), when=is_error)  # 条件旁路（不可序列化）
)

result = await system.run_flow(pipeline, input)
```

## 文件改动

| 文件 | 改动 |
|------|------|
| `flow/flow.py` | 新增 `_Notify`, `_Tap`, `_Guard`；`Flow` 加 `notify()`, `tap()`, `guard()` 方法 |
| `flow/combinators.py` | 新增 `broadcast()` |
| `flow/interpreter.py` | 新增三个 case |
| `flow/serialize.py` | 新增三个序列化 + 反序列化 |
| `flow/yaml_parser.py` | **新建**——YAML → Flow ADT 解析器 |

## JSON 语法

和 YAML 覆盖同一组 variant。`to_dict` / `from_dict` 的 JSON 格式。每个节点有 `type` 字段标识 variant。

### 全部节点类型

```json
// Agent
{"type": "Agent", "cls": "Researcher", "timeout": 30.0}

// FlatMap（顺序）
{"type": "FlatMap",
 "first": {"type": "Agent", "cls": "Researcher"},
 "next":  {"type": "Agent", "cls": "Writer"}}

// Zip（分发并行 —— 拆 tuple）
{"type": "Zip",
 "left":  {"type": "Agent", "cls": "A"},
 "right": {"type": "Agent", "cls": "B"}}

// ZipAll（N-way 分发）
{"type": "ZipAll",
 "flows": [
   {"type": "Agent", "cls": "A"},
   {"type": "Agent", "cls": "B"},
   {"type": "Agent", "cls": "C"}
 ]}

// Broadcast（广播并行 —— 同一个 input）
{"type": "Broadcast",
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"}
 ]}

// Race（竞争）
{"type": "Race",
 "flows": [
   {"type": "Agent", "cls": "Google"},
   {"type": "Agent", "cls": "Bing"}
 ]}

// AtLeast（法定人数）
{"type": "AtLeast",
 "n": 2,
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"},
   {"type": "Agent", "cls": "AnalystC"}
 ]}

// Branch（类型路由）
{"type": "Branch",
 "source": {"type": "Agent", "cls": "Classifier"},
 "mapping": {
   "TechQuery":     {"type": "Agent", "cls": "TechWriter"},
   "CreativeQuery": {"type": "Agent", "cls": "CreativeWriter"}
 }}

// FallbackTo（兜底）
{"type": "FallbackTo",
 "source":   {"type": "Agent", "cls": "Writer"},
 "fallback": {"type": "Agent", "cls": "BackupWriter"}}

// RecoverWith（异常恢复）
{"type": "RecoverWith",
 "source":  {"type": "Agent", "cls": "RiskyAgent"},
 "handler": {"type": "Agent", "cls": "ErrorHandler"}}

// Loop（循环）
{"type": "Loop",
 "body":     {"type": "Agent", "cls": "Refiner"},
 "max_iter": 10}

// LoopWithState（带状态循环）
{"type": "LoopWithState",
 "body":     {"type": "Agent", "cls": "StatefulRefiner"},
 "max_iter": 10}

// Notify（旁路 fire-and-forget）
{"type": "Notify",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "AuditLogger"}}

// Tap（同步副作用）
{"type": "Tap",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "ProgressTracker"}}

// Guard（守卫）
{"type": "Guard",
 "source": {"type": "Agent", "cls": "Writer"},
 "check":  {"type": "Agent", "cls": "QualityChecker"}}
```

### 不可 JSON 化（含 callable）

| type | 原因 |
|------|------|
| Pure | f 是 callable |
| Map | f 是 callable |
| Filter | predicate 是 callable |
| AndThen | callback 是 callable |
| DivertTo | when 是 callable |
| BranchOn | predicate 是 callable |
| Recover | handler 是 callable |

### JSON ↔ YAML 对照

| JSON `type` | YAML 关键词 | 说明 |
|-------------|-----------|------|
| `Agent` | `agent:` | 单 agent |
| `FlatMap` | `steps:` 列表 | 顺序链 |
| `Zip` | `each:` (2 项) | 分发并行 |
| `ZipAll` | `each:` (N 项) | N-way 分发 |
| `Broadcast` | `all:` | 广播并行 |
| `Race` | `fastest of:` | 竞争 |
| `AtLeast` | `best N of:` | 法定人数 |
| `Branch` | `route:` | 类型路由 |
| `FallbackTo` | `if fails:` | 兜底 |
| `RecoverWith` | `recover:` | 异常恢复 |
| `Loop` | `repeat:` | 循环 |
| `Notify` | `notify:` | 旁路 |
| `Tap` | `tap:` | 同步副作用 |
| `Guard` | `guard:` | 守卫 |

### 完整示例

```json
{
  "type": "FlatMap",
  "first": {
    "type": "AtLeast",
    "n": 2,
    "flows": [
      {"type": "Agent", "cls": "Google"},
      {"type": "Agent", "cls": "Bing"},
      {"type": "Agent", "cls": "DuckDuckGo"}
    ]
  },
  "next": {
    "type": "FlatMap",
    "first": {
      "type": "Notify",
      "source": {
        "type": "Broadcast",
        "flows": [
          {"type": "Agent", "cls": "Researcher"},
          {"type": "Agent", "cls": "Analyst"}
        ]
      },
      "side": {"type": "Agent", "cls": "AuditLogger"}
    },
    "next": {
      "type": "Guard",
      "source": {
        "type": "FallbackTo",
        "source":   {"type": "Agent", "cls": "Writer"},
        "fallback": {"type": "Agent", "cls": "BackupWriter"}
      },
      "check": {"type": "Agent", "cls": "QualityChecker"}
    }
  }
}
```

等价 YAML：

```yaml
flow: ResearchReport
steps:
  - best 2 of:
      - agent: Google
      - agent: Bing
      - agent: DuckDuckGo
  - all:
      - agent: Researcher
      - agent: Analyst
    notify: AuditLogger
  - agent: Writer
    if fails: BackupWriter
    guard: QualityChecker
```

等价 Python：

```python
pipeline = (
    at_least(2, agent(Google), agent(Bing), agent(DuckDuckGo))
    .flat_map(
        broadcast(agent(Researcher), agent(Analyst))
        .notify(agent(AuditLogger))
    )
    .flat_map(
        agent(Writer)
        .fallback_to(agent(BackupWriter))
        .guard(agent(QualityChecker))
    )
)
```

## 三列对比：YAML / JSON / Python

逐 variant 展示三种写法。

### Agent

```yaml
# YAML
- agent: Researcher
  max: 30s
```
```json
// JSON
{"type": "Agent", "cls": "Researcher", "timeout": 30.0}
```
```python
# Python
agent(Researcher, timeout=30.0)
```

### FlatMap（顺序）

```yaml
# YAML
steps:
  - agent: Researcher
  - agent: Writer
```
```json
// JSON
{"type": "FlatMap",
 "first": {"type": "Agent", "cls": "Researcher"},
 "next":  {"type": "Agent", "cls": "Writer"}}
```
```python
# Python
agent(Researcher).flat_map(agent(Writer))
```

### Zip（分发并行 —— 拆 tuple）

```yaml
# YAML
- each:
    - agent: ProcessA
    - agent: ProcessB
```
```json
// JSON
{"type": "Zip",
 "left":  {"type": "Agent", "cls": "ProcessA"},
 "right": {"type": "Agent", "cls": "ProcessB"}}
```
```python
# Python
agent(ProcessA).zip(agent(ProcessB))
```

### Broadcast（广播并行 —— 同一个 input）

```yaml
# YAML
- all:
    - agent: AnalystA
    - agent: AnalystB
```
```json
// JSON
{"type": "Broadcast",
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"}]}
```
```python
# Python
broadcast(agent(AnalystA), agent(AnalystB))
```

### Race（竞争）

```yaml
# YAML
- fastest of:
    - agent: Google
    - agent: Bing
```
```json
// JSON
{"type": "Race",
 "flows": [
   {"type": "Agent", "cls": "Google"},
   {"type": "Agent", "cls": "Bing"}]}
```
```python
# Python
race(agent(Google), agent(Bing))
```

### AtLeast（法定人数）

```yaml
# YAML
- best 2 of:
    - agent: AnalystA
    - agent: AnalystB
    - agent: AnalystC
```
```json
// JSON
{"type": "AtLeast", "n": 2,
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"},
   {"type": "Agent", "cls": "AnalystC"}]}
```
```python
# Python
at_least(2, agent(AnalystA), agent(AnalystB), agent(AnalystC))
```

### Branch（类型路由）

```yaml
# YAML
- route:
    TechQuery: TechWriter
    CreativeQuery: CreativeWriter
```
```json
// JSON
{"type": "Branch",
 "source": {"type": "Agent", "cls": "Classifier"},
 "mapping": {
   "TechQuery":     {"type": "Agent", "cls": "TechWriter"},
   "CreativeQuery": {"type": "Agent", "cls": "CreativeWriter"}}}
```
```python
# Python
agent(Classifier).branch({
    TechQuery: agent(TechWriter),
    CreativeQuery: agent(CreativeWriter),
})
```

### FallbackTo（兜底）

```yaml
# YAML
- agent: Writer
  if fails: BackupWriter
```
```json
// JSON
{"type": "FallbackTo",
 "source":   {"type": "Agent", "cls": "Writer"},
 "fallback": {"type": "Agent", "cls": "BackupWriter"}}
```
```python
# Python
agent(Writer).fallback_to(agent(BackupWriter))
```

### RecoverWith（异常恢复）

```yaml
# YAML
- agent: Risky
  recover: ErrorHandler
```
```json
// JSON
{"type": "RecoverWith",
 "source":  {"type": "Agent", "cls": "Risky"},
 "handler": {"type": "Agent", "cls": "ErrorHandler"}}
```
```python
# Python
agent(Risky).recover_with(agent(ErrorHandler))
```

### Loop（循环）

```yaml
# YAML
- repeat:
    agent: Refiner
  max: 10
```
```json
// JSON
{"type": "Loop",
 "body": {"type": "Agent", "cls": "Refiner"},
 "max_iter": 10}
```
```python
# Python
loop(agent(Refiner), max_iter=10)
```

### Notify（旁路 fire-and-forget）

```yaml
# YAML
- agent: Writer
  notify: AuditLogger
```
```json
// JSON
{"type": "Notify",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "AuditLogger"}}
```
```python
# Python
agent(Writer).notify(agent(AuditLogger))
```

### Tap（同步副作用）

```yaml
# YAML
- agent: Writer
  tap: ProgressTracker
```
```json
// JSON
{"type": "Tap",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "ProgressTracker"}}
```
```python
# Python
agent(Writer).tap(agent(ProgressTracker))
```

### Guard（守卫）

```yaml
# YAML
- agent: Writer
  guard: QualityChecker
```
```json
// JSON
{"type": "Guard",
 "source": {"type": "Agent", "cls": "Writer"},
 "check":  {"type": "Agent", "cls": "QualityChecker"}}
```
```python
# Python
agent(Writer).guard(agent(QualityChecker))
```

### 含 callable 的 variant（仅 Python）

| 操作 | Python | YAML/JSON |
|------|--------|-----------|
| 纯变换 | `.map(lambda x: x.upper())` | 不可 |
| 条件过滤 | `.filter(lambda x: len(x) > 10)` | 不可（用 `.guard(agent(Checker))` 替代） |
| 条件旁路 | `.divert_to(side, when=pred)` | 不可（用 `.notify(side)` 替代，无条件） |
| 同步回调 | `.and_then(print)` | 不可（用 `.tap(agent(Logger))` 替代） |
| 条件分支 | `.branch_on(pred, a, b)` | 不可（用 `.branch({Type: flow})` 替代） |
| 异常处理 | `.recover(lambda e: default)` | 不可（用 `.recover_with(agent(Handler))` 替代） |

## 三者互转

```
YAML ←→ Flow ADT ←→ JSON (to_dict/from_dict)
```

YAML 面向人类编写，JSON 面向机器传输，Python 面向代码组合。三者覆盖同一组可序列化 variant，无损互转。
