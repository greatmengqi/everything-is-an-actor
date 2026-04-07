# MOA on Flow — 设计规格

## 目标

用 Flow ADT 组合子重新实现 MOA (Mixture-of-Agents) 语义，替代现有的元编程方式（动态生成 AgentActor 子类）。

## 架构变更

### 依赖方向

```
变更前:  moa/ → agents/ → core/        (moa 和 flow 平级，互不依赖)
变更后:  moa/ → flow/ → agents/ → core/ (moa 基于 flow 实现)
```

### 设计原则

- Flow 层保持纯粹，不引入 MOA 专属概念
- MOA 层作为 Flow 之上的 pattern library
- 三层渐进式 API：入门 → 中级 → 高级

## flow/ 层变更

### 新增：`at_least` combinator

通用 quorum 并行模式——"N 路并行，至少 K 个成功"。

```python
# flow/quorum.py

@dataclass(frozen=True)
class QuorumResult(Generic[O]):
    """Quorum 执行结果，分离成功和失败。"""
    succeeded: list[O]
    failed: list[Exception]

def at_least(n: int, *flows: Flow[I, O]) -> Flow[I, QuorumResult[O]]:
    """N 路并行执行，至少 n 个成功才算通过。

    - 所有 flows 并行执行，等待全部完成
    - 成功结果收入 succeeded，异常收入 failed
    - 如果 succeeded 数量 < n，raise RuntimeError
    - 返回 QuorumResult 包含全部结果
    """
```

**实现方式**：基于现有算子组合。

```
zip_all(
    flow1.map(wrap_ok).recover(wrap_err),
    flow2.map(wrap_ok).recover(wrap_err),
    ...
).map(split_and_validate(n))
```

**ADT 节点**：不需要新的 ADT variant。`at_least` 是 combinator 函数，返回由 `_ZipAll` + `_Map` + `_Recover` 组合而成的 Flow 树。

**语义**：
- 必须等待所有 flows 完成，不提前取消（aggregator 需要看到全部结果）
- 域异常恢复到 `failed` 列表；系统异常（`MemoryError`, `SystemExit`）传播

### 变更：Interpreter 内部化

`Interpreter` 从公共 API 降为 `AgentSystem` 的内部实现。

`AgentSystem` 新增方法：

```python
async def run_flow(self, flow: Flow[I, O], input: I) -> O:
    """执行 Flow 程序，返回最终结果。"""

async def run_flow_stream(self, flow: Flow[I, O], input: I) -> AsyncIterator[TaskEvent]:
    """执行 Flow 程序，流式返回事件。"""
```

内部委托给 `Interpreter`，对外不暴露。

## moa/ 层重写

### 删除

| 文件 | 删除内容 |
|------|---------|
| `moa/config.py` | `MoANode`, `MoATree`, `ProposerSpec` |
| `moa/builder.py` | `MoABuilder`, `ResolvedNode`, `LayerOutput`（移至新位置） |

### 新增

#### `moa/layer_output.py` — directive 载体

```python
@dataclass(frozen=True)
class LayerOutput(Generic[O]):
    """Aggregator 输出，携带 directive 给下一层。

    普通返回值：只传数据，directive 清空。
    LayerOutput 返回值：result 作为数据，directive 注入下一层 proposer 输入。
    """
    result: O
    directive: str | None = None
```

`LayerOutput` 是 MOA 专属概念，不属于 Flow 层。Flow 层通过闭包间接使用，无需 import。

#### `moa/patterns.py` — MOA pattern 函数

```python
def moa_layer(
    proposers: list[type[AgentActor]],
    aggregator: type[AgentActor],
    min_success: int = 1,
    timeout: float = 30.0,
) -> Flow:
    """单层 MOA：并行 proposers + quorum 验证 + aggregator 聚合。

    内部处理：
    1. 如果有 directive，注入到 proposer 输入：{"input": ..., "directive": ...}
    2. 用 at_least(min_success, ...) 并行执行 proposers
    3. 将 QuorumResult 传给 aggregator
    4. 从 aggregator 输出提取 directive（如果返回 LayerOutput）
    5. 输出 (result, directive) 元组供下一层使用

    输入: (input, directive) 元组（由 moa_tree 保证首层包装为 (input, None)）
    输出: (result, directive) 元组
    """

def moa_tree(layers: list[Flow]) -> Flow:
    """多层 MOA：flat_map 链式组合，directive 跨层传递。

    接收 moa_layer() 返回的 Flow 列表，用 flat_map 串联。
    首层包装输入为 (input, None)，末层解包为纯 result。
    """
```

