"""WhatsApp / Open Graph preview card (1200x630), drawn with Pillow."""
import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (17, 19, 23)
ACCENT = (224, 56, 62)
COLOURS = {"FALSE": (220, 38, 38), "TRUE": (22, 163, 74), "PARTIALLY_TRUE": (250, 204, 21),
           "MISLEADING": (249, 115, 22), "UNVERIFIED": (245, 158, 11)}
FONT_DIRS = ["/System/Library/Fonts/Supplemental", "/usr/share/fonts/truetype/dejavu",
             "/usr/share/fonts/TTF", "C:/Windows/Fonts"]
FONTS = {"bold": ["Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
         "regular": ["Arial.ttf", "DejaVuSans.ttf", "arial.ttf"]}


def _font(kind, size):
    for d in FONT_DIRS:
        for name in FONTS[kind]:
            path = Path(d) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def render(claim, label, snippet):
    """label: e.g. 'FALSE' or 'PENDING VERIFICATION'. snippet: English text to show.

    Kannada glyph shaping needs libraqm, which Pillow often lacks, so Kannada claims get an
    English caption on the image (the WhatsApp message itself carries the Kannada text).
    """
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # soft glow + brand bar
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    for r, a in ((520, 18), (380, 26), (240, 34)):
        gd.ellipse((W // 2 - r, -r, W // 2 + r, r), fill=(BG[0] + a // 3, BG[1] + a, BG[2] + a))
    img = Image.blend(img, glow, 0.6)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 10), fill=ACCENT)
    d.ellipse((60, 52, 112, 104), fill=ACCENT)
    d.line((74, 79, 84, 89, 99, 68), fill="white", width=6, joint="curve")
    d.text((128, 58), "Verdicto", font=_font("bold", 40), fill="white")
    d.text((W - 60, 66), "Crowd-verified fact check", font=_font("regular", 26), fill=(160, 160, 170),
           anchor="ra")

    colour = COLOURS.get(claim.verdict if claim.status == "FINAL" else "", (82, 82, 91))
    badge_font = _font("bold", 64 if len(label) < 12 else 48)
    bw = d.textlength(label, font=badge_font) + 64
    d.rounded_rectangle((60, 150, 60 + bw, 250), radius=18, fill=colour)
    d.text((92, 200), label, font=badge_font, fill="black" if colour in (COLOURS["PARTIALLY_TRUE"],) else "white",
           anchor="lm")

    y = 290
    for line in textwrap.wrap(f"“{snippet}”", width=46)[:4]:
        d.text((60, y), line, font=_font("regular", 42), fill=(235, 235, 240))
        y += 56
    footer = (f"{claim.confidence_pct}% weighted agreement · {claim.votes.count()} verifier votes"
              if claim.status == "FINAL" else "AI-provisional · awaiting verified experts")
    d.text((60, H - 70), footer, font=_font("regular", 28), fill=(160, 160, 170))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
