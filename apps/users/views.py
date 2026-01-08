from django.urls import path
from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch
from django.db import transaction, IntegrityError
from datetime import timedelta
from django.shortcuts import render

from .models import PickupPoint, WarehouseCN, Order, TrackingEvent, Base
from .serializers import (
    RegisterSerializer,
    PickupPointSerializer,
    WarehouseCNSerializer,
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileSerializer,
    OrderSerializer,
    OrderScanSerializer,
    BaseSerializer
)

# 👇 НОВОЕ: подключаем пермишен
from .permissions import IsEmployee


# -------------------------
#   Auth
# -------------------------
def index(request):
    return render(request, 'index.html')


class RegisterAPIView(generics.CreateAPIView):
    """POST /auth/register/ — создаёт пользователя и возвращает профиль."""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class CustomTokenObtainPairView(TokenObtainPairView):
    """POST /auth/login/ — телефон + пароль -> токены + user."""
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


class CustomTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]


class PasswordResetRequestAPIView(generics.CreateAPIView):
    """POST /auth/password-reset/"""
    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer


class PasswordResetConfirmAPIView(generics.CreateAPIView):
    """POST /auth/password-reset/confirm/"""
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer


class LogoutAPIView(APIView):
    """
    POST /auth/logout/
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
            pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


class LogoutAllAPIView(APIView):
    """
    POST /auth/logout-all/
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


# -------------------------
#   Profile
# -------------------------
class MeAPIView(generics.RetrieveUpdateAPIView):
    """
    GET  /me/    -> профиль текущего пользователя
    PATCH /me/   -> частичное обновление (full_name, email, pickup_point_id)
    """
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# -------------------------
#   Справочники
# -------------------------
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


# -------------------------
#   Orders
# -------------------------
class MyOrdersAPIView(generics.ListAPIView):
    """GET /orders/ — список заказов текущего пользователя"""
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .prefetch_related(
                Prefetch(
                    "events",
                    queryset=TrackingEvent.objects.order_by("timestamp"),
                )
            )
            .order_by("-created_at")
        )


class OrderTrackAPIView(APIView):
    """GET /orders/track/{tracking_number}/ — отследить заказ по треку"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, tracking_number: str):
        tracking_number = (tracking_number or "").strip().upper()  # 👈 нормализация трека
        order = get_object_or_404(
            Order.objects.prefetch_related(
                Prefetch(
                    "events",
                    queryset=TrackingEvent.objects.order_by("timestamp"),
                )
            ),
            tracking_number=tracking_number,
        )
        serializer = OrderSerializer(order)
        return Response(serializer.data)


class OrderScanAPIView(generics.CreateAPIView):
    serializer_class = OrderScanSerializer
    # 👇 только авторизованные + только сотрудники/админы
    permission_classes = [IsAuthenticated, IsEmployee]

    def create(self, request, *args, **kwargs):
        # опционально нормализуем трек ещё до сериалайзера
        if "tracking_number" in request.data and isinstance(request.data["tracking_number"], str):
            request.data["tracking_number"] = request.data["tracking_number"].strip().upper()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()  # {"order": ..., "created_event": ...}

        # 201 — если реально создано новое событие; 200 — если уже финальный статус
        status_code = status.HTTP_201_CREATED if result.get("created_event") else status.HTTP_200_OK
        return Response(serializer.data, status=status_code)


class OrderFindAPIView(APIView):
    """
    GET /orders/find/?tracking_number=AB123
    Возвращает заказ (если есть) + флаги:
      - is_owner: заказ уже принадлежит вам
      - can_claim: можно ли привязать к себе (никому не принадлежит или уже ваш)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tn = request.query_params.get("tracking_number") or request.query_params.get("q")
        if not tn:
            return Response({"detail": "Укажите параметр tracking_number."}, status=status.HTTP_400_BAD_REQUEST)

        tn = tn.strip().upper()  # 👈 нормализация трека

        try:
            order = (
                Order.objects
                .prefetch_related(Prefetch("events", queryset=TrackingEvent.objects.order_by("timestamp")))
                .get(tracking_number=tn)
            )
        except Order.DoesNotExist:
            return Response({"detail": "Трек не найден."}, status=status.HTTP_404_NOT_FOUND)

        is_owner = (order.user_id == request.user.id)
        can_claim = (order.user_id is None) or is_owner

        data = OrderSerializer(order).data
        data.update({"is_owner": is_owner, "can_claim": can_claim})
        return Response(data, status=status.HTTP_200_OK)

CLIENT_CREATED_STATUS = "Ожидает поступления на склад в Китае"


class OrderClaimAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tn = (request.data.get("tracking_number") or "").strip().upper()
        if not tn:
            return Response({"detail": "Укажите tracking_number."}, status=status.HTTP_400_BAD_REQUEST)

        description = (request.data.get("description") or "").strip()

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(tracking_number=tn)
                .first()
            )

            created_now = False

            # 1) Если заказа нет — создаём (и только тогда ставим стартовый статус)
            if not order:
                try:
                    order = Order.objects.create(
                        tracking_number=tn,
                        description=description,
                        user=request.user,
                    )
                    created_now = True
                except IntegrityError:
                    order = (
                        Order.objects.select_for_update()
                        .filter(tracking_number=tn)
                        .first()
                    )
                    created_now = False

            if not order:
                return Response({"detail": "Не удалось создать/найти заказ."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 2) Если уже привязан к другому — запрет
            if order.user_id and order.user_id != request.user.id:
                return Response(
                    {"detail": "Заказ уже привязан к другому пользователю."},
                    status=status.HTTP_409_CONFLICT
                )

            # 3) Присваиваем заказ текущему пользователю (статусы НЕ трогаем)
            order.user = request.user
            if created_now and description:
                # описание логично сохранять при создании
                order.description = description
                order.save(update_fields=["user", "description"])
            else:
                order.save(update_fields=["user"])

            # 4) Стартовый статус создаём ТОЛЬКО если трек был создан сейчас
            if created_now:
                TrackingEvent.objects.create(
                    order=order,
                    status=CLIENT_CREATED_STATUS,
                    location="(клиент)",
                    actor=None,
                )

        data = OrderSerializer(order).data
        data.update({"claimed": True, "created": created_now})
        return Response(data, status=status.HTTP_200_OK)


class BaseList(generics.ListAPIView):
    serializer_class = BaseSerializer
    permission_classes = [AllowAny]
    
    queryset = Base.objects.all()