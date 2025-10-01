from django.test import TestCase

# Create your tests here.
# ВАЖНО: используй своё имя приложения!
from apps.users.models import (
    AutoStatusTemplate, PickupPoint, User, Order, TrackingEvent, handle_scan
)
from django.utils import timezone
from datetime import timedelta



pp, _ = PickupPoint.objects.get_or_create(
    name_ru="Бишкек",
    defaults=dict(
        name_kg="Бишкек",
        address="г. Бишкек, ул. Пример, 1",
        code_label="LIDER CARGO Бишкек",
        region_code="01",
        branch_code="01",
        lc_prefix="BS",
        is_active=True,
    )
)
pp.id, pp.name_ru, pp.code_pair
