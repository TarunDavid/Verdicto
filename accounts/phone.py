import re

PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(value):
    """'+91 98450 12345', '098450-12345', '9845012345' -> '+919845012345'. Returns '' if invalid."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return f"+91{digits}" if PHONE_RE.match(digits) else ""
