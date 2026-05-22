from rest_framework import serializers

from pipelines.models import Pipeline, PipelineRun, StepRun


# Hard limits (per F6 / design Pass 2)
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25MB
PDF_MAGIC = b"%PDF-"


class StepRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = StepRun
        fields = (
            "id",
            "order",
            "step_type",
            "status",
            "config_snapshot",
            "output",
            "started_at",
            "finished_at",
            "error",
        )


class PipelineRunSerializer(serializers.ModelSerializer):
    step_runs = StepRunSerializer(many=True, read_only=True)
    pipeline_slug = serializers.SlugRelatedField(
        source="pipeline", slug_field="slug", read_only=True
    )

    class Meta:
        model = PipelineRun
        fields = (
            "id",
            "pipeline_run_id",
            "pipeline_slug",
            "status",
            "original_filename",
            "inputs",
            "result",
            "error",
            "step_runs",
            "created_at",
            "updated_at",
        )


class PipelineRunCreateSerializer(serializers.Serializer):
    """Input for ``POST /api/pipelines/runs/``.

    Two flows:
      1) Fresh run: ``file`` is required (multipart), ``parent_run_id`` is empty.
      2) Live prompt diff: ``parent_run_id`` references a completed run with
         a usable PDF_TO_MD output; ``file`` is NOT required. ``config_overrides``
         supplies the new prompt for step 2.
    """

    pipeline_slug = serializers.CharField()
    file = serializers.FileField(required=False, allow_empty_file=False)
    parent_run_id = serializers.CharField(required=False, allow_blank=True)
    # CharField + manual JSON parse because multipart/form-data sends nested
    # JSON as a raw string; DictField silently drops it.
    config_overrides = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        attrs = super().to_internal_value(data)
        raw = attrs.get("config_overrides")
        if raw in (None, ""):
            attrs["config_overrides"] = {}
            return attrs
        if isinstance(raw, dict):
            return attrs
        import json
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            raise serializers.ValidationError(
                {"config_overrides": "Must be a JSON object."}
            )
        if not isinstance(parsed, dict):
            raise serializers.ValidationError(
                {"config_overrides": "Must be a JSON object."}
            )
        attrs["config_overrides"] = parsed
        return attrs

    def validate_pipeline_slug(self, value):
        user = self.context["request"].user
        from django.db.models import Q
        if not Pipeline.objects.filter(
            Q(is_seeded=True) | Q(created_by=user), slug=value
        ).exists():
            raise serializers.ValidationError("Pipeline not found or not accessible.")
        return value

    def validate_parent_run_id(self, value):
        if not value:
            return value
        user = self.context["request"].user
        if not PipelineRun.objects.filter(
            pipeline_run_id=value,
            created_by=user,
            status=PipelineRun.Status.COMPLETED,
        ).exists():
            raise serializers.ValidationError(
                "parent_run_id not found, not owned by you, or not completed."
            )
        return value

    def validate_file(self, value):
        # Size cap
        if value.size > MAX_FILE_SIZE_BYTES:
            raise serializers.ValidationError(
                f"File exceeds 25MB cap ({value.size / 1024 / 1024:.1f}MB)."
            )
        # MIME via magic bytes
        head = value.read(5)
        value.seek(0)
        if head != PDF_MAGIC:
            raise serializers.ValidationError(
                "Only PDF files are supported (magic bytes %PDF- not found)."
            )
        return value

    def validate(self, attrs):
        # Either a fresh file OR a parent_run_id must be supplied.
        if not attrs.get("file") and not attrs.get("parent_run_id"):
            raise serializers.ValidationError(
                "Provide either `file` (fresh upload) or `parent_run_id` (live diff)."
            )
        return attrs
