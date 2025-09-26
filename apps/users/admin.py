# admin.py
from django.contrib import admin
from django import forms
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.db.models import Count

from .models import User, PickupPoint, WarehouseCN, ClientCodeCounter, Order, TrackingEvent


# ---------- Forms для корректной работы UserAdmin ----------

class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Подтверждение пароля", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("phone", "full_name", "pickup_point", "email")

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Пароли не совпадают.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            # гарантируем клиентский код при создании через админку
            if not user.client_code:
                user.assign_client_code()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        help_text="Пароли не хранятся в открытом виде."
    )

    class Meta:
        model = User
        fields = (
            "phone",
            "full_name",
            "email",
            "pickup_point",
            "lc_number",      # 🔹 ручное поле
            "region_code",    # 🔹 ручное поле
            "password",
            "is_active",
            "is_blocked",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )

# ---------- Admin для User ----------

@admin.action(description="Сгенерировать клиентские коды (только у кого пусто)")
def generate_missing_codes(modeladmin, request, queryset):
    created = 0
    for u in queryset:
        if not u.client_code:
            u.assign_client_code()
            created += 1
    modeladmin.message_user(request, f"Сгенерировано кодов: {created}")

@admin.action(description="Перегенерировать клиентские коды (ОСТОРОЖНО: изменит текущие коды)")
def regenerate_codes(modeladmin, request, queryset):
    for u in queryset:
        # assign_client_code: если lc_number уже есть — только пересоберёт client_code с новым префиксом ПВЗ
        u.assign_client_code()
    modeladmin.message_user(request, f"Коды пересчитаны для {queryset.count()} пользователей")

@admin.action(description="Заблокировать выбранных пользователей")
def block_users(modeladmin, request, queryset):
    updated = queryset.update(is_blocked=True)
    modeladmin.message_user(request, f"Заблокировано: {updated}")

@admin.action(description="Разблокировать выбранных пользователей")
def unblock_users(modeladmin, request, queryset):
    updated = queryset.update(is_blocked=False)
    modeladmin.message_user(request, f"Разблокировано: {updated}")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    model = User

    list_display = (
        "id",
        "full_name",
        "phone",
        "pickup_point",
        "client_code",
        "client_code_display_admin",
        "is_active",
        "is_blocked",
        "is_staff",
        "date_joined",
    )
    list_display_links = ("id", "full_name", "phone")
    list_filter = ("pickup_point", "is_blocked", "is_active", "is_staff", "is_superuser", "date_joined")
    search_fields = ("full_name", "phone", "email", "client_code")
    ordering = ("-id",)

    readonly_fields = ("client_code", "date_joined", "updated_at",
                       "client_code_display_admin", "cn_warehouse_address_admin")

    fieldsets = (
        ("Учетные данные", {"fields": ("phone", "password")}),
        ("Личные данные", {"fields": ("full_name", "email", "pickup_point")}),
        ("Клиентский код", {
            "fields": (
                "lc_number", "region_code", "client_code",
                "client_code_display_admin", "rack", "cell",
                "cn_warehouse_address_admin"
            )
        }),
        ("Права и статусы", {"fields": ("is_active", "is_blocked", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Служебные", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    autocomplete_fields = ("pickup_point",)

    add_fieldsets = (
        ("Создание пользователя", {
            "classes": ("wide",),
            "fields": ("phone", "full_name", "pickup_point", "email", "password1", "password2"),
        }),
    )

    actions = [generate_missing_codes, regenerate_codes, block_users, unblock_users]

    # удобные «виртуальные» поля для отображения
    @admin.display(description="Код (с префиксом)")
    def client_code_display_admin(self, obj: User):
        return obj.client_code_display

    @admin.display(description="Адрес склада в Китае")
    def cn_warehouse_address_admin(self, obj: User):
        return obj.cn_warehouse_address


# ---------- Admin для справочников ----------

@admin.register(WarehouseCN)
class WarehouseCNAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "address_cn", "contact_name", "contact_phone", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "address_cn", "contact_name", "contact_phone")
    ordering = ("name",)

class UserInline(admin.TabularInline):
    model = User
    fields = ("full_name", "phone", "lc_number", "region_code", "client_code")
    extra = 0
    readonly_fields = ("client_code",)


@admin.register(PickupPoint)
class PickupPointAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name_ru",
        "code_label",
        "region_code",
        "branch_code",
        "lc_prefix",                # 🔹 показываем префикс LC
        "default_cn_warehouse",
        "is_active",
        "users_count",
        "updated_at",
    )
    list_filter = ("is_active", "default_cn_warehouse", "lc_prefix")  # 🔹 фильтр по префиксу
    search_fields = ("name_ru", "name_kg", "address", "code_label", "lc_prefix")  # 🔹 поиск по префиксу
    autocomplete_fields = ("default_cn_warehouse",)
    ordering = ("name_ru",)

    inlines = [UserInline]

    # Можно явно указать поля редактирования, если хотите:
    # fields = ("name_ru", "name_kg", "address", "code_label", "region_code", "branch_code", "lc_prefix",
    #           "default_cn_warehouse", "is_active", "created_at", "updated_at")
    # readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_users_count=Count("users"))

    @admin.display(description="Клиентов")
    def users_count(self, obj):
        return getattr(obj, "_users_count", obj.users.count())


@admin.register(ClientCodeCounter)
class ClientCodeCounterAdmin(admin.ModelAdmin):
    list_display = ("pickup_point", "last_number", "updated_at")
    search_fields = ("pickup_point__name_ru", "pickup_point__lc_prefix")
    readonly_fields = ("updated_at",)
    autocomplete_fields = ("pickup_point",)


class TrackingEventInline(admin.TabularInline):
    model = TrackingEvent
    extra = 0
    readonly_fields = ("timestamp",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "tracking_number", "user", "last_status", "created_at")
    search_fields = ("tracking_number", "user__full_name", "user__phone")
    ordering = ("-created_at",)
    inlines = [TrackingEventInline]


@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "status", "location", "timestamp")
    list_filter = ("status", "timestamp")
    search_fields = ("status", "order__tracking_number")
    ordering = ("-timestamp",)
