# Flow DSL Spec

Flow ADT 的三种等价表示：YAML（人类编写）、JSON（机器传输）、Python（代码组合）。
三者覆盖同一组可序列化 variant，无损互转。

```
YAML ←→ Flow ADT ←→ JSON (to_dict/from_dict)
              ↕
           Python
```

## 设计原则

- 每个关键词 1:1 对应一个 ADT variant
- YAML 关键词是单个无空格标识符，参数在下一级
- 简单场景用简写（`agent: Name`），有参数展开为 dict
- 含 callable 的 variant 不可序列化——有 agent 化的替代版本
- 数据传递和代码一致，不发明 YAML 特有的路由语法

## 全部 variant 三列对比

### agent — 单个 Agent

```yaml
# YAML（简写）
- agent: Researcher

# YAML（带参数）
- agent:
    name: Writer
    timeout: 30s
    fallback: BackupWriter
    notify: AuditLogger
    tap: ProgressTracker
    guard: QualityChecker
```

```json
{"type": "Agent", "cls": "Researcher", "timeout": 30.0}
```

```python
agent(Researcher, timeout=30.0)
```

修饰（fallback/notify/tap/guard）在 JSON 和 Python 中是外层包装：

```json
{"type": "Guard",
 "source": {"type": "Notify",
   "source": {"type": "FallbackTo",
     "source":   {"type": "Agent", "cls": "Writer"},
     "fallback": {"type": "Agent", "cls": "BackupWriter"}},
   "side": {"type": "Agent", "cls": "AuditLogger"}},
 "check": {"type": "Agent", "cls": "QualityChecker"}}
```

```python
(agent(Writer)
  .fallback_to(agent(BackupWriter))
  .notify(agent(AuditLogger))
  .guard(agent(QualityChecker)))
```

---

### steps — 顺序（FlatMap）

数据传递：上一步 output = 下一步 input

```yaml
steps:
  - agent: Researcher
  - agent: Writer
  - agent: Reviewer
```

```json
{"type": "FlatMap",
 "first": {"type": "Agent", "cls": "Researcher"},
 "next": {"type": "FlatMap",
   "first": {"type": "Agent", "cls": "Writer"},
   "next":  {"type": "Agent", "cls": "Reviewer"}}}
```

```python
agent(Researcher).flat_map(agent(Writer)).flat_map(agent(Reviewer))
```

---

### each — 分发并行（Zip）

数据传递：拆 tuple，一人一份。`left_in, right_in = input`

```yaml
- each:
    - agent: ProcessA
    - agent: ProcessB
```

```json
{"type": "Zip",
 "left":  {"type": "Agent", "cls": "ProcessA"},
 "right": {"type": "Agent", "cls": "ProcessB"}}
```

```python
agent(ProcessA).zip(agent(ProcessB))
```

N-way 用 ZipAll：

```json
{"type": "ZipAll",
 "flows": [
   {"type": "Agent", "cls": "A"},
   {"type": "Agent", "cls": "B"},
   {"type": "Agent", "cls": "C"}]}
```

```python
zip_all(agent(A), agent(B), agent(C))
```

---

### all — 广播并行（Broadcast）

数据传递：**同一个 input** 给所有 flow，收集全部结果为 tuple

```yaml
- all:
    - agent: AnalystA
    - agent: AnalystB
```

```json
{"type": "Broadcast",
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"}]}
```

```python
broadcast(agent(AnalystA), agent(AnalystB))
```

---

### race — 竞争（Race）

数据传递：**同一个 input**，取最快的 output

```yaml
- race:
    - agent: Google
    - agent: Bing
    - agent: DuckDuckGo
```

```json
{"type": "Race",
 "flows": [
   {"type": "Agent", "cls": "Google"},
   {"type": "Agent", "cls": "Bing"},
   {"type": "Agent", "cls": "DuckDuckGo"}]}
```

```python
race(agent(Google), agent(Bing), agent(DuckDuckGo))
```

---

### quorum — 法定人数（AtLeast）

数据传递：**同一个 input**，至少 n 个成功，输出 QuorumResult

```yaml
- quorum:
    n: 2
    flows:
      - agent: AnalystA
      - agent: AnalystB
      - agent: AnalystC
```

```json
{"type": "AtLeast", "n": 2,
 "flows": [
   {"type": "Agent", "cls": "AnalystA"},
   {"type": "Agent", "cls": "AnalystB"},
   {"type": "Agent", "cls": "AnalystC"}]}
```

```python
at_least(2, agent(AnalystA), agent(AnalystB), agent(AnalystC))
```

---

