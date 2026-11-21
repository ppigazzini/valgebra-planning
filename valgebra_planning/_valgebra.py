"""The boundary: every name this package takes from valgebra and valgebra-jsonschema.

No other module imports either package, so a rename upstream is one edit here, and
`tests/test_boundaries.py` holds that line.
"""

from __future__ import annotations

from valgebra import (
    ValidationError,
    Validator,
    anything,
    complement,
    intersection,
    nothing,
    union,
)
from valgebra_jsonschema import (
    ANNOTATIONS,
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
    older,
    read_tool,
    upgrade,
)
from valgebra_jsonschema.sample import Sampler

__all__ = [
    "ANNOTATIONS",
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
    "ValidationError",
    "Validator",
    "anything",
    "compile_schema",
    "complement",
    "finite",
    "intersection",
    "json_key",
    "nothing",
    "older",
    "read_tool",
    "union",
    "upgrade",
]
