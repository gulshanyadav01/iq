from django.db import models

from config.abstract_models import TimeStampedUUIDModel


class PipelineStep(TimeStampedUUIDModel):
    class StepType(models.TextChoices):
        PDF_TO_MD = "PDF_TO_MD", "PDF to Markdown"
        LLM_EXTRACT = "LLM_EXTRACT", "LLM Extract"

    pipeline = models.ForeignKey(
        "pipelines.Pipeline",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.PositiveSmallIntegerField()
    step_type = models.CharField(max_length=32, choices=StepType.choices, db_index=True)
    name = models.CharField(max_length=255)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["pipeline", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["pipeline", "order"],
                name="pipelines_pipelinestep_unique_pipeline_order",
            ),
        ]

    def __str__(self):
        return f"{self.pipeline.slug} #{self.order} ({self.step_type})"
