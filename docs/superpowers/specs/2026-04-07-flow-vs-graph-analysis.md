# Flow DSL vs Graph — 表达能力与语义边界分析

## 问题

在 agent 编排里，常见有两种主表示：

- **图模型**：节点 + 边，强调拓扑关系、依赖关系、可视化编辑。
- **Flow 模型**：顺序、并行、分支、恢复、循环等组合子构成的声明式程序，强调执行语义。

这里要回答的不是“图能不能运行”，而是更具体的问题：

1. Flow 是否比图更适合作为 agent orchestration 的**规范表示**（canonical representation）？
2. Flow 是否能被 `JSON` / `YAML` **精确表达**？
3. 图在这个体系里应该是**源语言**，还是 **投影视图**？

## 结论

结论分三层：

### 0. 立场版（一句话）

如果只能押一个方向，我会押：

> **用 Flow 做 agent 编排的语义主表示，用 YAML/JSON 做规范承载，用 Graph 做投影视图。**

换句话说：

- 让 `Flow` 定义“这件事到底是什么意思”
- 让 `YAML` / `JSON` 定义“这件事怎么被保存和传输”
- 让 `Graph` 定义“这件事怎么被展示、编辑和观测”

这比“直接把图当唯一真相”更可验证，也比“只有 DSL 没有图”更容易工程落地。

### 1. `JSON` / `YAML` 可以精确表达 Flow

前提是 Flow 不是“松散配置”，而是**有固定 variant 和组合语义的 ADT**。

一旦 Flow 被定义为：

- `Agent`
- `FlatMap`
- `Zip` / `Broadcast` / `Race` / `AtLeast`
- `Branch`
- `RecoverWith` / `FallbackTo`
- `Loop`
- `Notify` / `Tap` / `Guard`

那么 `JSON` / `YAML` 只是序列化载体，完全可以无损表达这棵语法树。

### 2. Flow 比图更适合作为“语义主表示”

原因不是它“更高级”，而是它更容易把以下信息变成**显式语义**：

- 输入输出类型
- 组合方式
- 数据传递规则
- 失败传播规则
- 恢复与补偿规则
- 循环终止条件
- 副作用与主路径的边界

这些信息在图里也可以存在，但往往会退化为：

- 节点属性里的隐式约定
- 边上的额外 metadata
- 引擎内部 hardcode 的特殊分支

结果就是：图表达了“连线”，但没有直接表达“组合律”。

### 3. 图更适合作为执行视图和可视化视图

图并没有过时。图非常适合：

- 编辑器
- 监控面板
- Mermaid / DAG 可视化
- 执行计划展开
- Trace 回放

因此更合理的分层是：

```text
Flow DSL / ADT      = 语义层（source of truth）
JSON / YAML         = 持久化与传输层
Graph / Mermaid DAG = 投影视图 / 执行视图
Interpreter         = 运行时语义承载
```

## 原创性边界

这里需要把“原创”说准确，否则很容易发生概念层面的误解。

### 不是在宣称“发明了 flow 这个概念”

广义的 `flow`、`workflow`、`dataflow`、`DAG orchestration`、`reactive pipeline` 都早已有长期历史。

因此不应声称：

- 首次发明了 flow
- 首次发明了工作流编排
- 首次发明了图之外的流程表示

这些说法都会把“通用概念”与“本仓库中的特定模型”混为一谈。

### 原创的对象是什么

更准确的说法是：**这里原创的是一套特定定义下的 agent-oriented Flow 模型**。

它至少包含以下组合特征：

- `Flow[I, O]` 作为类型化 ADT，而不是仅仅一组运行时节点
- `YAML / JSON / Python` 三种等价表示，围绕同一语义核心无损互转
- graph 作为派生视图，而不是语义本体
- actor-native interpreter，把组合子解释为 actor runtime 上的执行语义
- 面向 agent orchestration 的恢复、守卫、quorum、loop 等一等原语

换句话说，原创性不在 “flow” 这个单词，而在于：

> **首次系统化提出这一套面向 agent 编排的、类型化的、可序列化的、actor-native 的 Flow 语义体系。**

### 推荐表述口径

为了既保留原创性主张，又避免过度宣称，建议分三种口径使用。

#### 1. 学术 / 设计口径

