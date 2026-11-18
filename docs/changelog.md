---
description: Every released version, what it changed, and what counts as a break.
---

## Versioning, and what counts as a break

The version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Below `0.1.0`
there is no minor number to carry a break, so every `0.0.x` release may break, and its notes say
where. A break is a public name removed or changed, a program accepted that is now refused or the
reverse, or a command's output or exit status changed for a case it already answered; the public
names are each module's `__all__` ([the Python API](api.md)). Each release takes valgebra's and
valgebra-jsonschema's newest `0.0.x`, and the ranges it requires say which.

<!-- The repository's CHANGELOG.md, served verbatim: one list, rendered in two places. -->

--8<-- "CHANGELOG.md"
