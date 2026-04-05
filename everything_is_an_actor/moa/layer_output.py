"""LayerOutput — directive carrier for MOA layer-to-layer communication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

O = TypeVar("O")


@dataclass(frozen=True)
class LayerOutput(Generic[O]):
    """Aggregator output carrying a directive for the next layer.

    If an aggregator returns a plain value, directive is cleared.
    If it returns ``LayerOutput``, ``.result`` is the value and
    ``.directive`` is injected into the next layer's proposer input
    as ``{"input": ..., "directive": ...}``.
    """

    result: O
    directive: str | None = None
