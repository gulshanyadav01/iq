from rest_framework import serializers

from pipelines.models import Pipeline, PipelineStep


class PipelineStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineStep
        fields = ("id", "order", "step_type", "name", "config")


class PipelineSerializer(serializers.ModelSerializer):
    steps = PipelineStepSerializer(many=True, read_only=True)

    class Meta:
        model = Pipeline
        fields = ("id", "slug", "name", "description", "is_seeded", "steps", "created_at")
