# Flow YAML DSL

YAML 表示的 Flow ADT。每个 YAML 关键词精确对应一个 ADT variant，解析成 Flow 语法树后由 Interpreter 执行。

## 设计原则

- YAML 关键词 1:1 对应 Flow ADT variant，不发明新语义
- 可序列化的 variant（serialize.py 的 `to_dict` 支持的）才进 YAML
- 含 callable 的 variant（Pure, Map, Filter, AndThen, DivertTo, BranchOn, Recover）不进 YAML——留在 Python 代码里
- 数据传递规则和代码完全一致，不加 as/input/$input 等 YAML 特有的路由语法

## ADT variant → YAML 映射

### 可 YAML 化（不含 callable）

| ADT variant | YAML 语法 | 输入行为 | 输出行为 |
|-------------|-----------|---------|---------|
| `_Agent(cls, timeout)` | `agent: Name` | 接收上一步 output | agent 的 execute 返回值 |
| `_FlatMap(first, next)` | `steps:` 列表 | 链式：O₁ = I₂ | 最后一步的 output |
| `_Zip(left, right)` | `each:` | 拆 tuple：`left_in, right_in = input` | `(left_out, right_out)` tuple |
| `_ZipAll(flows)` | `each:` (N 项) | 拆 tuple/list | 结果 list |
| `_Race(flows)` | `fastest of:` | **同一个 input** 给所有 flow | 最快的 output |
| `at_least(n, flows)` | `best N of:` | **同一个 input** 给所有 flow（内部 `pure(dup) + zip_all`） | `QuorumResult` |
| `_Branch(source, mapping)` | `route:` | source output 按类型路由 | 匹配分支的 output |
| `_FallbackTo(source, fallback)` | `if fails:` | source 和 fallback 拿**同一个 input** | 成功方的 output |
| `_RecoverWith(source, handler)` | `recover:` | handler 拿 Exception 作为 input | handler 的 output |
| `_Loop(body, max_iter)` | `repeat:` + `max:` | 循环体返回 `Continue(val)` → val 作为下轮 input | `Done(val)` 的 val |

### 不可 YAML 化（含 callable，留在 Python）

| ADT variant | 原因 | Python 中使用 |
|-------------|------|-------------|
| `_Pure(f)` | f 是 callable | `pure(lambda x: ...)` |
| `_Map(source, f)` | f 是 callable | `.map(transform)` |
| `_Filter(source, predicate)` | predicate 是 callable | `.filter(check)` |
| `_AndThen(source, callback)` | callback 是 callable | `.and_then(log)` |
| `_DivertTo(source, side, when)` | when 是 callable | `.divert_to(side, pred)` |
| `_BranchOn(source, pred, then, else)` | predicate 是 callable | `.branch_on(pred, a, b)` |
| `_Recover(source, handler)` | handler 是 callable | `.recover(lambda e: ...)` |

## 数据传递规则

**和代码完全一致，无 YAML 特有规则。**

### flat_map（steps 列表）

```
步骤 A: Flow[I, M]
步骤 B: Flow[M, O]

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

interpreter:
  left_input, right_input = input
```

```yaml
steps:
  - agent: Splitter       # I → (Part1, Part2)
  - each:
      - agent: ProcessA   # Part1 → ResultA
      - agent: ProcessB   # Part2 → ResultB
  - agent: Merger          # (ResultA, ResultB) → O
```

### race（fastest of）

```
所有 flow 拿同一个 input

interpreter:
  self._interpret(f, input) for f in flows  # 同一个 input
```

```yaml
steps:
  - agent: Preparator     # I → SearchQuery
  - fastest of:
      - agent: Google     # SearchQuery → Result
      - agent: Bing       # SearchQuery → Result
      - agent: DuckDuckGo # SearchQuery → Result
  - agent: Analyzer        # Result → O
```

### at_least（best N of）

```
内部实现：pure(lambda x: (x,) * k).flat_map(zip_all(*wrapped))
即：复制 input k 份 → 每个 flow 拿一份 → 收集结果 → 验证 quorum

所有 flow 拿同一个 input
```

```yaml
steps:
  - best 2 of:
      - agent: AnalystA   # input → Result
      - agent: AnalystB   # input → Result
      - agent: AnalystC   # input → Result
  - agent: Summarizer      # QuorumResult → O
```

### branch（route）

```
source output 按 isinstance 匹配分支

interpreter:
  for typ, branch_flow in mapping.items():
      if isinstance(value, typ):
          return self._interpret(branch_flow, value)
```

```yaml
steps:
  - agent: Classifier     # I → TechQuery | CreativeQuery | GeneralQuery
  - route:
      TechQuery: TechWriter        # TechQuery → O
      CreativeQuery: CreativeWriter # CreativeQuery → O
      GeneralQuery: GeneralWriter   # GeneralQuery → O
```

