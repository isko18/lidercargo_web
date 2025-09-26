from rest_framework.routers import DefaultRouter
from .views import *
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path("auth/register/", RegisterAPIView.as_view(), name="auth-register"),
    path("auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/password/reset/", PasswordResetRequestAPIView.as_view(), name="password-reset"),
    path("auth/password/reset/confirm/", PasswordResetConfirmAPIView.as_view(), name="password-reset-confirm"),
    path("auth/logout/", LogoutAPIView.as_view(), name="auth-logout"),
    path("auth/logout/all/", LogoutAllAPIView.as_view(), name="auth-logout-all"),

    path("catalog/pvz/", PickupPointList.as_view(), name="pvz-list"),
    path("catalog/pvz/<int:pk>/", PickupPointDetail.as_view(), name="pvz-detail"),
    path("catalog/warehouses-cn/", WarehouseCNList.as_view(), name="whcn-list"),
    path("catalog/warehouses-cn/<int:pk>/", WarehouseCNDetail.as_view(), name="whcn-detail"),
    path("orders/", MyOrdersAPIView.as_view(), name="my-orders"),
    path("orders/track/<str:tracking_number>/", OrderTrackAPIView.as_view(), name="order-track"),
]


