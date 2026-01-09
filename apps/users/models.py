from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models, transaction, IntegrityError
from django.utils import timezone


# =========================
#        Настройки
# =========================
class Base(models.Model):
    logo = models.ImageField(verbose_name="лого", upload_to="logo/", null=True, blank=True)
    banner = models.FileField(verbose_name="Баннер", upload_to="banner/", null=True, blank=True)

    class Meta:
        verbose_name = "Настройка"
        verbose_name_plural = "Настройки"


# =========================
#        Заказ
# =========================
class Order(models.Model):
    """
    Заказ (посылка), привязанный к клиенту.

    - 2 ручных скана:
      1) "Товар поступил на склад в Китае [LIDER CARGO]"
         -> после него запускается авто-цепочка статусов (AFTER_SCAN_1)
      2) "Товар прибыл в пункт выдачи [...]"
         -> после него идут уведомления и через 14 дней авто-статус "Получен" (AFTER_SCAN_2)
    """

    TRACK_NUMBER_MAX_LENGTH = 32

    STATUS_FLOW = [
        "Товар поступил на склад в Китае",
        "Прибыл в пункт выдачи",
    ]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Клиент",
    )
    tracking_number = models.CharField(
        "Трек-номер",
        max_length=TRACK_NUMBER_MAX_LENGTH,
        unique=True,
        db_index=True,
    )
    description = models.CharField("Описание (опционально)", max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tracking_number} ({getattr(self.user, 'full_name', 'без клиента')})"

    @property
    def last_event(self):
        return self.events.order_by("-timestamp").first()

    @property
    def last_status(self):
        ev = self.last_event
        return ev.status if ev else None

    @property
    def last_manual_event(self):
        """Последний РУЧНОЙ скан (actor не NULL)."""
        return self.events.filter(actor__isnull=False).order_by("-timestamp").first()

    @property
    def manual_scan_count(self) -> int:
        """Сколько ручных сканов уже сделано."""
        return self.events.filter(actor__isnull=False).count()

    @property
    def next_status(self):
        """
        СТРОГИЙ порядок: 1 → 2 (две ручные точки).
        Прогресс считаем по наличию РУЧНЫХ статусов из STATUS_FLOW подряд с начала.
        Скан #2 у нас форматированный ("Товар прибыл в пункт выдачи ..."), поэтому сверяем startswith.
        """
        manual_texts = list(self.events.filter(actor__isnull=False).values_list("status", flat=True))

        def matches(flow_text: str, actual: str) -> bool:
            if flow_text == "Прибыл в пункт выдачи":
                return actual.startswith("Товар прибыл в пункт выдачи")
            return actual == flow_text

        progress = -1
        for idx, flow_text in enumerate(self.STATUS_FLOW):
            if any(matches(flow_text, t) for t in manual_texts):
                progress = idx
            else:
                break

        nxt_idx = progress + 1
        if nxt_idx < len(self.STATUS_FLOW):
            return self.STATUS_FLOW[nxt_idx]
        return None

    def can_scan(self) -> bool:
        """Кулдаун считаем по последнему РУЧНОМУ скану."""
        cooldown_min = getattr(settings, "SCAN_COOLDOWN_MINUTES", 5)
        last = self.last_manual_event
        if not last:
            return True
        return timezone.now() - last.timestamp >= timedelta(minutes=cooldown_min)

    def _template_context(self, actor=None):
        """
        Контекст подстановок.
        Назначение берём из ПВЗ клиента (owner), если его нет — из ПВЗ сотрудника, который сканирует.
        """
        pp = None
        if getattr(self, "user", None) and getattr(self.user, "pickup_point", None):
            pp = self.user.pickup_point
        elif actor and getattr(actor, "pickup_point", None):
            pp = actor.pickup_point

        dest_city = getattr(pp, "name_ru", "") if pp else ""
        dest_code = f"{getattr(pp, 'region_code', '')}-{getattr(pp, 'branch_code', '')}" if pp else ""
        dest_addr = getattr(pp, "address", "") if pp else ""
        dest_label = getattr(pp, "code_label", "") if pp else ""

        return {
            "pvz_name": dest_label or dest_city,
            "pvz_code": dest_code,
            "pvz_address": dest_addr,
            "track": self.tracking_number,
            "dest_city": dest_city,
            "dest_label": dest_label,
            "dest_code": dest_code,
        }

    def _render_text(self, template_text: str, actor=None) -> str:
        try:
            return template_text.format(**self._template_context(actor=actor))
        except Exception:
            return template_text

    PHASE_BY_STATUS = {
        "Товар поступил на склад в Китае [LIDER CARGO]": "AFTER_SCAN_1",
        "Товар поступил на склад в Китае": "AFTER_SCAN_1",
        # "Прибыл в пункт выдачи": "AFTER_SCAN_2",  # этот ключ фактически не используется (у тебя форматированный текст)
    }

    def apply_scan(self, location: str = "", actor=None):
        if actor is not None:
            if not (
                getattr(actor, "is_employee", False)
                or getattr(actor, "is_staff", False)
                or getattr(actor, "is_superuser", False)
            ):
                raise PermissionError("Сканировать могут только сотрудники.")

        if not self.can_scan():
            cooldown_min = getattr(settings, "SCAN_COOLDOWN_MINUTES", 5)
            raise ValueError(f"Скан возможен только через {cooldown_min} минут")

        nxt = self.next_status
        if not nxt:
            return None

        status_text = nxt
        if nxt == "Товар поступил на склад в Китае":
            status_text = "Товар поступил на склад в Китае [LIDER CARGO]"

        if nxt == "Прибыл в пункт выдачи":
            status_text = self._render_text(
                "Товар прибыл в пункт выдачи "
                "[{pvz_name} {pvz_code}, трек-номер: {track}, адрес: {pvz_address}]",
                actor=actor,
            )

        ev = TrackingEvent.objects.create(
            order=self,
            status=status_text,
            location=location or "",
            actor=actor,
        )

        self.create_due_auto_events(base_event=ev, actor=actor)
        return ev

    def create_due_auto_events(self, base_event: "TrackingEvent", actor=None):
        phase = self.PHASE_BY_STATUS.get(base_event.status)

        if not phase and base_event.status.startswith("Товар прибыл в пункт выдачи"):
            phase = "AFTER_SCAN_2"

        if not phase:
            return

        templates = AutoStatusTemplate.objects.filter(phase=phase, is_active=True).order_by("order_index")

        now = timezone.now()
        exists_cache = set(self.events.values_list("status", flat=True))

        for tpl in templates:
            due_ts = base_event.timestamp + timedelta(minutes=tpl.offset_minutes)
            if due_ts <= now:
                rendered = self._render_text(tpl.text, actor=actor)
                if rendered not in exists_cache:
                    TrackingEvent.objects.create(
                        order=self,
                        status=rendered,
                        location="(авто)",
                        timestamp=due_ts,
                    )
                    exists_cache.add(rendered)


