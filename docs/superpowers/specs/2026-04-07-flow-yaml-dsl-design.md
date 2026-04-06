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

| 组合子 | 输入行为 | 输出行为 |
|--------|---------|---------|
| flat_map | 上一步 output → 下一步 input | 最后一步的 output |
| zip | **拆 tuple**：`a_in, b_in = input` | `(a_out, b_out)` tuple |
| broadcast | **同一个 input** 给所有 | 全部结果 tuple |
| race | **同一个 input** 给所有 | 最快的 output |
| at_least | **同一个 input** 给所有 | `QuorumResult(succeeded, failed)` |
| branch | source output 按类型路由 | 匹配分支的 output |
| fallback_to | source 和 fallback 拿**同一个 input** | 成功方的 output |
| recover_with | handler 拿 **Exception** 作为 input | handler 的 output |
| loop | `Continue(val)` → val 作下轮 input | `Done(val)` 的 val |
| notify | source output 给 side（后台） | source output **不变** |
| tap | source output 给 side（同步） | source output **不变** |
| guard | source output 给 check agent | source output **不变**（或异常） |

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
