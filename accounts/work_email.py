"""Work-email verification by one-time code (SPEC §8 / PRD2 §4.2).

Only a keyed hash of the code is stored; codes expire after 10 minutes and allow 5 attempts.
"""
import hashlib
import hmac
import secrets
import socket
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import WorkEmailOTP

FREE_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "ymail.com", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "rediffmail.com", "proton.me", "protonmail.com",
    "icloud.com", "me.com", "aol.com", "zoho.com", "gmx.com", "mail.com", "yandex.com",
}
TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5


class WorkEmailError(Exception):
    pass


def _hash(code, email):
    return hmac.new(settings.SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def check_domain(email):
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in FREE_PROVIDERS:
        raise WorkEmailError("Use your work or institution email, not a free email provider.")
    if not has_mail_server(domain):
        raise WorkEmailError("That domain can't receive email (no mail server found).")
    return domain


def has_mail_server(domain):
    """True if the domain has MX records (or, per RFC 5321, an address record as fallback)."""
    import dns.exception
    import dns.resolver
    try:
        return bool(dns.resolver.resolve(domain, "MX", lifetime=4))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return False
    except (dns.resolver.NoAnswer, dns.exception.Timeout):
        try:
            socket.getaddrinfo(domain, None)
            return True
        except socket.gaierror:
            return False


def send_code(user, email):
    email = email.strip().lower()
    check_domain(email)
    code = f"{secrets.randbelow(10**6):06d}"
    WorkEmailOTP.objects.filter(user=user, used=False).update(used=True)  # one live code at a time
    WorkEmailOTP.objects.create(user=user, email=email, code_hash=_hash(code, email),
                                expires_at=timezone.now() + TTL)
    send_mail("Your Verdicto verification code",
              f"Your code is {code}. It expires in 10 minutes. If you didn't ask for it, ignore this email.",
              settings.DEFAULT_FROM_EMAIL, [email])
    return email


def verify_code(user, code):
    otp = WorkEmailOTP.objects.filter(user=user, used=False).first()
    if otp is None or otp.expires_at < timezone.now():
        raise WorkEmailError("This code has expired. Request a new one.")
    if otp.attempts >= MAX_ATTEMPTS:
        raise WorkEmailError("Too many attempts. Request a new code.")
    otp.attempts += 1
    otp.save(update_fields=["attempts"])
    if not hmac.compare_digest(otp.code_hash, _hash(code, otp.email)):
        left = MAX_ATTEMPTS - otp.attempts
        raise WorkEmailError(f"Wrong code. {left} attempt{'s' if left != 1 else ''} left.")
    otp.used = True
    otp.save(update_fields=["used"])
    profile = user.verifier_profile
    profile.work_email = otp.email
    profile.work_email_verified = True
    profile.org_domain = otp.email.rsplit("@", 1)[-1]
    profile.save(update_fields=["work_email", "work_email_verified", "org_domain"])
    return user