# =========================
#     Событие трекинга
# =========================
class TrackingEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Заказ",
    )
    status = models.CharField("Статус", max_length=255)
    location = models.CharField("Локация", max_length=255, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scans",
        verbose_name="Сотрудник",
    )

    class Meta:
        verbose_name = "Событие отслеживания"
        verbose_name_plural = "События отслеживания"
        ordering = ["timestamp"]

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.status}"


# =========================
#   Авто-статусы (шаблоны)
# =========================
class AutoStatusTemplate(models.Model):
    phase = models.CharField("Фаза после скана", max_length=20)
    order_index = models.PositiveSmallIntegerField(default=0)
    text = models.CharField("Текст статуса", max_length=255)
    offset_minutes = models.PositiveIntegerField("Смещение (минуты)", default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["phase", "order_index"]
        indexes = [models.Index(fields=["phase", "is_active"])]

    def __str__(self):
        return f"{self.phase} #{self.order_index}: +{self.offset_minutes}m — {self.text[:40]}..."


# =========================
#   Справочник складов CN
# =========================
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


# валидаторы
DIG2 = RegexValidator(r"^\d{2}$", 'Требуется двузначный код, например "01".')
DIG_1_4 = RegexValidator(r"^\d{1,4}$", 'Код филиала: 1–4 цифры, например "155" или "0155".')


# =========================
#        ПВЗ
# =========================
class PickupPoint(models.Model):
    name_ru = models.CharField("Название (RU)", max_length=80)
    name_kg = models.CharField("Аталышы (KG)", max_length=80, blank=True)
    address = models.CharField("Адрес (локальный)", max_length=255, blank=True)

    code_label = models.CharField(
        "Метка для клиентского кода",
        max_length=80,
        help_text="Можно оставить для админки/вывески (в личный код больше не входит).",
    )

    region_code = models.CharField("Код региона", max_length=2, validators=[DIG2])
    branch_code = models.CharField("Код филиала", max_length=4, validators=[DIG_1_4])

    lc_prefix = models.CharField(
        "Префикс LC для ПВЗ",
        max_length=10,
        default="LC",
        help_text='Например: "OS" для Оша, "BS" для Бишкека',
    )

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

    def __str__(self):
        return self.name_ru

    @property
    def code_pair(self) -> str:
        return f"{self.region_code}-{self.branch_code}"


# =========================
#    Пользовательский менеджер
# =========================
class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra_fields):
        if not phone:
            raise ValueError("Телефон обязателен")
        if not password:
            raise ValueError("Пароль обязателен")

        phone = (phone or "").replace(" ", "").strip()
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)

        # генерим lc/client_code до сохранения — это ОК, потому что формат не зависит от user.id
        if not user.client_code:
            user.assign_client_code(save=False)

        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_employee", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_employee", True)

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
                    branch_code="0",
                    lc_prefix="ADM",
                    is_active=True,
                )
            extra_fields["pickup_point"] = pp

        return self._create_user(phone, password, **extra_fields)


