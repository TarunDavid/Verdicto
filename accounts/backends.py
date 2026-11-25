from django.contrib.auth.backends import ModelBackend

from .models import User
from .phone import normalize_phone


class PhoneBackend(ModelBackend):
    """Log in with phone number + password. (Django's ModelBackend still handles admin usernames.)"""

    def authenticate(self, request, username=None, password=None, **kwargs):
        phone = normalize_phone(username)
        if not phone or not password:
            return None
        user = User.objects.filter(phone=phone).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
