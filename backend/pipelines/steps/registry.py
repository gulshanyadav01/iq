from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipelines.steps.executor import StepExecutor

# Concrete executors will register themselves once implemented. v1 has two
# planned types: PDF_TO_MD and LLM_EXTRACT (per the approved plan). Each
# `register_executor` call below is a no-op until the corresponding module
# defines a real class — keeping the registry initialized to an empty dict
# means `get_executor` raises a clear error when callers run before wiring.
STEP_REGISTRY: dict[str, type["StepExecutor"]] = {}


def register_executor(cls: type["StepExecutor"]) -> type["StepExecutor"]:
    """Decorator: register a StepExecutor subclass under its declared step_type."""
    step_type = getattr(cls, "step_type", None)
    if not step_type:
        raise ValueError(f"{cls.__name__} must declare a class-level `step_type`")
    if step_type in STEP_REGISTRY:
        raise ValueError(f"Duplicate executor registration for step_type={step_type}")
    STEP_REGISTRY[step_type] = cls
    return cls


def get_executor(step_type: str) -> type["StepExecutor"]:
    try:
        return STEP_REGISTRY[step_type]
    except KeyError:
        raise KeyError(
            f"No executor registered for step_type={step_type!r}. "
            f"Known: {sorted(STEP_REGISTRY)}"
        )


# Import side effects: pull in concrete executors so they self-register via the
# decorator above. Keep imports at the bottom to avoid circulars with executor.py.
from pipelines.steps import pdf_to_md  # noqa: E402,F401
from pipelines.steps import llm_extract  # noqa: E402,F401
