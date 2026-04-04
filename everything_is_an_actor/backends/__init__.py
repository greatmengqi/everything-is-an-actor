"""
Actor Backends

提供多种后端实现：
- single_loop: 单 Loop，最高性能
- multi_loop: 多 Loop，I/O 隔离
- multi_process: 多进程，CPU 并行（TODO）
"""

from everything_is_an_actor.backends.single_loop import SingleLoopBackend
from everything_is_an_actor.backends.multi_loop import MultiLoopBackend

__all__ = [
    "SingleLoopBackend",
    "MultiLoopBackend",
]
