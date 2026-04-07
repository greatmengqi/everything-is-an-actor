"""Parse LLM YAML output into a Flow AST.

The wire format is the *serializable* subset of the Flow ADT plus the
ZipAll variant (which the upstream `flow.serialize.from_dict` does not
yet handle as of v0.1.8 of `everything-is-an-actor`). We implement the
build directly here to avoid being blocked on an upstream change.

The grammar handled here matches `jit_flow.prompt._GRAMMAR_REFERENCE`
exactly: any change in one must be reflected in the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

import yaml

# Re-exported for callers who want to construct Flow ADTs themselves.
# Imports are local to keep this module importable from environments
# without the runtime installed (e.g. unit tests for the regex extractor).


_FENCED_YAML_RE = re.compile(r"```yaml\s*\n(?P<body>.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class ParseError(Exception):
    """Raised when LLM output cannot be parsed into a Flow AST.

    The `phase` field tells JIT Flow's repair loop which kind of error
    occurred so the repair prompt can be specialized.
    """

    phase: str  # one of: "extract" | "yaml" | "ast"
    message: str
    raw_output: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"[{self.phase}] {self.message}"


def _extract_yaml_body(llm_output: str) -> str:
    """Pull the fenced ```yaml ... ``` body out of the LLM response.

    If no fence is found, fall back to treating the entire response as
    YAML --- some models forget the fence on the first try and we want
    the parser to give them a chance.
    """
    match = _FENCED_YAML_RE.search(llm_output)
    if match is not None:
        return match.group("body")
    return llm_output.strip()


def parse_flow_yaml(
    llm_output: str,
    registry: Mapping[str, type],
) -> Any:
    """Parse LLM output into a Flow AST node.

    Returns a Flow ADT (frozen dataclass tree). Raises `ParseError`
    with a phase label so the repair loop can craft a focused retry.

    `registry` maps agent class names to their Python class objects;
    every `Agent` node in the LLM output must reference a name in
    this registry.
    """
    body = _extract_yaml_body(llm_output)

    try:
        as_dict = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ParseError(
            phase="yaml",
            message=f"YAML syntax error: {exc}",
            raw_output=llm_output,
        ) from exc

    if not isinstance(as_dict, dict):
        raise ParseError(
            phase="yaml",
            message=(
                "Top-level YAML must be a mapping with a `type` field; got "
                f"{type(as_dict).__name__}."
            ),
            raw_output=llm_output,
        )

    try:
        return _build(as_dict, registry)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(
            phase="ast",
            message=f"Flow AST construction failed: {exc}",
            raw_output=llm_output,
        ) from exc


def _build(d: Any, registry: Mapping[str, type]) -> Any:
    """Recursive AST builder for the JIT Flow wire format."""
    if not isinstance(d, dict):
        raise ParseError(
            phase="ast",
            message=f"every node must be a mapping; got {type(d).__name__}",
            raw_output="",
        )
    if "type" not in d:
        raise ParseError(
            phase="ast",
            message=f"node missing required `type` field; keys: {sorted(d)}",
            raw_output="",
        )

    typ = d["type"]

    # Lazy imports of the runtime ADT classes so this module can be
    # unit-tested without the full runtime in some environments.
    from everything_is_an_actor.flow.flow import (  # type: ignore
        _Agent,
        _FallbackTo,
        _FlatMap,
        _Loop,
        _Race,
        _RecoverWith,
        _Zip,
        _ZipAll,
    )

    if typ == "Agent":
        cls_name = d.get("cls")
        if not isinstance(cls_name, str):
            raise ParseError(
                phase="ast",
                message="`Agent` node must have string field `cls`",
                raw_output="",
            )
        if cls_name not in registry:
            raise ParseError(
                phase="ast",
                message=(
                    f"`Agent.cls = {cls_name!r}` is not in the catalog; "
                    f"available: {sorted(registry)}"
                ),
                raw_output="",
            )
        timeout = float(d.get("timeout", 30.0))
        return _Agent(cls=registry[cls_name], timeout=timeout)

    if typ == "FlatMap":
        return _FlatMap(
            first=_build(d["first"], registry),
            next=_build(d["next"], registry),
        )

    if typ == "Zip":
        return _Zip(
            left=_build(d["left"], registry),
            right=_build(d["right"], registry),
        )

    if typ == "ZipAll":
        flows = d.get("flows")
        if not isinstance(flows, list) or len(flows) < 2:
            raise ParseError(
                phase="ast",
                message="`ZipAll.flows` must be a list of at least 2 nodes",
                raw_output="",
            )
        return _ZipAll(flows=tuple(_build(f, registry) for f in flows))

    if typ == "Race":
        flows = d.get("flows")
        if not isinstance(flows, list) or len(flows) < 2:
            raise ParseError(
                phase="ast",
                message="`Race.flows` must be a list of at least 2 nodes",
                raw_output="",
            )
        return _Race(flows=tuple(_build(f, registry) for f in flows))

    if typ == "RecoverWith":
        return _RecoverWith(
            source=_build(d["source"], registry),
            handler=_build(d["handler"], registry),
        )

    if typ == "FallbackTo":
        return _FallbackTo(
            source=_build(d["source"], registry),
            fallback=_build(d["fallback"], registry),
        )

    if typ == "Loop":
        return _Loop(
            body=_build(d["body"], registry),
            max_iter=int(d.get("max_iter", 10)),
        )

    raise ParseError(
        phase="ast",
        message=(
            f"unknown node type {typ!r}; allowed: "
            "Agent, FlatMap, Zip, ZipAll, Race, RecoverWith, FallbackTo, Loop"
        ),
        raw_output="",
    )
