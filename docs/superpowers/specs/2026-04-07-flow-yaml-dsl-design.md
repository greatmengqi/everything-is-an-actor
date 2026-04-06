# Flow DSL Spec

Flow ADT 的三种等价表示。三者覆盖同一组可序列化 variant，无损互转。

```
YAML（人类编写） ←→ Flow ADT ←→ JSON（机器传输）
                        ↕
                  Python（代码组合）
```

## 三列对比

| 组合子 | YAML | JSON | Python |
|--------|------|------|--------|
| 单 agent | `agent: A` | `Agent(cls)` | `agent(A)` |
| agent+参数 | `agent: {name: A, timeout: 30}` | `Agent(cls, timeout)` | `agent(A, timeout=30)` |
| 顺序 | `steps: [A, B, C]` | `FlatMap(first, next)` | `a.flat_map(b)` |
| 分发并行 | `each: [A, B]` | `Zip(left, right)` | `a.zip(b)` |
| 广播并行 | `all: [A, B]` | `Broadcast(flows)` | `broadcast(a, b)` |
| 竞争 | `race: [A, B, C]` | `Race(flows)` | `race(a, b, c)` |
| 法定人数 | `at_least: {n: 2, flows: [A,B,C]}` | `AtLeast(n, flows)` | `at_least(2, a, b, c)` |
| 类型路由 | `branch: {source: X, mapping: {...}}` | `Branch(source, mapping)` | `x.branch({T: a})` |
| 兜底 | `fallback_to: {source: A, fallback: B}` | `FallbackTo(source, fallback)` | `a.fallback_to(b)` |
| 异常恢复 | `recover_with: {source: A, handler: B}` | `RecoverWith(source, handler)` | `a.recover_with(b)` |
| 循环 | `loop: {body: A, max_iter: 10}` | `Loop(body, max_iter)` | `loop(a, max_iter=10)` |
| 旁路通知 | `notify: {source: A, side: L}` | `Notify(source, side)` | `a.notify(l)` |
| 同步副作用 | `tap: {source: A, side: T}` | `Tap(source, side)` | `a.tap(t)` |
| 守卫 | `guard: {source: A, check: C}` | `Guard(source, check)` | `a.guard(c)` |
| 子flow | `flow: Name` | 展开为 ADT | 变量引用 |

## 数据传递

| 组合子 | 类型签名 | 输入 | 输出 |
|--------|---------|------|------|
| flat_map | `Flow[I,M] → Flow[M,O]` = `Flow[I,O]` | M 链式传递 | O |
| zip | `Flow[I,O] × Flow[I2,O2]` = `Flow[(I,I2), (O,O2)]` | 拆 tuple | tuple |
| broadcast | `Flow[I,O] × N` = `Flow[I, (O,...)]` | **同一个 I** 广播 | tuple |
| race | `Flow[I,O] × N` = `Flow[I, O]` | **同一个 I** 广播 | 最快的 O |
| at_least | `Flow[I,O] × N` = `Flow[I, QuorumResult[O]]` | **同一个 I** 广播 | QuorumResult |
| branch | `Flow[I, A\|B] → {A: Flow[A,O], B: Flow[B,O]}` | isinstance 路由 | O |
| fallback_to | `Flow[I,O] × Flow[I,O]` = `Flow[I, O]` | **同一个 I** | 成功方的 O |
| recover_with | `Flow[I,O] × Flow[Exception,O]` = `Flow[I, O]` | Exception | O |
| loop | `Flow[I, Continue[I]\|Done[O]]` = `Flow[I, O]` | Continue → 下轮 I | Done 的 O |
| notify | `Flow[I,O] × Flow[O,Any]` = `Flow[I, O]` | O 给 side（后台） | O **穿透** |
| tap | `Flow[I,O] × Flow[O,Any]` = `Flow[I, O]` | O 给 side（同步） | O **穿透** |
| guard | `Flow[I,O] × Flow[O,bool]` = `Flow[I, O]` | O 给 check | O **穿透**（或异常） |

## 新增代码

| 文件 | 改动 |
|------|------|
| `flow/flow.py` | 新增 `_Notify`, `_Tap`, `_Guard`；`Flow` 加 `notify()`, `tap()`, `guard()` |
| `flow/combinators.py` | 新增 `broadcast()` |
| `flow/interpreter.py` | 新增三个 case |
| `flow/serialize.py` | 新增四个 to_dict/from_dict |
| `flow/yaml_parser.py` | **新建** |

## 完整示例

```yaml
flow: ResearchReport
steps:
  - at_least:
      n: 2
      flows:
        - agent: Google
        - agent: Bing
        - agent: DuckDuckGo
  - all:
      - agent: Researcher
      - agent: Analyst
  - guard:
      source:
        notify:
          source:
            fallback_to:
              source: {agent: Writer, timeout: 60}
              fallback: {agent: BackupWriter}
          side: {agent: AuditLogger}
      check: {agent: QualityChecker}
  - loop:
      body: {agent: Reviewer}
      max_iter: 5
```

```json
{"type":"FlatMap",
 "first":{"type":"AtLeast","n":2,
   "flows":[{"type":"Agent","cls":"Google"},
            {"type":"Agent","cls":"Bing"},
            {"type":"Agent","cls":"DuckDuckGo"}]},
 "next":{"type":"FlatMap",
   "first":{"type":"Broadcast",
     "flows":[{"type":"Agent","cls":"Researcher"},
              {"type":"Agent","cls":"Analyst"}]},
   "next":{"type":"FlatMap",
     "first":{"type":"Guard",
       "source":{"type":"Notify",
         "source":{"type":"FallbackTo",
           "source":{"type":"Agent","cls":"Writer","timeout":60},
           "fallback":{"type":"Agent","cls":"BackupWriter"}},
         "side":{"type":"Agent","cls":"AuditLogger"}},
       "check":{"type":"Agent","cls":"QualityChecker"}},
     "next":{"type":"Loop",
       "body":{"type":"Agent","cls":"Reviewer"},
       "max_iter":5}}}}
```

```python
pipeline = (
    at_least(2, agent(Google), agent(Bing), agent(DuckDuckGo))
    .flat_map(broadcast(agent(Researcher), agent(Analyst)))
    .flat_map(
        agent(Writer, timeout=60)
        .fallback_to(agent(BackupWriter))
        .notify(agent(AuditLogger))
        .guard(agent(QualityChecker))
    )
    .flat_map(loop(agent(Reviewer), max_iter=5))
)
```

## 混合使用

YAML 定义可序列化骨架，Python 补充 callable：

```python
pipeline = Flow.from_yaml("pipeline.yaml", registry=agent_registry)
pipeline = pipeline.map(lambda r: r.summary).filter(lambda r: len(r) > 100)
result = await system.run_flow(pipeline, input)
```
