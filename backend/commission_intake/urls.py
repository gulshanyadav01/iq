from django.urls import path, include
from rest_framework.routers import DefaultRouter

from commission_intake.views import CommissionDocumentViewSet, CICSResultViewSet

router = DefaultRouter()
router.register(r"documents", CommissionDocumentViewSet, basename="commission-documents")
router.register(r"results", CICSResultViewSet, basename="commission-results")

urlpatterns = [
    path("", include(router.urls)),
]