### fallback（if fails）

```
source 失败时，fallback 拿原始 input（不是 exception）

interpreter:
  try: self._interpret(source, input)
  except: self._interpret(fallback, input)  # 同一个 input
```

```yaml
- agent: Writer
  if fails: BackupWriter    # 两个都拿同一个 input
```

### loop（repeat）

```
body 返回 Continue(val) → val 作为下轮 input
body 返回 Done(val) → val 作为最终 output
超过 max_iter 抛 RuntimeError
```

```yaml
- repeat:
    agent: Refiner         # I → Continue(I) | Done(O)
  max: 10                  # max_iter 安全边界
```

## 完整 YAML 语法

```yaml
flow: PipelineName
steps:
  # 单 agent
  - agent: AgentName
  - agent: AgentName
    max: 30s                    # timeout

  # 顺序（嵌套 steps = 子 flat_map 链）
  - steps:
      - agent: A
      - agent: B

  # 分发并行（zip —— 拆 input tuple）
  - each:
      - agent: A               # input[0]
      - agent: B               # input[1]

  # 广播并行（at_least n=全部 —— 同一个 input）
  - best all of:
      - agent: A
      - agent: B

  # 竞争（race —— 同一个 input，取最快）
  - fastest of:
      - agent: A
      - agent: B

  # 法定人数（at_least —— 同一个 input，至少 N 个成功）
  - best 2 of:
      - agent: A
      - agent: B
      - agent: C

  # 分支（branch —— 按类型路由）
  - route:
      TypeA: AgentForA
      TypeB: AgentForB
      default: AgentDefault

  # 兜底（fallback_to）
  - agent: Primary
    if fails: Backup

  # 异常恢复（recover_with —— handler 拿 Exception）
  - agent: Risky
    recover: ErrorHandler

  # 循环（loop）
  - repeat:
      agent: Refiner
    max: 10

  # 子 flow 引用
  - flow: OtherPipeline

  # 组合修饰（可叠加）
  - agent: Writer
    if fails: BackupWriter
    max: 60s
```

## 解析规则

YAML → Flow ADT 的转换伪代码：

```python
def parse(node) -> Flow:
    match node:
        case {"agent": name, **opts}:
            flow = _Agent(cls=registry[name], timeout=parse_timeout(opts))
            if "if fails" in opts:
                flow = _FallbackTo(source=flow, fallback=parse({"agent": opts["if fails"]}))
            if "recover" in opts:
                flow = _RecoverWith(source=flow, handler=parse({"agent": opts["recover"]}))
            return flow

        case {"steps": items}:
            flows = [parse(item) for item in items]
            return reduce(lambda a, b: _FlatMap(first=a, next=b), flows)

        case {"each": items}:
            flows = [parse(item) for item in items]
            return _ZipAll(flows=tuple(flows)) if len(flows) > 2 else _Zip(...)

        case {"fastest of": items}:
            return _Race(flows=tuple(parse(item) for item in items))

        case {"best N of": items} | {"best all of": items}:
            return at_least(n, *[parse(item) for item in items])

        case {"route": mapping}:
            # 需要 registry 解析类型名
            ...

        case {"repeat": body, "max": n}:
            return _Loop(body=parse(body), max_iter=n)

        case {"flow": name}:
            return flow_registry[name]
```

## 不做的

- `as:` / `input:` / `$input` — Flow ADT 没有命名/引用语义，数据传递靠类型链
- `transform:` — 对应 `_Pure` / `_Map`，含 callable，不可序列化，留在 Python
- `notify:` — 对应 `_DivertTo`，含 callable（when predicate），留在 Python
- `tap:` — 对应 `_AndThen`，含 callable，留在 Python
- `filter:` — 对应 `_Filter`，含 callable，留在 Python

**含 callable 的组合子只能在 Python 代码里用，YAML 是纯结构描述。**

## 混合使用

YAML 定义结构骨架，Python 补充含 callable 的部分：

```python
# YAML 定义结构
pipeline = Flow.from_yaml("research_pipeline.yaml", registry=agent_registry)

# Python 补充 callable 部分
pipeline = (
    pipeline
    .map(lambda result: result.summary)       # 纯变换
    .filter(lambda r: len(r) > 100)           # 过滤
    .divert_to(agent(Logger), when=is_error)  # 旁路
)

result = await system.run_flow(pipeline, input)
```

## 和 to_dict/from_dict 的关系

YAML 和 JSON 序列化覆盖同一组 variant：

```
YAML ←→ Flow ADT ←→ JSON (to_dict/from_dict)
```

三者可互转。YAML 面向人类编写，JSON 面向机器传输，Flow ADT 面向代码组合。