> 我们提出了一种面向 agent orchestration 的原创 Flow 模型：以 `Flow[I, O]` 为语义核心，以 `YAML / JSON / Python` 为等价表示，以 graph 为派生投影视图。

这个口径强调“提出了一种特定模型”，而不是宣称发明了整个 flow 范畴。

#### 2. 对外介绍口径

> This repository introduces an original Flow model for agent orchestration, rather than claiming to invent the general notion of “flow”.

这个口径适合 README、官网摘要、对外分享。

#### 3. 内部强表述口径

> 我们不是首创 flow 这个词，但我们首创了这套特定定义下的 Flow 编排模型。

这个口径适合团队内部统一叙述，能同时保留自信和边界感。

### 判断原创性的实质标准

如果未来需要更正式地主张原创性，可以围绕下面几个实质点，而不是围绕词汇本身：

- 是否定义了新的语义核心，而不是仅仅重命名现有 DAG
- 是否给出了成体系的组合子代数与数据传递规则
- 是否把 `YAML / JSON / Python / graph / runtime` 统一到同一个语义中心
- 是否明确把 graph 降级为投影，而把 Flow ADT 提升为 source of truth
- 是否形成了可以跨语言实现的协议层，而不是单一语言内部 API

如果这些点成立，那么“原创 Flow 模型”就是对模型层创新的描述，而不是营销性夸张。

## 为什么 Flow 能被 `JSON` / `YAML` 精确表达

“能精确表达”不是指能写出某种配置文件，而是指下面四件事都成立。

### 1. 结构可枚举

Flow 的组合子集合是有限的、封闭的。

例如：

- 顺序 = `steps`
- 广播并行 = `all`
- 分发并行 = `each`
- 竞争 = `race`
- 法定人数 = `at_least`
- 类型路由 = `branch`
- 异常恢复 = `recover_with`
- 兜底 = `fallback_to`
- 循环 = `loop`

这意味着 YAML/JSON 解析后可以落到确定的 ADT variant，而不是运行时靠字符串猜测语义。

### 2. 嵌套结构天然匹配组合语义

Flow 不是平面表，而是树。

`YAML` / `JSON` 对树形结构的表达天然充分：

- 顺序组合用数组
- 命名参数用对象
- 子 flow 用嵌套对象
- 多分支映射用字典

例如：

```yaml
steps:
  - agent: Search
  - all:
      - agent: Analyst
      - agent: Summarizer
  - fallback_to:
      source: {agent: Writer}
      fallback: {agent: BackupWriter}
```

这段 YAML 表达的不是“画了三段线”，而是**先顺序、再广播并行、再失败兜底**。

### 3. 语义边界可以静态校验

如果 Flow 是 ADT，就可以在上传 YAML 后做纯静态检查：

- `flat_map(A, B)`：`A.O == B.I`
- `race(A, B)`：输入类型一致，输出类型一致
- `branch(source, mapping)`：`mapping` 覆盖 source 输出的可分派类型
- `guard(A, check)`：`check` 的输出必须是 `bool`

图模型当然也能加校验，但图通常先有节点和边，再事后补规则；Flow 则是规则先于可视化存在。

### 4. 可逆映射更清晰

理想情况应该是：

```text
YAML ↔ Flow ADT ↔ JSON
```

这要求三者都承载同一套语义，而不是：

- YAML 丢一部分信息
- 图补一部分信息
- 运行时再偷偷推断一部分信息

一旦信息分散在三处，格式虽然“能表示”，但就不是精确表达，而是拼凑表达。

## 图模型的强项与边界

### 强项

图模型最强的点不是语义，而是直观性。

- 人一眼能看懂拓扑
- 方便做拖拽式编排
- 适合展示依赖关系
- 适合执行状态染色
- 适合 trace 和 metrics 叠加

如果目标是：

- 低门槛工作流平台
- 面向业务方的可视化编排
- DAG 任务调度

图通常是第一选择。

### 边界

图模型的问题在于：它对“线”很强，对“律”很弱。

例如在图里，下面几个问题经常没有统一的一等表示：

