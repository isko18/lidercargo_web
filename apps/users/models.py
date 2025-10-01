from django.db import models, transaction, IntegrityError
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator
from django.utils import timezone
from django.conf import settings
from datetime import timedelta


# =========================
#        Заказ
# =========================
class Order(models.Model):
    """Заказ (посылка), привязанный к клиенту. Продвигается по сканам."""

    TRACK_NUMBER_MAX_LENGTH = 32

    # Шаги обработки по порядку (4 ручных скана)
    STATUS_FLOW = [
        "Товар поступил на склад в Китае",
        "Товар отправлен со склада",
        "Прибыл в пункт выдачи",
        "Получен",
    ]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,           # можно без клиента
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

    # ---------- Вспомогательные ----------
    @property
    def last_event(self):
        return self.events.order_by("-timestamp").first()

    @property
    def last_status(self):
        ev = self.last_event
        return ev.status if ev else None

    # >>> НОВОЕ: считаем только РУЧНЫЕ сканы <<<
    @property
    def last_manual_event(self):
        """Последний РУЧНОЙ скан (actor не NULL)."""
        return self.events.filter(actor__isnull=False).order_by("-timestamp").first()

    @property
    def manual_scan_count(self) -> int:
        """Сколько ручных сканов уже сделано (для вычисления шага 1..4)."""
        return self.events.filter(actor__isnull=False).count()

    @property
    def next_status(self):
        """
        СТРОГИЙ порядок 1→2→3→4.
        Прогресс считаем по наличию РУЧНЫХ статусов из STATUS_FLOW подряд с начала.
        Шаг 3 у нас форматируется, поэтому сверяем startswith.
        Автостатусы (actor IS NULL) игнорируем.
        """
        manual_texts = list(
            self.events.filter(actor__isnull=False).values_list("status", flat=True)
        )

        def matches(flow_text: str, actual: str) -> bool:
            # шаг 3 логирован в развернутом виде — сравнение по префиксу
            if flow_text == "Прибыл в пункт выдачи":
                return actual.startswith("Товар прибыл в пункт выдачи")
            return actual == flow_text

        # последовательно проверяем шаги с начала
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

    # ---------- Подстановки для статусов ----------
    def _template_context(self, actor=None):
        """
        Контекст подстановок. Назначение берём из ПВЗ клиента (owner),
        если его нет — из ПВЗ сотрудника, который сканирует.
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
            "pvz_name": dest_label or dest_city,  # "LIDER CARGO Ош" / "Бишкек"
            "pvz_code": dest_code,                # "02-01"
            "pvz_address": dest_addr,             # адрес ПВЗ
            "track": self.tracking_number,
            "dest_city": dest_city,               # для авто-текстов
            "dest_label": dest_label,
            "dest_code": dest_code,
        }

    def _render_text(self, template_text: str, actor=None) -> str:
        """Безопасная подстановка плейсхолдеров {pvz_name}, {pvz_code}, {track}, {pvz_address}, {dest_city} ..."""
        try:
            return template_text.format(**self._template_context(actor=actor))
        except Exception:
            return template_text

    def apply_scan(self, location: str = "", actor=None):
        """
        Добавить следующий статус по скану. Возвращает созданный TrackingEvent или None, если уже всё пройдено.
        Если actor передан — требуем, чтобы он был сотрудником/админом.
        """
        if actor is not None:
            if not (
                getattr(actor, "is_employee", False)
                or getattr(actor, "is_staff", False)
                or getattr(actor, "is_superuser", False)
            ):
                raise PermissionError("Сканировать могут только сотрудники.")

        # Проверка кулдауна по РУЧНОМУ событию
        if not self.can_scan():
            cooldown_min = getattr(settings, "SCAN_COOLDOWN_MINUTES", 5)
            raise ValueError(f"Скан возможен только через {cooldown_min} минут")

        # Определяем следующий статус
        nxt = self.next_status
        if not nxt:
            return None  # уже «Получен»

        # Формируем текст: для шага 3 — как на макете
        status_text = nxt
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
            actor=actor,  # фиксируем, кто сканировал (если передан)
        )

        # «Досыпать» автостатусы, если их время уже пришло
        self.create_due_auto_events(base_event=ev, actor=actor)

        return ev

    # ---------- Автоматические статусы по времени ----------
    PHASE_BY_STATUS = {
        "Товар поступил на склад в Китае": "AFTER_SCAN_1",
        "Товар отправлен со склада": "AFTER_SCAN_2",
        "Прибыл в пункт выдачи": "AFTER_SCAN_3",
        "Получен": "AFTER_SCAN_4",
    }

    def create_due_auto_events(self, base_event: "TrackingEvent", actor=None):
        """
        «Ленивая» автодозагрузка: создаёт только те авто-события из шаблонов,
        у которых (base_event.timestamp + offset) <= now и которых ещё нет у заказа.
        """
        phase = self.PHASE_BY_STATUS.get(base_event.status)
        # если статус шага 3 был отформатирован, он начинается с "Товар прибыл в пункт выдачи"
        if not phase and base_event.status.startswith("Товар прибыл в пункт выдачи"):
            phase = "AFTER_SCAN_3"
        if not phase:
            return

        templates = AutoStatusTemplate.objects.filter(phase=phase, is_active=True).order_by("order_index")

        now = timezone.now()
        exists_cache = set(self.events.values_list("status", flat=True))  # чтобы меньше бить БД

        for tpl in templates:
            due_ts = base_event.timestamp + timedelta(minutes=tpl.offset_minutes)
            if due_ts <= now:
                rendered = self._render_text(tpl.text, actor=actor)
                if rendered not in exists_cache:
                    TrackingEvent.objects.create(
                        order=self,
                        status=rendered,
                        location="(авто)",
                        timestamp=due_ts,  # важно: историческая отметка
                    )
                    exists_cache.add(rendered)

# =========================
#     Событие трекинга
# =========================
class TrackingEvent(models.Model):
    """История сканирований/статусов по заказу."""

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

    # НОВОЕ: кто сделал скан (сотрудник/админ). Для автособытий остаётся NULL.
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


# двузначный код: "01", "02", ...
DIG2 = RegexValidator(r"^\d{2}$", 'Требуется двузначный код, например "01".')


# =========================
#        ПВЗ
# =========================
class PickupPoint(models.Model):
    name_ru = models.CharField("Название (RU)", max_length=80)  # Бишкек, Ош, ...
    name_kg = models.CharField("Аталышы (KG)", max_length=80, blank=True)
    address = models.CharField("Адрес (локальный)", max_length=255, blank=True)

    code_label = models.CharField(
        "Метка для клиентского кода",
        max_length=80,
        help_text="Что попадёт в префикс кода, например «Бишкек» или «Ош»",
    )

    region_code = models.CharField("Код региона", max_length=2, validators=[DIG2])
    branch_code = models.CharField("Код филиала", max_length=2, validators=[DIG2])

    # Префикс для LC на уровне ПВЗ (например: "OS", "BS" и т.п.)
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

        phone = phone.replace(" ", "")
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)

        # Сначала генерируем client_code (если его нет)
        if not user.client_code:
            user.assign_client_code(save=False)

        # Сохраняем один раз — уже с client_code
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        # по умолчанию — клиент, не сотрудник
        extra_fields.setdefault("is_employee", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # суперюзеру включим флаг сотрудника — чтобы мог сканировать
        extra_fields.setdefault("is_employee", True)

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

    region_code = models.CharField("Код региона (ручной ввод)", max_length=10, blank=True)

    # НОВОЕ: флаг сотрудника (для права сканировать)
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

    # -------- Представления --------
    @property
    def client_code_display(self) -> str:
        pp = self.pickup_point
        return (
            f"{pp.code_label}-"
            f"{self.region_code or pp.region_code}-"
            f"{pp.branch_code}"
            f"({pp.lc_prefix}-{self.lc_number})"
        )

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

    # -------- Генерация client_code --------
    def assign_client_code(self, save=True):
        pp = self.pickup_point
        base_code = f"{pp.code_label}-{self.region_code or pp.region_code}-{pp.branch_code}"

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
                        # гонка — пробуем следующий номер
                        continue
        else:
            self.client_code = f"{base_code}({pp.lc_prefix}-{self.lc_number})"
            if save:
                self.save(update_fields=["client_code", "lc_number", "updated_at"])

        return self.client_code


# =========================
#   Авто-статусы (шаблоны)
# =========================
class AutoStatusTemplate(models.Model):
    """
    Шаблоны автособытий, которые должны возникать ПОСЛЕ какого-то ручного скана.
    Пример phase: "AFTER_SCAN_1", "AFTER_SCAN_2", "AFTER_SCAN_3", "AFTER_SCAN_4".
    text — готовый текст статуса (можно с плейсхолдерами, если будете подставлять при создании).
    offset_minutes — через сколько минут после базового события надо добавить этот статус.
    """
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
    Главная точка для сканера.
    - Если заказа нет — создаём и добавляем ШАГ 1.
    - Если заказ есть — добавляем следующий шаг из пайплайна.
    - Если уже «Получен» — вернёт (order, None).
    - Если не прошёл кулдаун — вернёт (order, None) или кинет ValueError (если raise_on_cooldown=True).
    - Сканировать могут только авторизованные сотрудники/админы (но старый код без user мы не ломаем).
    """
    tn = (tracking_number or "").strip()

    if user is not None:
        if not (
            getattr(user, "is_authenticated", False)
            and (getattr(user, "is_employee", False) or getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
        ):
            raise PermissionError("Сканировать могут только авторизованные сотрудники.")

    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(tracking_number=tn)
            created = False
        except Order.DoesNotExist:
            # ВАЖНО: не привязываем сотрудника как владельца заказа!
            order = Order.objects.create(tracking_number=tn, description=description)
            created = True

        if not created and not order.can_scan():
            if raise_on_cooldown:
                cooldown_min = getattr(settings, "SCAN_COOLDOWN_MINUTES", 5)
                raise ValueError(f"Повторный скан того же трека возможен через {cooldown_min} минут.")
            return order, None

        event = order.apply_scan(location=location or "", actor=user)
        return order, event