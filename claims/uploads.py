"""Screenshot uploads: check the real image type with Pillow, cap the size, strip metadata."""
import io

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

ALLOWED = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MAX_PIXELS = 40_000_000


def clean_screenshot(upload):
    """Return (ContentFile, png_bytes) for a safe re-encoded copy, or raise ValidationError.

    Re-encoding drops EXIF data (phone photos can carry GPS location) and anything hidden after
    the image data. The file name is randomised later by Claim.screenshot's upload_to.
    """
    if upload.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError("Images must be 5 MB or smaller.")
    try:
        raw = upload.read()
        Image.open(io.BytesIO(raw)).verify()          # structural check
        img = Image.open(io.BytesIO(raw))             # verify() leaves the image unusable
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError("That file is not a valid image.")
    if img.format not in ALLOWED:
        raise ValidationError("Upload a JPG, PNG or WEBP image.")
    if img.width * img.height > MAX_PIXELS:
        raise ValidationError("That image is too large.")
    img = img.convert("RGB")
    img.thumbnail((2000, 2000))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return ContentFile(out.getvalue(), name="upload.jpg"), out.getvalue()
