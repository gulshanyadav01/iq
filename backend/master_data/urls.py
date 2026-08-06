from django.urls import include, path
from rest_framework.routers import DefaultRouter

from master_data.views import CarrierMasterUploadViewSet, MasterDataUploadViewSet

router = DefaultRouter()
router.register(r"uploads", MasterDataUploadViewSet, basename="master-data-uploads")
router.register(
    r"carrier-uploads",
    CarrierMasterUploadViewSet,
    basename="master-data-carrier-uploads",
)

urlpatterns = [
    path("", include(router.urls)),
]
