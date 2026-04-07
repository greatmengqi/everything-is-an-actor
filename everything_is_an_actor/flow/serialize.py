"""Flow serialization — to_dict / from_dict for JSON-compatible round-trip.

Only structural variants (Agent, FlatMap, Zip, Race, Loop, Branch, etc.) are
serializable. Variants containing lambdas (Pure, Map, Filter, etc.) raise
TypeError — they must stay in Python code.
"""

from __future__ import annotations

from typing import Any

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

_NOT_SERIALIZABLE: tuple[type, ...] = (
    _Pure,
    _Map,
    _Filter,
    _AndThen,
    _Recover,
    _DivertTo,
    _BranchOn,
)


def to_dict(flow: Flow) -> dict[str, Any]:
    """Serialize a Flow ADT to a JSON-compatible dict."""
    if isinstance(flow, _NOT_SERIALIZABLE):
        raise TypeError(
            f"Flow variant {type(flow).__name__.lstrip('_')} contains a callable and is not serializable. "
            "Keep it in Python code."
        )

    match flow:
        case _Agent(cls=cls, timeout=timeout):
            d: dict[str, Any] = {"type": "Agent", "cls": cls.__name__}
            if timeout != 30.0:
                d["timeout"] = timeout
            return d

        case _FlatMap(first=first, next=next_flow):
            return {"type": "FlatMap", "first": to_dict(first), "next": to_dict(next_flow)}

        case _Zip(left=left, right=right):
            return {"type": "Zip", "left": to_dict(left), "right": to_dict(right)}

        case _Race(flows=flows):
            return {"type": "Race", "flows": [to_dict(f) for f in flows]}

        case _Loop(body=body, max_iter=max_iter):
            return {"type": "Loop", "body": to_dict(body), "max_iter": max_iter}

        case _LoopWithState(body=body, max_iter=max_iter):
            return {"type": "LoopWithState", "body": to_dict(body), "max_iter": max_iter}

        case _Branch(source=source, mapping=mapping):
            return {
                "type": "Branch",
                "source": to_dict(source),
                "mapping": {k.__name__: to_dict(v) for k, v in mapping.items()},
            }

        case _RecoverWith(source=source, handler=handler):
            return {"type": "RecoverWith", "source": to_dict(source), "handler": to_dict(handler)}

        case _FallbackTo(source=source, fallback=fallback):
            return {"type": "FallbackTo", "source": to_dict(source), "fallback": to_dict(fallback)}

        case _:
            raise TypeError(f"Unknown Flow variant: {type(flow).__name__}")


def from_dict(d: dict[str, Any], registry: dict[str, type]) -> Flow:
    """Deserialize a dict back to a Flow ADT, using registry to resolve agent names."""
    typ = d["type"]

    match typ:
        case "Agent":
            return _Agent(cls=registry[d["cls"]], timeout=d.get("timeout", 30.0))
        case "FlatMap":
            return _FlatMap(first=from_dict(d["first"], registry), next=from_dict(d["next"], registry))
        case "Zip":
            return _Zip(left=from_dict(d["left"], registry), right=from_dict(d["right"], registry))
        case "Race":
            return _Race(flows=[from_dict(f, registry) for f in d["flows"]])
        case "Loop":
            return _Loop(body=from_dict(d["body"], registry), max_iter=d["max_iter"])
        case "LoopWithState":
            return _LoopWithState(body=from_dict(d["body"], registry), max_iter=d["max_iter"])
        case "Branch":
            source = from_dict(d["source"], registry)
            mapping = {registry[k]: from_dict(v, registry) for k, v in d["mapping"].items()}
            return _Branch(source=source, mapping=mapping)
        case "RecoverWith":
            return _RecoverWith(source=from_dict(d["source"], registry), handler=from_dict(d["handler"], registry))
        case "FallbackTo":
            return _FallbackTo(source=from_dict(d["source"], registry), fallback=from_dict(d["fallback"], registry))
        case _:
            raise ValueError(f"Unknown Flow type: {typ}")
