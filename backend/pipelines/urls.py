from django.urls import include, path
from rest_framework.routers import DefaultRouter

from pipelines.views import PipelinesViewSet, PipelineRunsViewSet

router = DefaultRouter()
router.register(r"pipelines", PipelinesViewSet, basename="pipelines")
router.register(r"runs", PipelineRunsViewSet, basename="pipeline-runs")

urlpatterns = [
    path("", include(router.urls)),
]
