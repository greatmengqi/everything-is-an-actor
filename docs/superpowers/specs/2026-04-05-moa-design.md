# MOA (Mixture of Agents) Design Spec

## Problem

LLM 应用中常见的 Mixture-of-Agents 模式（多个 proposer 并行生成 → aggregator 综合）缺乏与 actor 运行时的集成。现有开源实现（Together MoA、groq-moa）是纯过程式脚本，没有 supervision、streaming observability、类型安全或可组合性。

## Goal

在 `everything_is_an_actor` 框架的 `core/` + `agents/` 之上，封装 MOA 编排模式为可组合的 AgentActor pattern。

## Design Decisions

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 方向 | 可组合 pattern — MOA 本身是 AgentActor | 框架已有全部并发原语，MOA 是编排模式不是新基础设施 |
| 2 | Proposer 粒度 | 同构 + 异构统一，`list[ProposerSpec]` | `sequence()` 已支持两者 |
| 3 | Aggregator 输入 | `list[TaskResult[O]]` | TaskResult 已有 output/error/task_id，不引入新类型 |
| 4 | 层间指令 | 静态前馈 + 可选 `LayerOutput(result, directive)` | 论文是纯静态，directive 是可选增强 |
| 5 | 容错 | `ComposableFuture.recover()` → Validated 语义 + `min_success` | 不新增并发原语 |
| 6 | Streaming | 全透明 — 利用已有 TaskEvent + ContextVar | 零新代码 |
| 7 | 架构 | 方案 A — Thin Orchestrator，MoAAgent 内部 for 循环 | 社区验证，不暴露内部，以后可改 |
| 8 | API | 递归 ADT（MoATree + MoANode）+ MoABuilder interpreter | Config = AST，Builder = Interpreter |

## Module Structure

```
everything_is_an_actor/
  core/       ← generic actor runtime (PR #43)
  agents/     ← AI-specific (Task, AgentActor, AgentSystem)
  moa/        ← MOA pattern (new)
    __init__.py
    config.py
    builder.py
    utils.py
```

Dependency: `moa/ → agents/ → core/`。`agents/` 不知道 `moa/` 存在。

## Type System

### Config ADT (`config.py`)

```python
from __future__ import annotations

ProposerSpec = type[AgentActor] | MoATree    # sum type: leaf | subtree

@dataclass(frozen=True)
class MoANode:
    """Product type: 一层的 proposers + aggregator"""
    proposers: list[ProposerSpec]
    aggregator: type[AgentActor]
    min_success: int = 1

    def __post_init__(self):
        if self.min_success < 1:
            raise ValueError("min_success must be >= 1")
        if self.min_success > len(self.proposers):
            raise ValueError("min_success cannot exceed number of proposers")

@dataclass(frozen=True)
class MoATree:
    """Pipeline: 节点的有序序列"""
    nodes: list[MoANode]

    @classmethod
    def repeated(cls, node: MoANode, num_layers: int) -> MoATree:
        """语法糖: 同一节点重复 N 层"""
        return cls(nodes=[node] * num_layers)
```

### Resolved Types (`builder.py`)

```python
@dataclass(frozen=True)
class ResolvedNode:
    """解析后的节点: 所有子树已 build 成 AgentActor class"""
    proposers: list[type[AgentActor]]   # 全部是叶子
    aggregator: type[AgentActor]
    min_success: int

@dataclass(frozen=True)
class LayerOutput(Generic[O]):
    """Aggregator 可选输出: result + 给下一层的指令"""
    result: O
    directive: str | None = None
```

类型流: `MoATree → [MoANode] → build() → [ResolvedNode] → _MoAAgent`

## Builder / Interpreter (`builder.py`)

```python
class MoABuilder:
    def build(self, tree: MoATree) -> type[AgentActor]:
        resolved = [self._resolve_node(n) for n in tree.nodes]
        # 返回动态创建的 AgentActor 子类，绑定 resolved nodes
        ...

    def _resolve_node(self, node: MoANode) -> ResolvedNode:
        resolved_proposers = []
        for spec in node.proposers:
            match spec:
                case type() as cls:
                    resolved_proposers.append(cls)
                case MoATree() as subtree:
                    resolved_proposers.append(self.build(subtree))
        return ResolvedNode(
            proposers=resolved_proposers,
            aggregator=node.aggregator,
            min_success=node.min_success,
        )
```