- 这是把同一个输入广播给多个分支，还是把 tuple 拆给不同分支？
- 失败后是重试当前节点，还是回退到备用 flow？
- side effect 是 fire-and-forget，还是同步 tap？
- 循环的反馈值走哪条边，终止值走哪条边？
- branch 是按 predicate、按 tag、按 type，还是按字符串路由？

图当然可以表示这些，但通常需要：

- 特殊节点
- 特殊边类型
- 特殊属性字段
- 特殊执行器约定

这会让“简单可视化”逐步演化成“隐式语义容器”。

## 为什么 agent 编排更需要 Flow 语义层

agent 编排相比传统 DAG，有几个额外约束：

### 1. 输入输出不是简单文件依赖，而是类型化消息流

传统 DAG 更关注任务是否完成。agent 编排更关注：

- 上游返回的到底是什么结构
- 下游是否能消费它
- 是否需要路由到不同 agent
- 是否要把同一输入发给多个 agent 做 quorum / ensemble

因此 agent orchestration 更像**可组合程序**，不只是任务依赖图。

### 2. 失败不是边缘情况，而是主要语义

LLM 超时、工具调用失败、结构化输出失败、下游拒绝、质量门禁失败，这些都不是异常边角，而是主路径的一部分。

所以需要把这些语义直接放进模型里：

- `recover_with`
- `fallback_to`
- `guard`
- `at_least`

这类原语用 Flow 表达更自然。

### 3. 局部替换与组合性非常重要

agent 系统经常需要：

- 替换某个 agent 为另一个实现
- 把一小段 flow 封装成子 flow
- 把 YAML 定义的骨架和 Python 定义的局部逻辑拼接

如果主表示是 Flow ADT，这些都是程序级组合；如果主表示是图，通常会退化成编辑节点和边的结构操作。

## 推荐的分层模型

推荐把系统分成四层，而不是让图或 YAML 独自承担全部职责。

### 1. Flow ADT：唯一语义源

这一层定义：

- 有哪些组合子
- 每个组合子的输入输出是什么
- 数据如何传递
- 错误如何传播
- 循环如何终止

这是规范层。

### 2. YAML / JSON：声明式外部表示

这一层承担：

- 人工编写
- 版本化存储
- 跨进程传输
- 跨语言协议

这一层不应该发明新语义，只负责承载 Flow ADT。

### 3. Graph：投影视图

这一层承担：

- Mermaid 导出
- Studio 编辑器
- 运行态展示
- Trace 展开

图应该从 Flow 派生，而不是反过来成为唯一真相。

### 4. Interpreter：执行语义

这一层负责把 Flow ADT 解释为：

- actor topology
- ask / race / zip / supervision
- A2A / gRPC / MCP / 本地 agent 调用

这使得同一份 DSL 可以落到不同 runtime，而不改变上层表示。

## 对仓库当前方向的判断

当前设计方向是正确的：

- `Flow[I, O]` 作为 ADT
- YAML（人写）/ JSON（传输）/ Python（组合）三者等价
- Mermaid graph 作为可视化产物
- 类型校验在执行前完成

这条路线的关键收益不是“比图更炫”，而是：

- **语义集中**：组合语义只定义一次
- **静态检查**：在运行前发现类型链问题
- **跨语言**：规范先于实现，runtime 可以替换
- **可视化降级**：图是衍生物，不绑架核心语义

## 设计判断标准

如果一个 Flow DSL 想真正优于图模型，至少要满足以下标准：

### 1. 组合子集合小而完整

不能把一切都塞成任意脚本回调，否则 YAML 只是配置外壳，真正语义仍藏在代码里。

### 2. 数据传递规则显式

必须清楚区分：

- 顺序传递
- tuple 拆分
- 同输入广播
- quorum 聚合
- side-channel 穿透

### 3. 错误语义是一等公民

不能只靠全局 try/catch。恢复、兜底、守卫、取消策略都应该可组合。

### 4. 图是派生结果，不是语义补丁区

如果图上必须额外标很多运行时特殊属性，才能补全 YAML 没有的信息，说明语义分层失败了。

## 最小 Flow YAML 语义核心

如果要让 Flow DSL 真正成立，YAML 语义核心应该尽量小，但必须闭合到足以覆盖 agent 编排的主场景。

### 最小原语集合

建议至少保留下面八类：

