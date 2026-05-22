"""Config snapshotting helpers.

Per the approved plan (F4): shallow-key replace, NOT recursive deep-merge.

    merge_config({"a": {"x": 1}}, {"a": {"y": 2}}) == {"a": {"y": 2}}

Top-level keys in the override replace top-level keys in the base. Predictable,
easy to test, easy to reason about. If a future step type needs partial-config
overrides, opt in explicitly per step type rather than changing this rule globally.
"""

from copy import deepcopy
from typing import Any


def merge_config(base: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    base_copy = deepcopy(base or {})
    if not override:
        return base_copy
    base_copy.update(override)
    return base_copy
