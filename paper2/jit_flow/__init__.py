"""JIT Flow: inference-time generation of typed Flow expressions.

This package implements the architecture described in Section 4 of the
paper. It is intentionally minimal --- the goal is a reproducible
reference implementation, not a production framework.

Components:

  * `prompt`        : prompt template + few-shot examples
  * `parser`        : YAML text -> Flow AST (delegates to existing flow.serialize)
  * `repair`        : one-shot repair loop on parser/type errors
  * `inference_loop`: prompt -> parse -> type-check -> dispatch -> feedback
"""

from .inference_loop import JITFlowLoop, LoopConfig, LoopOutcome
from .parser import ParseError, parse_flow_yaml
from .prompt import build_prompt
from .repair import RepairResult, attempt_repair

__all__ = [
    "JITFlowLoop",
    "LoopConfig",
    "LoopOutcome",
    "ParseError",
    "RepairResult",
    "attempt_repair",
    "build_prompt",
    "parse_flow_yaml",
]
