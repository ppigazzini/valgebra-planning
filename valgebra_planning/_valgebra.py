"""The boundary: every name this package takes from valgebra and valgebra-jsonschema.

No other module imports either package, so a rename upstream is one edit here, and
`tests/test_boundaries.py` holds that line.
"""

from __future__ import annotations

from valgebra import (
    Validator,
    anything,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_jsonschema import (
    ARRAY,
    BOOLEAN,
    INTEGER,
    JSON,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    Compiler,
    Tool,
    Unsupported,
    compile_schema,
    finite,
    json_key,
    read_tool,
)
from valgebra_jsonschema.sample import Sampler

__all__ = [
    "ARRAY",
    "BOOLEAN",
    "INTEGER",
    "JSON",
    "NULL",
    "NUMBER",
    "OBJECT",
    "STRING",
    "Compiler",
    "Sampler",
    "Tool",
    "Unsupported",
    "Validator",
    "anything",
    "compile_schema",
    "complement",
    "finite",
    "intersection",
    "json_key",
    "nothing",
    "read_tool",
    "union",
]
