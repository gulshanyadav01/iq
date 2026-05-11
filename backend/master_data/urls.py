from django.urls import include, path
from rest_framework.routers import DefaultRouter

from master_data.views import MasterDataUploadViewSet

router = DefaultRouter()
router.register(r"uploads", MasterDataUploadViewSet, basename="master-data-uploads")

urlpatterns = [
    path("", include(router.urls)),
]