| 类别 | 原语 | 作用 |
|------|------|------|
| 叶子 | `agent` | 调用一个 agent |
| 顺序 | `steps` | 上一步输出进入下一步 |
| 并行 | `all` | 同一输入广播给多个 flow |
| 并行 | `each` | tuple / 多输入分发给多个 flow |
| 分支 | `branch` | 按类型或标签路由 |
| 恢复 | `fallback_to` / `recover_with` | 失败后的备用路径 |
| 约束 | `guard` | 质量门禁 / 契约校验 |
| 迭代 | `loop` | 直到 `Done` 为止 |

在这组最小原语之上，可以把一些能力看作派生能力：

- `race`：竞争并行
- `at_least`：quorum 并行
- `notify`：异步旁路通知
- `tap`：同步 side effect

也就是说，DSL 不必一开始就追求“全能”，但必须先把最关键的语义边界封住。

### 最小数据规则

仅有原语还不够，还必须把数据传递规则写死。

| 原语 | 输入规则 | 输出规则 |
|------|----------|----------|
| `agent` | 消费一个输入 `I` | 产出一个输出 `O` |
| `steps` | 第 `n` 步的输出喂给第 `n+1` 步 | 最后一项输出为整体输出 |
| `all` | 同一个输入广播给所有分支 | 聚合成 tuple / list |
| `each` | 从复合输入中拆分到各分支 | 聚合成 tuple / list |
| `branch` | 消费上游输出，按规则选一个分支 | 返回被选分支的输出 |
| `fallback_to` | 失败时用原始输入重跑备用 flow | 返回成功方输出 |
| `recover_with` | 失败时把异常值交给恢复 flow | 返回恢复 flow 输出 |
| `guard` | 校验上游输出，但默认值继续穿透 | 成功则透传，失败则异常 |
| `loop` | `Continue` 反馈到下一轮，`Done` 作为终值 | 返回 `Done` 内部值 |

这张表很重要，因为它把“画线关系”提升为“值如何流动”的明确定义。

### 最小 YAML 形态

一个足够小、但已经有表达力的 YAML 骨架，可以长这样：

```yaml
flow: AnswerPipeline
steps:
  - agent: Retriever
  - branch:
      source: {agent: Classifier}
      mapping:
        SimpleQuestion:
          agent: FastResponder
        ComplexQuestion:
          steps:
            - all:
                - agent: Researcher
                - agent: Analyst
            - agent: Writer
  - guard:
      source: {agent: QualityChecker}
      check: {agent: Acceptable}
  - fallback_to:
      source: {agent: Publisher}
      fallback: {agent: SafePublisher}
```

这个骨架已经覆盖：

- 顺序
- 分支
- 广播并行
- 守卫
- 失败兜底

也就是说，不需要先做成“大而全图平台”，就已经可以支撑一套严肃的 agent orchestration 语义。

### 最小静态校验集合

如果只做第一版校验器，建议先锁定以下规则：

1. `steps` 相邻节点类型必须首尾相接
2. `all` 的各分支输入类型必须一致
3. `each` 的输入必须能拆分到所有子分支
4. `branch` 的 `mapping` 必须非空，且 key 可匹配上游输出
5. `fallback_to` 的主分支和备用分支输入输出必须一致
6. `recover_with` 的恢复分支输入必须是异常类型
7. `guard.check` 的输出必须是 `bool`
8. `loop.body` 的输出必须是 `Continue[T] | Done[U]`

只要这八类校验存在，这套 YAML DSL 就已经不是“配置文件”，而是一个有静态边界的编排语言。

## 最终判断

如果问题是：

> Flow 模型是不是可以用 JSON 和 YAML 精确表达？

答案是：**可以，而且这正是它适合作为 agent orchestration canonical IR 的原因之一。**

如果问题是：

> 它是不是因此就比图模型更适合 agent 编排？

更准确的答案是：

- **作为语义主表示：是。**
- **作为可视化、编辑、执行展开视图：不是，图仍然不可替代。**

所以最稳健的架构不是 “Flow 或 Graph 二选一”，而是：

```text
Flow 定义语义，YAML/JSON 承载规范，Graph 负责投影，Interpreter 负责执行。
```

这比“让图兼任全部角色”更适合 agent 编排，也比“只有 DSL 没有图”更利于工程落地。
