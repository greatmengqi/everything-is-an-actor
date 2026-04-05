"""Flow -> Mermaid diagram generation."""

from __future__ import annotations

from everything_is_an_actor.flow.flow import (
    Flow,
    _Agent,
    _AndThen,
    _Branch,
    _BranchOn,
    _DivertTo,
    _FallbackTo,
    _Filter,
    _FlatMap,
    _Loop,
    _LoopWithState,
    _Map,
    _Pure,
    _Race,
    _Recover,
    _RecoverWith,
    _Zip,
)


def to_mermaid(flow: Flow) -> str:
    """Generate a Mermaid graph LR diagram from a Flow ADT."""
    ctx = _MermaidCtx()
    _visit(flow, ctx)
    return "graph LR\n" + "\n".join(ctx.lines)


class _MermaidCtx:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._counter = 0

    def new_id(self, prefix: str = "n") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def node(self, nid: str, label: str) -> None:
        self.lines.append(f"    {nid}[{label}]")

    def edge(self, src: str, dst: str, label: str = "") -> None:
        if label:
            self.lines.append(f"    {src} -->|{label}| {dst}")
        else:
            self.lines.append(f"    {src} --> {dst}")


def _visit(flow: Flow, ctx: _MermaidCtx) -> tuple[str, str]:
    """Visit a Flow node, return (entry_id, exit_id)."""
    match flow:
        case _Agent(cls=cls):
            nid = ctx.new_id("a")
            ctx.node(nid, cls.__name__)
            return nid, nid

        case _Pure(f=f):
            nid = ctx.new_id("p")
            name = getattr(f, "__name__", "fn")
            ctx.node(nid, name)
            return nid, nid

        case _Map(source=source, f=f):
            s_entry, s_exit = _visit(source, ctx)
            nid = ctx.new_id("m")
            ctx.node(nid, getattr(f, "__name__", "map"))
            ctx.edge(s_exit, nid)
            return s_entry, nid

        case _FlatMap(first=first, next=next_flow):
            f_entry, f_exit = _visit(first, ctx)
            n_entry, n_exit = _visit(next_flow, ctx)
            ctx.edge(f_exit, n_entry)
            return f_entry, n_exit

        case _Zip(left=left, right=right):
            l_entry, l_exit = _visit(left, ctx)
            r_entry, r_exit = _visit(right, ctx)
            fork = ctx.new_id("fork")
            join = ctx.new_id("join")
            ctx.node(fork, "parallel")
            ctx.node(join, "join")
            ctx.edge(fork, l_entry)
            ctx.edge(fork, r_entry)
            ctx.edge(l_exit, join)
            ctx.edge(r_exit, join)
            return fork, join

        case _Branch(source=source, mapping=mapping):
            s_entry, s_exit = _visit(source, ctx)
            merge = ctx.new_id("merge")
            ctx.node(merge, "merge")
            for typ, branch_flow in mapping.items():
                b_entry, b_exit = _visit(branch_flow, ctx)
                ctx.edge(s_exit, b_entry, typ.__name__)
                ctx.edge(b_exit, merge)
            return s_entry, merge

        case _BranchOn(source=source, then=then_flow, otherwise=otherwise_flow):
            s_entry, s_exit = _visit(source, ctx)
            t_entry, t_exit = _visit(then_flow, ctx)
            o_entry, o_exit = _visit(otherwise_flow, ctx)
            merge = ctx.new_id("merge")
            ctx.node(merge, "merge")
            ctx.edge(s_exit, t_entry, "true")
            ctx.edge(s_exit, o_entry, "false")
            ctx.edge(t_exit, merge)
            ctx.edge(o_exit, merge)
            return s_entry, merge

        case _Race(flows=flows):
            race_node = ctx.new_id("race")
            ctx.node(race_node, "race")
            for f in flows:
                f_entry, _ = _visit(f, ctx)
                ctx.edge(race_node, f_entry)
            return race_node, race_node

        case _Loop(body=body):
            loop_node = ctx.new_id("loop")
            ctx.node(loop_node, "loop")
            b_entry, b_exit = _visit(body, ctx)
            ctx.edge(loop_node, b_entry)
            ctx.edge(b_exit, loop_node, "Continue")
            return loop_node, loop_node

        case _LoopWithState(body=body):
            return _visit(_Loop(body=body, max_iter=0), ctx)

        case _Recover(source=source) | _RecoverWith(source=source) | _FallbackTo(source=source):
            return _visit(source, ctx)

        case _DivertTo(source=source, side=side):
            s_entry, s_exit = _visit(source, ctx)
            d_entry, _ = _visit(side, ctx)
            ctx.edge(s_exit, d_entry, "divert")
            return s_entry, s_exit

        case _AndThen(source=source) | _Filter(source=source):
            return _visit(source, ctx)

        case _:
            nid = ctx.new_id("unknown")
            ctx.node(nid, type(flow).__name__)
            return nid, nid
