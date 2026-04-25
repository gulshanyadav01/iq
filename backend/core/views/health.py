from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Health check",
        tags=["Health"],
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def get(self, request):
        return Response({"status": "ok"})