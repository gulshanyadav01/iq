from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commission_intake.models import CICSResult
from commission_intake.serializers import CICSResultSerializer


class CICSResultViewSet(viewsets.GenericViewSet):
    """
    Read-only access to extracted CICS results.

    GET /api/commission-intake/results/{id}/
        - Returns the full CICS document JSON, raw extraction, warnings/errors,
          and per-sheet breakdown for a completed commission document.
    """

    permission_classes = [IsAuthenticated]
    queryset = CICSResult.objects.select_related("document").all()
    serializer_class = CICSResultSerializer

    @extend_schema(
        summary="Get CICS result",
        description="Returns the full CICS document, raw extraction JSON, and per-sheet breakdown.",
        tags=["Commission Document"],
        responses={200: CICSResultSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = CICSResultSerializer(instance)
        return Response(serializer.data)
