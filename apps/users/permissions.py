from rest_framework.permissions import BasePermission

class IsEmployee(BasePermission):
    """
    Допускает только авторизованных сотрудников/админов.
    """
    message = "Сканировать могут только авторизованные сотрудники."

    def has_permission(self, request, view):
        u = request.user
        return bool(
            u and u.is_authenticated and (getattr(u, "is_employee", False) or u.is_staff or u.is_superuser)
        )