### route — 类型路由（Branch）

数据传递：source output 按 isinstance 匹配分支

```yaml
- route:
    source: Classifier
    mapping:
      TechQuery: TechWriter
      CreativeQuery: CreativeWriter
      default: GeneralWriter
```

```json
{"type": "Branch",
 "source": {"type": "Agent", "cls": "Classifier"},
 "mapping": {
   "TechQuery":     {"type": "Agent", "cls": "TechWriter"},
   "CreativeQuery": {"type": "Agent", "cls": "CreativeWriter"}}}
```

```python
agent(Classifier).branch({
    TechQuery: agent(TechWriter),
    CreativeQuery: agent(CreativeWriter),
})
```

---

### fallback — 兜底（FallbackTo）

数据传递：primary 和 backup 拿**同一个 input**

```yaml
- fallback:
    primary: Writer
    backup: BackupWriter
```

```json
{"type": "FallbackTo",
 "source":   {"type": "Agent", "cls": "Writer"},
 "fallback": {"type": "Agent", "cls": "BackupWriter"}}
```

```python
agent(Writer).fallback_to(agent(BackupWriter))
```

---

### recover — 异常恢复（RecoverWith）

数据传递：handler 拿 Exception 作为 input

```yaml
- recover:
    source: RiskyAgent
    handler: ErrorHandler
```

```json
{"type": "RecoverWith",
 "source":  {"type": "Agent", "cls": "RiskyAgent"},
 "handler": {"type": "Agent", "cls": "ErrorHandler"}}
```

```python
agent(RiskyAgent).recover_with(agent(ErrorHandler))
```

---

### loop — 循环（Loop）

数据传递：body 返回 `Continue(val)` → val 作为下轮 input；`Done(val)` → 最终 output

```yaml
- loop:
    body: Refiner
    max_iter: 10
```

```json
{"type": "Loop",
 "body": {"type": "Agent", "cls": "Refiner"},
 "max_iter": 10}
```

```python
loop(agent(Refiner), max_iter=10)
```

---

### notify — 旁路 fire-and-forget（Notify）

数据传递：source output 给 side（后台），主路径返回 source output 不变

```yaml
# 简写（agent 修饰）
- agent:
    name: Writer
    notify: AuditLogger

# 独立写法
- notify:
    source: Writer
    side: AuditLogger
```

```json
{"type": "Notify",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "AuditLogger"}}
```

```python
agent(Writer).notify(agent(AuditLogger))
```

---

### tap — 同步副作用（Tap）

数据传递：source output 给 side（同步），丢弃 side 输出，主路径返回 source output

```yaml
- agent:
    name: Writer
    tap: ProgressTracker
```

```json
{"type": "Tap",
 "source": {"type": "Agent", "cls": "Writer"},
 "side":   {"type": "Agent", "cls": "ProgressTracker"}}
```

```python
agent(Writer).tap(agent(ProgressTracker))
```

---

### guard — 守卫（Guard）

数据传递：source output 给 check agent，返回 True 继续，False 抛 FlowFilterError

```yaml
- agent:
    name: Writer
    guard: QualityChecker
```

```json
{"type": "Guard",
 "source": {"type": "Agent", "cls": "Writer"},
 "check":  {"type": "Agent", "cls": "QualityChecker"}}
```

```python
agent(Writer).guard(agent(QualityChecker))
```

---

### flow — 子流程引用

```yaml
- flow: SearchPipeline
```

```json
// 引用已注册的 flow，展开为其 ADT
```

```python
search_pipeline  # 一个 Flow 变量，直接组合
```

---

## 含 callable 的 variant（仅 Python）

| 操作 | Python | 可序列化替代（YAML/JSON） |
|------|--------|------------------------|
| 纯变换 | `.map(fn)` | 无——留在 Python |
| 条件过滤 | `.filter(pred)` | `guard:` + check agent |
| 条件旁路 | `.divert_to(side, when=pred)` | `notify:` （无条件） |
| 同步回调 | `.and_then(callback)` | `tap:` + side agent |
| 条件分支 | `.branch_on(pred, a, b)` | `route:` + 类型路由 |
| 异常处理 | `.recover(fn)` | `recover:` + handler agent |

## 新增代码

### flow/flow.py

```python
@dataclass(frozen=True)
class _Notify(Flow[I, O]):
    """Fire-and-forget to side flow. Serializable alternative to _DivertTo."""
    source: Flow[I, O]
    side: Flow[O, Any]

@dataclass(frozen=True)
class _Tap(Flow[I, O]):
    """Sync side-effect via agent. Serializable alternative to _AndThen."""
    source: Flow[I, O]
    side: Flow[O, Any]

@dataclass(frozen=True)
class _Guard(Flow[I, O]):
    """Guard via agent. Serializable alternative to _Filter."""
    source: Flow[I, O]
    check: Flow[O, bool]
```

