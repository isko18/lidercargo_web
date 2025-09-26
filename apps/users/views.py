from django.urls import path
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
from rest_framework.response import Response
from .models import PickupPoint, WarehouseCN, Order
from rest_framework import generics, permissions
from django.shortcuts import get_object_or_404
from .serializers import (
    RegisterSerializer,
    PickupPointSerializer,
    WarehouseCNSerializer,
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer, 
    ProfileSerializer,
    OrderSerializer
)



class RegisterAPIView(generics.CreateAPIView):
    """POST /auth/register/ — создаёт пользователя и возвращает профиль."""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

class CustomTokenObtainPairView(TokenObtainPairView):
    """POST /auth/login/ — телефон + пароль -> токены + user."""
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]

class PickupPointList(generics.ListAPIView):
    serializer_class = PickupPointSerializer
    permission_classes = [AllowAny]
    def get_queryset(self):
        return PickupPoint.objects.filter(is_active=True).select_related("default_cn_warehouse")

class PickupPointDetail(generics.RetrieveAPIView):
    serializer_class = PickupPointSerializer
    permission_classes = [AllowAny]
    def get_queryset(self):
        return PickupPoint.objects.filter(is_active=True).select_related("default_cn_warehouse")

class WarehouseCNList(generics.ListAPIView):
    serializer_class = WarehouseCNSerializer
    permission_classes = [AllowAny]
    def get_queryset(self):
        return WarehouseCN.objects.filter(is_active=True)

class WarehouseCNDetail(generics.RetrieveAPIView):
    serializer_class = WarehouseCNSerializer
    permission_classes = [AllowAny]
    def get_queryset(self):
        return WarehouseCN.objects.filter(is_active=True)
    
class PasswordResetRequestAPIView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

class PasswordResetConfirmAPIView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

# --- новое: логаут ---
class LogoutAPIView(APIView):
    """
    Принимает refresh-токен и помещает его в blacklist.
    Не требуем auth, чтобы можно было выйти даже с просроченным access.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "Требуется refresh токен."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = RefreshToken(refresh)
            token.blacklist()
        except Exception:
            # на неверный/просроченный тоже отвечаем 205, не даём утечек
            pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


class LogoutAllAPIView(APIView):
    """
    Разлогинить пользователя на всех устройствах.
    Требует действующий access (IsAuthenticated).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tokens = OutstandingToken.objects.filter(user=request.user)
        for t in tokens:
            try:
                BlacklistedToken.objects.get_or_create(token=t)
            except Exception:
                pass
        return Response(status=status.HTTP_205_RESET_CONTENT)
    
class MeAPIView(generics.RetrieveUpdateAPIView):
    """
    GET  /me/    -> профиль текущего пользователя
    PATCH /me/   -> частичное обновление (full_name, email, pickup_point_id)
    """
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user
    




class MyOrdersAPIView(generics.ListAPIView):
    """GET /orders/ — список заказов текущего пользователя"""
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user)


class OrderTrackAPIView(APIView):
    """GET /orders/track/{tracking_number}/ — отследить заказ по треку"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, tracking_number: str):
        order = get_object_or_404(Order, tracking_number=tracking_number)
        serializer = OrderSerializer(order)
        return Response(serializer.data)
