from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from pipelines.models import Pipeline
from pipelines.serializers import PipelineSerializer


class PipelinesViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only listing of pipelines.

    v1 has no PATCH endpoint — prompt edits happen per-run via `config_overrides`
    on POST /api/pipelines/runs/. See plan doc, section F-fixes, F10.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PipelineSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return (
            Pipeline.objects.filter(
                Q(is_seeded=True) | Q(created_by=self.request.user)
            )
            .prefetch_related("steps")
            .order_by("name")
        )
