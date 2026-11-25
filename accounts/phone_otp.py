"""Optional phone OTP at sign-up (PRD2 §4.4). Off unless PHONE_OTP_ENABLED=True.

The pending sign-up lives in the session (password already hashed); only a keyed hash of the code
is stored. In development the SMS "sends" by printing to the server log; plug a real SMS gateway
into send_sms() for production.
"""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings

logger = logging.getLogger("verdicto.sms")
SESSION_KEY = "pending_signup"
TTL_SECONDS = 600
MAX_ATTEMPTS = 5


def _hash(code, phone):
    return hmac.new(settings.SECRET_KEY.encode(), f"sms:{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def send_sms(phone, text):
    logger.warning("SMS to %s: %s", phone, text)  # development "gateway"


def start(request, data):
    code = f"{secrets.randbelow(10**6):06d}"
    request.session[SESSION_KEY] = {"data": data, "hash": _hash(code, data["phone"]),
                                    "expires": time.time() + TTL_SECONDS, "attempts": 0}
    send_sms(data["phone"], f"Your Verdicto code is {code}. It expires in 10 minutes.")


def check(request, code):
    """Returns (data, error). data is the sign-up data when the code is right."""
    pending = request.session.get(SESSION_KEY)
    if not pending or pending["expires"] < time.time():
        request.session.pop(SESSION_KEY, None)
        return None, "This code has expired. Please sign up again."
    if pending["attempts"] >= MAX_ATTEMPTS:
        request.session.pop(SESSION_KEY, None)
        return None, "Too many attempts. Please sign up again."
    pending["attempts"] += 1
    request.session[SESSION_KEY] = pending
    if not hmac.compare_digest(pending["hash"], _hash(code, pending["data"]["phone"])):
        return None, f"Wrong code. {MAX_ATTEMPTS - pending['attempts']} attempt(s) left."
    request.session.pop(SESSION_KEY, None)
    return pending["data"], None
