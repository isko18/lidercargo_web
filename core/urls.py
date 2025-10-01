from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from apps.users.views import index  # index должен отдавать React index.html

schema_view = get_schema_view(
    openapi.Info(
        title="LiderCargo API",
        default_version='v1',
        description="API для проекта LiderCargo",
        terms_of_service="#",
        contact=openapi.Contact(email="support@NurCRM.com"),
        license=openapi.License(name="LiderCargo License"),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

# ---- API только тут ----
api_urlpatterns = [
    path('api/users/', include('apps.users.urls')),
    # Добавляй другие api/* сюда
]

urlpatterns = [
    path('admin/', admin.site.urls),

    # Вся API под /v1/...
    path('v1/', include(api_urlpatterns)),

    # Документация
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),

    # Главная SPA страница
    path('', index, name='index'),

    # Catch-all для SPA: всё, что НЕ начинается с перечисленных префиксов, отдаём index
    re_path(r'^(?!v1/|admin/|swagger/|redoc/|static/|media/).*$',
            index, name='spa-catchall'),
]

# ---- Медиа/статика в DEV ----
# Для MEDIA нужно явно добавлять всегда в dev:
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Для статики в dev обычно не нужно, если стоит django.contrib.staticfiles.
    # Но если хочешь раздавать из STATIC_ROOT (после collectstatic), можно так:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