## MoAAgent Runtime (`builder.py`, internal)

`_MoAAgent` 是 builder 产出的 `AgentActor` 子类，用户不直接接触。

### execute() — 核心循环

```python
async def execute(self, input: Any) -> Any:
    current = input
    directive = None

    for node in self._moa_nodes:
        # 1. 构建 proposer 任务
        proposer_tasks = []
        for cls in node.proposers:
            task_input = current if directive is None else {"input": current, "directive": directive}
            proposer_tasks.append((cls, Task(input=task_input)))

        # 2. 并行 proposers (Validated 语义)
        results = await self._run_proposers(proposer_tasks, node.min_success)

        # 3. Aggregator
        agg_output = (await self.context.ask(
            node.aggregator, Task(input=results)
        )).get_or_raise()

        # 4. 解析 directive
        match agg_output:
            case LayerOutput(result=r, directive=d):
                current, directive = r, d
            case _:
                current, directive = agg_output, None

    return current
```

### _run_proposers() — Validated 语义

```python
async def _run_proposers(self, tasks, min_success):
    futures = [
        self.context.ask(target, msg).recover(
            lambda e, tid=msg.id: TaskResult(task_id=tid, error=str(e), status=TaskStatus.FAILED)
        )
        for target, msg in tasks
    ]
    all_results = await ComposableFuture.sequence(futures)

    successes = [r for r in all_results if r.is_success()]
    if len(successes) < min_success:
        failed = [r for r in all_results if r.is_failure()]
        raise RuntimeError(
            f"MOA: {len(successes)}/{len(tasks)} succeeded, need >= {min_success}. "
            f"Failures: {[r.error for r in failed]}"
        )
    return all_results
```

关键: `recover` 把每个 proposer 的 Either 语义提升为 Validated，`ComposableFuture.sequence` 不再 fail-fast。

## Utility (`utils.py`)

```python
def format_references(
    results: list[TaskResult[str]],
    *,
    include_failures: bool = False,
) -> str:
    """Together 式 prompt 拼接 — LLM aggregator 的便利函数，非框架核心"""
    lines = []
    for i, r in enumerate(results, 1):
        match r:
            case TaskResult(output=o) if r.is_success():
                lines.append(f"{i}. {o}")
            case TaskResult(error=e) if include_failures:
                lines.append(f"{i}. [FAILED: {e}]")
    return "\n".join(lines)
```

## Public API

```python
# everything_is_an_actor/moa/__init__.py
__all__ = [
    "MoATree",           # 树 (pipeline 配置)
    "MoANode",           # 节点 (单层配置)
    "MoABuilder",        # interpreter
    "LayerOutput",       # aggregator 可选输出
    "ResolvedNode",      # 解析后的节点
    "format_references", # prompt 拼接工具
]
```

不暴露: `ProposerSpec` (type alias), `_MoAAgent` (builder 内部)

## Usage Example

```python
from everything_is_an_actor.moa import MoATree, MoANode, MoABuilder
from everything_is_an_actor.agents import AgentSystem

# 1. 声明
tree = MoATree(nodes=[
    MoANode(proposers=[AgentA, AgentB, AgentC], aggregator=Agg),
])

# 2. 构建
MoAAgent = MoABuilder().build(tree)

# 3. 运行 (和普通 AgentActor 一样)
system = AgentSystem("moa-demo")
async for event in system.run(MoAAgent, "What is actor model?"):
    print(event)
await system.shutdown()
```

## Testing Strategy

- **config tests**: MoANode/MoATree 构造、校验、resolve、递归嵌套
- **builder tests**: build 返回 AgentActor 子类、端到端 propose→aggregate、多层串联、partial success (Validated)、min_success 不满足 raise、嵌套递归、LayerOutput directive 传递
- **utils tests**: format_references 格式化、include_failures 参数
- **集成测试**: 用 mock AgentActor (不依赖 LLM) 验证完整 pipeline

## Dependencies

- 前置: PR #43 (core/ 目录重构) 合并
- 不修改 core/ 或 agents/ 的任何代码
- 不引入外部依赖
