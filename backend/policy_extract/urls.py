from django.urls import path, include
from rest_framework.routers import DefaultRouter
from policy_extract.views import PolicyExtractionRequestViewSet

router = DefaultRouter()
router.register(r"requests", PolicyExtractionRequestViewSet, basename="policy-extract-requests")

urlpatterns = [
    path('', include(router.urls)),
]
