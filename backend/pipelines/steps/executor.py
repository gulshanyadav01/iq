from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class PrevStepOutput:
    """A previous step's result, passed into the next executor in chain order.

    `output` is the dict that the prior step's executor returned (artifact
    refs, token counts, etc.). The current step is NEVER in this list.
    """

    order: int
    step_type: str
    output: dict[str, Any]


class StepExecutor(ABC):
    """Base class for pipeline step executors.

    Subclasses declare a class-level `step_type` matching one of
    `PipelineStep.StepType` values and register themselves in
    `pipelines.steps.registry.STEP_REGISTRY`.
    """

    step_type: ClassVar[str]

    @abstractmethod
    def run(
        self,
        *,
        step_run: "Any",  # pipelines.models.StepRun — quoted to avoid circular import
        prev_outputs: list[PrevStepOutput],
    ) -> dict[str, Any]:
        """Execute the step. Returns the output dict to persist on `step_run.output`.

        Implementations write any artifacts (MD, blocks.json, LLM response) to
        MinIO via Django's `default_storage` and reference them by key in the
        returned dict. The caller is responsible for persisting `output` and
        updating status/timestamps.
        """
        raise NotImplementedError
