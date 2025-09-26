from django.db import models, transaction
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator
from django.utils import timezone
from django.db import IntegrityError, transaction
from django.conf import settings





class Order(models.Model):
    """Заказ (посылка), привязанный к клиенту."""

    TRACK_NUMBER_MAX_LENGTH = 32

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
        verbose_name="Клиент"
    )
    tracking_number = models.CharField(
        "Трек-номер",
        max_length=TRACK_NUMBER_MAX_LENGTH,
        unique=True,
        db_index=True
    )
    description = models.CharField(
        "Описание (опционально)",
        max_length=255,
        blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tracking_number} ({self.user.full_name})"

    @property
    def last_status(self):
        """Последний статус из TrackingEvent."""
        ev = self.events.order_by("-timestamp").first()
        return ev.status if ev else None


class TrackingEvent(models.Model):
    """История сканирований/статусов по заказу."""

    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Заказ"
    )
    status = models.CharField("Статус", max_length=255)
    location = models.CharField("Локация", max_length=255, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Событие отслеживания"
        verbose_name_plural = "События отслеживания"
        ordering = ["timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.status}"


# ===== Справочник складов в Китае =====
class WarehouseCN(models.Model):
    name = models.CharField("Название (произвольное)", max_length=120, blank=True)
    address_cn = models.CharField("Адрес (CN)", max_length=255)
    contact_name = models.CharField("Контакт (CN)", max_length=80, blank=True)
    contact_phone = models.CharField("Телефон (CN)", max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Склад в Китае"
        verbose_name_plural = "Склады в Китае"

    def __str__(self):
        return self.name or self.address_cn


# двузначный код: "01", "02", ...
DIG2 = RegexValidator(r"^\d{2}$", 'Требуется двузначный код, например "01".')

# ===== Пункты выдачи (ПВЗ) =====
class PickupPoint(models.Model):
    name_ru = models.CharField("Название (RU)", max_length=80)      # Бишкек, Ош, ...
    name_kg = models.CharField("Аталышы (KG)", max_length=80, blank=True)
    address = models.CharField("Адрес (локальный)", max_length=255, blank=True)
    code_label = models.CharField(  # что уходит в префикс клиентского кода
        "Метка для клиентского кода",
        max_length=80,
        help_text="Что попадёт в префикс кода, например «Бишкек» или «Ош»",
    )

    # вручную задаваемые коды
    region_code = models.CharField("Код региона", max_length=2, validators=[DIG2])
    branch_code = models.CharField("Код филиала", max_length=2, validators=[DIG2])

    default_cn_warehouse = models.ForeignKey(
        WarehouseCN,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pickup_points",
        verbose_name="Склад CN по умолчанию",
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Пункт выдачи"
        verbose_name_plural = "Пункты выдачи"
        indexes = [
            models.Index(fields=["region_code", "branch_code"]),
            models.Index(fields=["is_active"]),
        ]
        # ВАЖНО: НЕ ставим уникальность на (region_code, branch_code),
        # чтобы можно было иметь несколько ПВЗ в одном филиале.

    def __str__(self):
        return self.name_ru

    @property
    def code_pair(self) -> str:
        return f"{self.region_code}-{self.branch_code}"


# ===== Пользовательский менеджер =====
class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra_fields):
        if not phone:
            raise ValueError("Телефон обязателен")
        if not password:
            raise ValueError("Пароль обязателен")

        phone = phone.replace(" ", "")
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)

        # 🔹 Сначала генерируем client_code
        if not user.client_code:
            user.assign_client_code(save=False)

        # 🔹 Теперь сохраняем один раз — уже с client_code
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        # если ПВЗ не передали — выбираем/создаём дефолтный
        pp = extra_fields.get("pickup_point")
        if pp is None:
            pp = PickupPoint.objects.filter(is_active=True).first()
            if pp is None:
                pp = PickupPoint.objects.create(
                    name_ru="Админ",
                    name_kg="Админ",
                    address="",
                    code_label="Админ",
                    region_code="00",
                    branch_code="00",
                    is_active=True,
                )
            extra_fields["pickup_point"] = pp

        return self._create_user(phone, password, **extra_fields)


# ===== Счётчик LC по ПВЗ =====
class ClientCodeCounter(models.Model):
    pickup_point = models.OneToOneField(PickupPoint, on_delete=models.CASCADE, related_name="code_counter")
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["pickup_point"])]

    def __str__(self):
        return f"{self.pickup_point.name_ru} — {self.last_number}"


# ===== Пользователь =====
class User(AbstractBaseUser, PermissionsMixin):
    KYRGYZ_PHONE = RegexValidator(regex=r"^\+996\d{9}$", message="Формат: +996XXXXXXXXX")

    id = models.BigAutoField(primary_key=True)
    full_name = models.CharField("ФИО", max_length=150)
    phone = models.CharField("Телефон", max_length=13, unique=True, validators=[KYRGYZ_PHONE], db_index=True)
    email = models.EmailField("Email для восстановления", null=True, blank=True, unique=True)

    pickup_point = models.ForeignKey(
        PickupPoint, on_delete=models.PROTECT, related_name="users", verbose_name="ПВЗ"
    )

    rack = models.PositiveSmallIntegerField("Ряд", default=1)
    cell = models.PositiveSmallIntegerField("Ячейка", default=1)

    lc_number = models.CharField("Номер LC", max_length=20, blank=True)

    client_code = models.CharField(
        "Личный код",
        max_length=64,
        null=True,   # ← разрешаем NULL
        blank=True   # ← в формах можно оставить пустым
    )

    region_code = models.CharField("Код региона (ручной ввод)", max_length=10, blank=True)

    is_blocked = models.BooleanField("Заблокирован", default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        indexes = [models.Index(fields=["pickup_point"])]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"

    # -------- Представления --------
    @property
    def client_code_display(self) -> str:
        pp = self.pickup_point
        return f"{pp.code_label}-{self.region_code or pp.region_code}-{pp.branch_code}(LC-{self.lc_number})"

    def get_cn_warehouse(self):
        return self.pickup_point.default_cn_warehouse

    @property
    def cn_warehouse_address(self) -> str:
        wh = self.get_cn_warehouse()
        base = wh.address_cn if wh else ""
        contact = " ".join(
            filter(None, [getattr(wh, "contact_name", ""), getattr(wh, "contact_phone", "")])
        ).strip()
        tail = f"{self.rack:02d}-{self.cell:02d}(LC-{self.lc_number})"
        parts = [base, tail, contact]
        return " ".join(p for p in parts if p)

    # -------- Генерация client_code --------
    def assign_client_code(self, save=True):
        pp = self.pickup_point
        base_code = f"{pp.code_label}-{self.region_code or pp.region_code}-{pp.branch_code}"

        if not self.lc_number:
            counter, _ = ClientCodeCounter.objects.get_or_create(pickup_point=pp)

            while True:
                counter.last_number += 1
                candidate_lc = str(counter.last_number).zfill(4)
                candidate_code = f"{base_code}(LC-{candidate_lc})"

                if not User.objects.filter(client_code=candidate_code).exists():
                    self.lc_number = candidate_lc
                    self.client_code = candidate_code

                    try:
                        with transaction.atomic():
                            counter.save(update_fields=["last_number"])
                            if save:
                                self.save(update_fields=["client_code", "lc_number", "updated_at"])
                        break
                    except IntegrityError:
                        continue
        else:
            self.client_code = f"{base_code}(LC-{self.lc_number})"
            if save:
                self.save(update_fields=["client_code", "lc_number", "updated_at"])

        return self.client_code