# =========================
#   Счётчик LC по ПВЗ
# =========================
class ClientCodeCounter(models.Model):
    pickup_point = models.OneToOneField(
        PickupPoint, on_delete=models.CASCADE, related_name="code_counter"
    )
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["pickup_point"])]

    def __str__(self):
        return f"{self.pickup_point.name_ru} — {self.last_number}"


# =========================
#         Пользователь
# =========================
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
        null=True,
        blank=True,
    )

    # ✅ УДАЛЕНО: region_code (ручной ввод)
    # region_code = models.CharField("Код региона (ручной ввод)", max_length=10, blank=True)

    is_employee = models.BooleanField("Сотрудник", default=False)

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

    @staticmethod
    def _branch_left(branch_code: str) -> str:
        s = (branch_code or "").strip()
        if not s:
            return "0"
        # "0155" -> "155"
        s2 = s.lstrip("0")
        return s2 if s2 else "0"

    @property
    def client_code_display(self) -> str:
        pp = self.pickup_point
        left = self._branch_left(pp.branch_code)  # ✅ 155
        return f"{left}({pp.lc_prefix}-{self.lc_number})"

    def get_cn_warehouse(self):
        return self.pickup_point.default_cn_warehouse

    @property
    def cn_warehouse_address(self) -> str:
        wh = self.get_cn_warehouse()
        base = wh.address_cn if wh else ""
        contact = " ".join(
            filter(None, [getattr(wh, "contact_name", ""), getattr(wh, "contact_phone", "")])
        ).strip()
        tail = f"{self.rack:02d}-{self.cell:02d}({self.pickup_point.lc_prefix}-{self.lc_number})"
        parts = [base, tail, contact]
        return " ".join(p for p in parts if p)

    def assign_client_code(self, save=True):
        """
        ✅ Новый формат личного кода:
        155(BS-0241)
        где 155 = branch_code без ведущих нулей.
        """
        pp = self.pickup_point
        left = self._branch_left(pp.branch_code)
        base_code = left

        if not self.lc_number:
            counter, _ = ClientCodeCounter.objects.get_or_create(pickup_point=pp)

            while True:
                counter.last_number += 1
                candidate_lc = str(counter.last_number).zfill(4)
                candidate_code = f"{base_code}({pp.lc_prefix}-{candidate_lc})"

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
            self.client_code = f"{base_code}({pp.lc_prefix}-{self.lc_number})"
            if save:
                self.save(update_fields=["client_code", "lc_number", "updated_at"])

        return self.client_code


# =========================
#   Утилита для сканера (атомарно)
# =========================
def handle_scan(
    tracking_number: str,
    *,
    location: str | None = None,
    user=None,
    description: str = "",
    raise_on_cooldown: bool = False
):
    """
    - Если заказа нет — создаём и добавляем СКАН #1.
    - Если заказ есть — добавляем следующий ручной шаг (СКАН #2).
    - Если оба ручных уже пройдены — вернёт (order, None).
    - Кулдаун — по последнему ручному скану.
    """
    tn = "".join((tracking_number or "").split()).strip().upper()

    if user is not None:
        if not (
            getattr(user, "is_authenticated", False)
            and (
                getattr(user, "is_employee", False)
                or getattr(user, "is_staff", False)
                or getattr(user, "is_superuser", False)
            )
        ):
            raise PermissionError("Сканировать могут только авторизованные сотрудники.")

    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(tracking_number=tn)
            created = False
        except Order.DoesNotExist:
            order = Order.objects.create(tracking_number=tn, description=description)
            created = True

        if not created and not order.can_scan():
            if raise_on_cooldown:
                cooldown_min = getattr(settings, "SCAN_COOLDOWN_MINUTES", 5)
                raise ValueError(f"Повторный скан того же трека возможен через {cooldown_min} минут.")
            return order, None

        event = order.apply_scan(location=location or "", actor=user)
        return order, event