#### `moa/system.py` — 高级入口

```python
class MoASystem:
    """面向入门用户的一站式 MOA 入口。

    封装 ActorSystem → AgentSystem 初始化链。
    """

    def __init__(self) -> None:
        self._actor_system = ActorSystem()
        self._agent_system = AgentSystem(self._actor_system)

    async def run(self, flow: Flow, input: Any) -> Any:
        """执行 MOA pipeline，返回最终结果。"""
        return await self._agent_system.run_flow(flow, input)

    async def run_stream(self, flow: Flow, input: Any) -> AsyncIterator[TaskEvent]:
        """执行 MOA pipeline，流式返回事件。"""
        async for event in self._agent_system.run_flow_stream(flow, input):
            yield event

    async def shutdown(self) -> None:
        """关闭底层 actor system。"""
        await self._agent_system.shutdown()
```

### 公共 API

```python
# moa/__init__.py
from .layer_output import LayerOutput
from .patterns import moa_layer, moa_tree
from .system import MoASystem
```

## 用户 API

### 三层渐进入口

| 层级 | 入口 | 需要理解 | 适用场景 |
|------|------|---------|---------|
| 入门 | `MoASystem.run(moa_tree([...]), input)` | 填参数 | 标准 MOA 模式 |
| 中级 | `AgentSystem.run_flow(flow, input)` | Flow 组合 | 自定义编排 |
| 高级 | `AgentSystem` + `ActorSystem` | Actor 模型 | 完全控制 |

### 入门用户示例

```python
from everything_is_an_actor.moa import MoASystem, moa_layer, moa_tree, LayerOutput

# 定义 agents
class Researcher(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"Research on: {input}"

class Synthesizer(AgentActor[QuorumResult[str], LayerOutput[str]]):
    async def execute(self, results: QuorumResult[str]) -> LayerOutput[str]:
        return LayerOutput(
            result="\n".join(results.succeeded),
            directive="Focus on practical applications",
        )

class Refiner(AgentActor[QuorumResult[str], str]):
    async def execute(self, results: QuorumResult[str]) -> str:
        return f"Final: {results.succeeded[0]}"

# 组合并运行
system = MoASystem()
result = await system.run(
    moa_tree([
        moa_layer(
            proposers=[Researcher1, Researcher2, Researcher3],
            aggregator=Synthesizer,
            min_success=2,
        ),
        moa_layer(
            proposers=[Critic],
            aggregator=Refiner,
        ),
    ]),
    input="What is quantum computing?",
)
await system.shutdown()
```

### 中级用户示例

```python
from everything_is_an_actor.flow import agent, at_least

# 直接用 Flow 算子组合，绕过 moa 便捷函数
pipeline = (
    at_least(2, agent(R1), agent(R2), agent(R3))
    .flat_map(agent(Synthesizer))
    .flat_map(
        at_least(1, agent(Critic))
        .flat_map(agent(Refiner))
    )
)

system = AgentSystem(ActorSystem())
result = await system.run_flow(pipeline, "input")
```

## 不变的部分

- `flow/` 核心 ADT（Flow、所有现有 combinator）不动
- `agents/`（AgentActor、Task、TaskResult、TaskEvent）不动
- `core/`（Actor、ActorRef、ActorSystem）不动

## 实现清单

- [ ] `flow/quorum.py` — `QuorumResult` 类型 + `at_least` combinator
- [ ] `flow/combinators.py` — 导出 `at_least`
- [ ] `flow/interpreter.py` — 无新 ADT 节点，无需修改
- [ ] `agents/system.py` — 新增 `run_flow` / `run_flow_stream`，内部使用 Interpreter
- [ ] `moa/layer_output.py` — `LayerOutput` 迁移
- [ ] `moa/patterns.py` — `moa_layer` / `moa_tree` 实现
- [ ] `moa/system.py` — `MoASystem` 实现
- [ ] `moa/__init__.py` — 公共 API 导出
- [ ] 删除 `moa/config.py`、`moa/builder.py`
- [ ] 更新受影响的测试
- [ ] 更新 `docs/moa-on-flow.md` 同步新设计