Flow 方法链：

```python
class Flow(Generic[I, O]):
    def notify(self, side: Flow[O, Any]) -> Flow[I, O]:
        return _Notify(source=self, side=side)

    def tap(self, side: Flow[O, Any]) -> Flow[I, O]:
        return _Tap(source=self, side=side)

    def guard(self, check: Flow[O, bool]) -> Flow[I, O]:
        return _Guard(source=self, check=check)
```

### flow/combinators.py

```python
def broadcast(*flows: Flow[I, O]) -> Flow[I, tuple[O, ...]]:
    """Same input to all flows, collect all outputs as tuple."""
    k = len(flows)
    if k < 2:
        raise ValueError("broadcast() requires at least 2 flows")
    return pure(lambda x, _k=k: (x,) * _k).flat_map(zip_all(*flows))
```

### flow/interpreter.py

```python
case _Notify(source=source, side=side):
    result = await self._interpret(source, input)
    ComposableFuture.eager(self._interpret(side, result))
    return result

case _Tap(source=source, side=side):
    result = await self._interpret(source, input)
    await self._interpret(side, result)
    return result

case _Guard(source=source, check=check):
    result = await self._interpret(source, input)
    passed = await self._interpret(check, result)
    if not passed:
        raise FlowFilterError(result)
    return result
```

### flow/serialize.py

新增 Notify/Tap/Guard/Broadcast 的 to_dict/from_dict。

### flow/yaml_parser.py（新建）

YAML → Flow ADT 解析器。

## 文件改动总结

| 文件 | 改动 |
|------|------|
| `flow/flow.py` | 新增 `_Notify`, `_Tap`, `_Guard`；`Flow` 加方法 |
| `flow/combinators.py` | 新增 `broadcast()` |
| `flow/interpreter.py` | 新增三个 case |
| `flow/serialize.py` | 新增四个序列化 |
| `flow/yaml_parser.py` | **新建** |

## 完整示例

```yaml
flow: ResearchReport
steps:
  - quorum:
      n: 2
      flows:
        - agent: Google
        - agent: Bing
        - agent: DuckDuckGo
  - all:
      - agent: Researcher
      - agent: Analyst
  - agent:
      name: Writer
      fallback: BackupWriter
      notify: AuditLogger
      guard: QualityChecker
      timeout: 60s
  - loop:
      body: Reviewer
      max_iter: 5
```

```json
{"type": "FlatMap",
 "first": {"type": "AtLeast", "n": 2,
   "flows": [
     {"type": "Agent", "cls": "Google"},
     {"type": "Agent", "cls": "Bing"},
     {"type": "Agent", "cls": "DuckDuckGo"}]},
 "next": {"type": "FlatMap",
   "first": {"type": "Broadcast",
     "flows": [
       {"type": "Agent", "cls": "Researcher"},
       {"type": "Agent", "cls": "Analyst"}]},
   "next": {"type": "FlatMap",
     "first": {"type": "Guard",
       "source": {"type": "Notify",
         "source": {"type": "FallbackTo",
           "source":   {"type": "Agent", "cls": "Writer", "timeout": 60.0},
           "fallback": {"type": "Agent", "cls": "BackupWriter"}},
         "side": {"type": "Agent", "cls": "AuditLogger"}},
       "check": {"type": "Agent", "cls": "QualityChecker"}},
     "next": {"type": "Loop",
       "body": {"type": "Agent", "cls": "Reviewer"},
       "max_iter": 5}}}}
```

```python
pipeline = (
    at_least(2, agent(Google), agent(Bing), agent(DuckDuckGo))
    .flat_map(broadcast(agent(Researcher), agent(Analyst)))
    .flat_map(
        agent(Writer, timeout=60.0)
        .fallback_to(agent(BackupWriter))
        .notify(agent(AuditLogger))
        .guard(agent(QualityChecker))
    )
    .flat_map(loop(agent(Reviewer), max_iter=5))
)
```

## 混合使用

YAML 定义可序列化骨架，Python 补充含 callable 的部分：

```python
pipeline = Flow.from_yaml("pipeline.yaml", registry=agent_registry)
pipeline = (
    pipeline
    .map(lambda r: r.summary)
    .filter(lambda r: len(r) > 100)
)
result = await system.run_flow(pipeline, input)
```
