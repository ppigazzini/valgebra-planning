"""Plans of tool calls proven well-typed for every output the tool contracts admit.

A tool's contract is a pair of sets valgebra decides inclusion over. A program of calls and
type tests is accepted only where every value it passes on is proven inside the argument it
fills, whatever values inside their output schemas the tools return.
"""

from __future__ import annotations

__all__: list[str] = []
