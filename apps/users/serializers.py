# serializers.py
from django.conf import settings
from django.contrib.auth import password_validation
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import PickupPoint, WarehouseCN, User, Order, TrackingEvent, handle_scan


# -------------------------
#  Пользователь / Регистрация
# -------------------------
class RegisterSerializer(serializers.ModelSerializer):
    pickup_point_id = serializers.PrimaryKeyRelatedField(
        source="pickup_point",
        queryset=PickupPoint.objects.filter(is_active=True),
        write_only=True,
    )
    client_code = serializers.CharField(read_only=True)
    cn_warehouse_address = serializers.SerializerMethodField(read_only=True)

    # опциональные ручные поля
    lc_number = serializers.CharField(required=False, allow_blank=True)
    region_code = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = (
            "full_name",
            "phone",
            "email",
            "pickup_point_id",
            "password",
            "lc_number",
            "region_code",
            "client_code",
            "cn_warehouse_address",
        )
        extra_kwargs = {"password": {"write_only": True}}

    def validate_phone(self, value: str) -> str:
        return value.replace(" ", "")

    def validate_email(self, value):
        if not value:
            return value
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Этот email уже используется.")
        return value

    def validate(self, attrs):
        password = attrs.get("password")
        temp_user = User(phone=attrs.get("phone"), full_name=attrs.get("full_name"))
        password_validation.validate_password(password=password, user=temp_user)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        email = validated_data.get("email")
        if email:
            validated_data["email"] = email.strip().lower()

        user = User.objects.create_user(password=password, **validated_data)
        if not user.client_code:
            user.assign_client_code(save=True)
        return user

    def get_cn_warehouse_address(self, obj: User):
        return obj.cn_warehouse_address


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Логин по телефону (USERNAME_FIELD='phone') + обогащённый ответ."""

    @classmethod
    def get_token(cls, user: User):
        token = super().get_token(user)
        token["phone"] = user.phone
        token["full_name"] = user.full_name
        token["client_code"] = user.client_code
        token["pvz_id"] = user.pickup_point_id
        token["pvz_region"] = user.pickup_point.region_code
        token["pvz_branch"] = user.pickup_point.branch_code
        token["pvz_lc_prefix"] = user.pickup_point.lc_prefix
        return token

    def validate(self, attrs):
        data = super().validate(attrs)  # access/refresh + self.user

        if not self.user.is_active or getattr(self.user, "is_blocked", False):
            raise AuthenticationFailed("Пользователь заблокирован или неактивен.")

        if not self.user.client_code:
            self.user.assign_client_code()

        pvz = self.user.pickup_point
        data["user"] = {
            "id": self.user.id,
            "full_name": self.user.full_name,
            "phone": self.user.phone,
            "client_code": self.user.client_code,
            "client_code_display": self.user.client_code_display,
            "pickup_point": {
                "id": pvz.id,
                "name_ru": pvz.name_ru,
                "code_label": pvz.code_label,
                "region_code": pvz.region_code,
                "branch_code": pvz.branch_code,
                "lc_prefix": pvz.lc_prefix,
            },
            "cn_warehouse_address": self.user.cn_warehouse_address,
            "is_staff": self.user.is_staff,
        }
        return data


# -------------------------
#  Password reset
# -------------------------
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def create(self, validated_data):
        email = validated_data["email"].strip().lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            self.instance = {"detail": "Если этот email зарегистрирован, мы отправили ссылку для восстановления."}
            return self.instance

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        base = getattr(settings, "PASSWORD_RESET_FRONTEND_URL", "https://example.com/password-reset")
        reset_link = f"{base}?uid={uid}&token={token}"

        subject = "Сброс пароля LIDER CARGO"
        message = (
            f"Здравствуйте, {user.full_name}!\n\n"
            f"Для сброса пароля перейдите по ссылке:\n{reset_link}\n\n"
            f"Если вы не запрашивали сброс, просто игнорируйте это письмо."
        )
        send_mail(
            subject,
            message,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [email],
            fail_silently=True,
        )

        self.instance = {"detail": "Если этот email зарегистрирован, мы отправили ссылку для восстановления."}
        if getattr(settings, "DEBUG", False):
            self.instance.update({"uid": uid, "token": token, "reset_link": reset_link})
        return self.instance

    def to_representation(self, instance):
        return instance


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        try:
            uid = force_str(urlsafe_base64_decode(attrs["uid"]))
            self.user = User.objects.get(pk=uid)
        except Exception:
            raise serializers.ValidationError("Неверная ссылка для сброса.")

        if not default_token_generator.check_token(self.user, attrs["token"]):
            raise serializers.ValidationError("Неверный или просроченный токен.")

        password_validation.validate_password(attrs["new_password"], user=self.user)
        return attrs

    def create(self, validated_data):
        self.user.set_password(validated_data["new_password"])
        self.user.save(update_fields=["password"])
        self.instance = {"detail": "Пароль успешно обновлён."}
        return self.instance

    def to_representation(self, instance):
        return instance


# -------------------------
#  Справочники
# -------------------------
class WarehouseCNSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarehouseCN
        fields = ("id", "name", "address_cn", "contact_name", "contact_phone", "is_active")


class PickupPointSerializer(serializers.ModelSerializer):
    default_cn_warehouse = WarehouseCNSerializer(read_only=True)

    class Meta:
        model = PickupPoint
        fields = (
            "id",
            "name_ru",
            "name_kg",
            "address",
            "code_label",
            "region_code",
            "branch_code",
            "lc_prefix",
            "default_cn_warehouse",
            "is_active",
        )


# -------------------------
#  Профиль
# -------------------------
class ProfileSerializer(serializers.ModelSerializer):
    pickup_point = PickupPointSerializer(read_only=True)
    client_code_display = serializers.CharField(read_only=True)
    cn_warehouse_address = serializers.CharField(read_only=True)

    pickup_point_id = serializers.PrimaryKeyRelatedField(
        source="pickup_point",
        queryset=PickupPoint.objects.filter(is_active=True),
        write_only=True,
        required=False,
    )

    class Meta:
        model = User
        fields = (
            "id",
            "full_name",
            "phone",                 # логин — только чтение
            "email",
            "pickup_point",          # read-only nested
            "pickup_point_id",       # write-only PK
            "client_code",
            "client_code_display",
            "cn_warehouse_address",
            "is_staff",
        )
        read_only_fields = ("phone", "client_code", "is_staff")

    def validate_email(self, value):
        if value is None or value == "":
            return value
        value = value.strip().lower()
        qs = User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk if self.instance else None)
        if qs.exists():
            raise serializers.ValidationError("Этот email уже используется.")
        return value

    def update(self, instance: User, validated_data):
        pickup_was = instance.pickup_point_id
        pickup_new = validated_data.get("pickup_point", instance.pickup_point)

        instance.full_name = validated_data.get("full_name", instance.full_name)
        email = validated_data.get("email", instance.email)
        instance.email = email.strip().lower() if email else None
        instance.pickup_point = pickup_new
        instance.save(update_fields=["full_name", "email", "pickup_point", "updated_at"])

        # Если ПВЗ изменился — переназначаем клиентский код
        if pickup_new.id != pickup_was:
            instance.assign_client_code(save=True)

        return instance


# -------------------------
#  Трекинг
# -------------------------
class TrackingEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingEvent
        fields = ("id", "status", "location", "timestamp")


class OrderSerializer(serializers.ModelSerializer):
    events = TrackingEventSerializer(many=True, read_only=True)
    last_status = serializers.CharField(read_only=True)
    next_status = serializers.SerializerMethodField()
    can_scan = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "tracking_number",
            "description",
            "created_at",
            "last_status",
            "next_status",
            "can_scan",
            "events",
        )

    def get_next_status(self, obj: Order):
        return obj.next_status

    def get_can_scan(self, obj: Order):
        return obj.can_scan()


# -------------------------
#  Сканер
# -------------------------
class OrderScanSerializer(serializers.Serializer):
    """Сериалайзер для POST /scan/"""
    tracking_number = serializers.CharField()
    location = serializers.CharField(required=False, allow_blank=True)

    # опционально — для удобства ответа
    order = OrderSerializer(read_only=True)
    created_event = TrackingEventSerializer(read_only=True)

    def create(self, validated_data):
        tn = validated_data["tracking_number"]
        location = validated_data.get("location", "")

        try:
            order, event = handle_scan(tn, location=location)
        except ValueError as e:
            # кулдаун/частые сканы
            raise ValidationError({"detail": str(e)})

        # отдаём полезный ответ
        self.instance = {
            "order": order,
            "created_event": event,
        }
        return self.instance

    def to_representation(self, instance):
        # красиво сериализуем order + event
        return {
            "order": OrderSerializer(instance["order"]).data,
            "created_event": (
                TrackingEventSerializer(instance["created_event"]).data
                if instance["created_event"] else None
            ),
        }
