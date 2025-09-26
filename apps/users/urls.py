from rest_framework.routers import DefaultRouter
from .views import *
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    # auth
    path("auth/register/", RegisterAPIView.as_view(), name="auth-register"),
    path("auth/login/", CustomTokenObtainPairView.as_view(), name="auth-login"),
    path("auth/token/refresh/", CustomTokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/password-reset/", PasswordResetRequestAPIView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", PasswordResetConfirmAPIView.as_view(), name="password-reset-confirm"),
    path("auth/logout/", LogoutAPIView.as_view(), name="auth-logout"),
    path("auth/logout-all/", LogoutAllAPIView.as_view(), name="auth-logout-all"),

    # profile
    path("me/", MeAPIView.as_view(), name="me"),

    # directories
    path("pickup-points/", PickupPointList.as_view(), name="pickuppoint-list"),
    path("pickup-points/<int:pk>/", PickupPointDetail.as_view(), name="pickuppoint-detail"),
    path("warehouses/", WarehouseCNList.as_view(), name="warehouse-list"),
    path("warehouses/<int:pk>/", WarehouseCNDetail.as_view(), name="warehouse-detail"),

    # orders
    path("orders/", MyOrdersAPIView.as_view(), name="my-orders"),
    path("orders/track/<str:tracking_number>/", OrderTrackAPIView.as_view(), name="order-track"),
    path("orders/scan/", OrderScanAPIView.as_view(), name="order-scan"),
    path("orders/find/", OrderFindAPIView.as_view(), name="order-find"),     # NEW
    path("orders/claim/", OrderClaimAPIView.as_view(), name="order-claim"), 
]

