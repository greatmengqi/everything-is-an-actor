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
| **单 agent** | `agent: Researcher` | `{"type":"Agent","cls":"Researcher"}` | `agent(Researcher)` |
| agent+超时 | `agent: {name: W, timeout: 30s}` | `{"type":"Agent","cls":"W","timeout":30}` | `agent(W, timeout=30)` |
| **顺序** | `steps: [A, B, C]` | `{"type":"FlatMap","first":A,"next":B}` | `a.flat_map(b).flat_map(c)` |
| **分发并行** (拆tuple) | `each: [A, B]` | `{"type":"Zip","left":A,"right":B}` | `a.zip(b)` |
| **广播并行** (同一input) | `all: [A, B]` | `{"type":"Broadcast","flows":[A,B]}` | `broadcast(a, b)` |
| **竞争** (取最快) | `race: [A, B, C]` | `{"type":"Race","flows":[A,B,C]}` | `race(a, b, c)` |
| **法定人数** | `quorum: {n: 2, flows: [A,B,C]}` | `{"type":"AtLeast","n":2,"flows":[A,B,C]}` | `at_least(2, a, b, c)` |
| **类型路由** | `route: {source: X, mapping: {...}}` | `{"type":"Branch","source":X,"mapping":{...}}` | `x.branch({T1: a, T2: b})` |
| **兜底** | `fallback: {primary: A, backup: B}` | `{"type":"FallbackTo","source":A,"fallback":B}` | `a.fallback_to(b)` |
| **异常恢复** | `recover: {source: A, handler: B}` | `{"type":"RecoverWith","source":A,"handler":B}` | `a.recover_with(b)` |
| **循环** | `loop: {body: A, max_iter: 10}` | `{"type":"Loop","body":A,"max_iter":10}` | `loop(a, max_iter=10)` |
| **旁路通知** (fire-and-forget) | `agent: {name: W, notify: L}` | `{"type":"Notify","source":W,"side":L}` | `w.notify(l)` |
| **同步副作用** | `agent: {name: W, tap: T}` | `{"type":"Tap","source":W,"side":T}` | `w.tap(t)` |
| **守卫** (bool判定) | `agent: {name: W, guard: C}` | `{"type":"Guard","source":W,"check":C}` | `w.guard(c)` |
| **子flow引用** | `flow: PipelineName` | 展开为 ADT | 变量引用 |

## 仅 Python（含 callable，不可序列化）

| 操作 | Python | 可序列化替代 |
|------|--------|------------|
| 纯变换 | `.map(fn)` | 无 |
| 条件过滤 | `.filter(pred)` | `.guard(agent(Checker))` |
| 条件旁路 | `.divert_to(side, when=pred)` | `.notify(side)` |
| 同步回调 | `.and_then(callback)` | `.tap(agent(Logger))` |
| 条件分支 | `.branch_on(pred, a, b)` | `.branch({Type: flow})` |
| 异常处理 | `.recover(fn)` | `.recover_with(agent(Handler))` |

## 数据传递

| 组合子 | 输入行为 | 输出行为 |
|--------|---------|---------|
| steps (flat_map) | 上一步 output → 下一步 input | 最后一步的 output |
| each (zip) | **拆 tuple**：`a_in, b_in = input` | `(a_out, b_out)` tuple |
| all (broadcast) | **同一个 input** 给所有 | 全部结果 tuple |
| race | **同一个 input** 给所有 | 最快的 output |
| quorum (at_least) | **同一个 input** 给所有 | `QuorumResult(succeeded, failed)` |
| route (branch) | source output 按类型路由 | 匹配分支的 output |
| fallback | primary 和 backup 拿**同一个 input** | 成功方的 output |
| recover | handler 拿 **Exception** 作为 input | handler 的 output |
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
