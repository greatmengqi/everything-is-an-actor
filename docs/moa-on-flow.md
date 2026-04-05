# MOA on Flow

## 概述

MOA (Mixture-of-Agents) 基于 Flow ADT 实现，作为 Flow 之上的 pattern library。

```
依赖方向: moa/ → flow/ → agents/ → core/
```

## 核心组件

### flow 层：`at_least` combinator

通用 quorum 并行模式——"N 路并行，至少 K 个成功"。

```python
from everything_is_an_actor.flow import at_least, agent
from everything_is_an_actor.flow.quorum import QuorumResult

flow = at_least(2, agent(Agent1), agent(Agent2), agent(Agent3))
# 返回 QuorumResult(succeeded=[...], failed=[...])
```

- 所有 flows 并行执行，等待全部完成
- 域异常恢复到 `failed` 列表；系统异常（`MemoryError`）传播
- 不引入新 ADT 节点，完全由现有算子组合

### moa 层：pattern library

| 组件 | 职责 |
|------|------|
| `LayerOutput` | Aggregator 输出的 directive 载体 |
| `moa_layer()` | 单层 MOA：proposers → quorum → aggregator |
| `moa_tree()` | 多层 flat_map 链 + directive 跨层传递 |
| `MoASystem` | 高级入口，封装 ActorSystem → AgentSystem |

## 使用示例

### 入门用户

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

### 中级用户

```python
from everything_is_an_actor.agents import AgentSystem
from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.flow import agent, at_least

# 直接用 Flow 算子组合
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
await system.shutdown()
```

## 三层渐进 API

| 层级 | 入口 | 需要理解 |
|------|------|---------|
| 入门 | `MoASystem.run(moa_tree([...]), input)` | 填参数 |
| 中级 | `AgentSystem.run_flow(flow, input)` | Flow 组合 |
| 高级 | `AgentSystem` + `ActorSystem` | Actor 模型 |

## directive 传递

`LayerOutput` 允许 aggregator 给下一层传递指令：

```python
# Layer 1 aggregator 返回 LayerOutput
return LayerOutput(result="answer", directive="focus on X")

# Layer 2 proposer 收到的 input 变为
{"input": "answer", "directive": "focus on X"}
```

- 普通返回值：directive 清空
- `LayerOutput` 返回值：directive 注入下一层 proposer 输入
- `moa_tree` 自动处理首层包装和末层解包
