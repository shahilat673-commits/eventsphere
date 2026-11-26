from functools import wraps
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Allow only users having one of the given roles (role-based access control)."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.role not in roles:
                raise PermissionDenied
            if request.user.role == 'ORGANISER' and not request.user.is_approved:
                messages.error(request, 'Your organiser account is awaiting admin approval.')
                raise PermissionDenied
            return view(request, *a, **kw)
        return wrapper
    return deco
