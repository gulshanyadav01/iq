from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.serializers import UserSerializer
from config.permissions import IsSelf


@extend_schema(exclude=True)
class UserViewSet(viewsets.ModelViewSet):
    model = get_user_model()
    queryset = model.objects.order_by('first_name')
    permission_classes = [IsAuthenticated, IsSelf]
    serializer_class = UserSerializer
    pagination_class = PageNumberPagination
    filter_backends = [filters.SearchFilter, DjangoFilterBackend]
    filter_fields = ['first_name', 'last_name', 'email']
    search_fields = ['first_name', 'last_name', 'email']

    def get_serializer_context(self):
        return {
            'user': self.request.user
        }

    def list(self, request, *args, **kwargs):
        raise NotFound()

    @action(detail=False, methods=['GET'])
    def me(self, request):
        return Response(self.serializer_class(instance=request.user).data)
