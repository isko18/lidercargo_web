from django.contrib import admin
from .models import User, PickupPoint, WarehouseCN, ClientCodeCounter, Order, TrackingEvent, AutoStatusTemplate

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display  = ("full_name", "phone", "is_employee", "pickup_point", "is_active")
    list_filter   = ("is_employee", "pickup_point", "is_active", "is_staff", "is_superuser")
    search_fields = ("full_name", "phone", "email")
    fieldsets = (
        (None, {"fields": ("full_name", "phone", "email", "password")}),
        ("Филиал/роль", {"fields": ("pickup_point", "is_employee", "is_active", "is_staff", "is_superuser")}),
        ("LC", {"fields": ("lc_number", "client_code", "region_code", "rack", "cell")}),
        ("Права", {"fields": ("groups", "user_permissions")}),
        ("Служебное", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    readonly_fields = ("date_joined", "updated_at")

@admin.register(PickupPoint)
class PickupPointAdmin(admin.ModelAdmin):
    list_display = ("name_ru", "code_pair", "lc_prefix", "is_active")
    list_filter  = ("is_active",)
    search_fields= ("name_ru", "region_code", "branch_code")

@admin.register(WarehouseCN)
class WarehouseCNAdmin(admin.ModelAdmin):
    list_display = ("name", "address_cn", "is_active")
    list_filter  = ("is_active",)
    search_fields= ("name", "address_cn")

@admin.register(ClientCodeCounter)
class ClientCodeCounterAdmin(admin.ModelAdmin):
    list_display = ("pickup_point", "last_number", "updated_at")
    search_fields= ("pickup_point__name_ru",)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display  = ("tracking_number", "user", "created_at", "last_status_admin")
    search_fields = ("tracking_number", "user__full_name", "user__phone")
    autocomplete_fields = ("user",)

    def last_status_admin(self, obj):
        return obj.last_status
    last_status_admin.short_description = "Последний статус"

@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    list_display  = ("order", "status", "location", "timestamp", "actor")
    list_filter   = ("status", "timestamp")
    search_fields = ("order__tracking_number", "status", "actor__full_name")

@admin.register(AutoStatusTemplate)
class AutoStatusTemplateAdmin(admin.ModelAdmin):
    list_display  = ("phase", "order_index", "text", "offset_minutes", "is_active")
    list_filter   = ("phase", "is_active")
    search_fields = ("text",)
    ordering      = ("phase", "order_index")
