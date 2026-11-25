"""Compile Kannada translations without GNU gettext: locale/kn_strings.py -> django.po + django.mo."""
import ast
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

HEADER = ('Project-Id-Version: Verdicto\nLanguage: kn\nMIME-Version: 1.0\n'
          'Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n'
          'Plural-Forms: nplurals=2; plural=(n != 1);\n')


def load_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "KN":
            return ast.literal_eval(node.value)
    raise ValueError("KN dict not found")


def po_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_mo(messages, path):
    """GNU .mo format: header, two offset tables, then the strings (sorted by msgid)."""
    keys = sorted(messages)
    ids = b"".join(k.encode() + b"\0" for k in keys)
    strs = b"".join(messages[k].encode() + b"\0" for k in keys)
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets, voffsets, ko, vo = [], [], 0, 0
    for k in keys:
        kb, vb = k.encode(), messages[k].encode()
        koffsets += [len(kb), keystart + ko]
        voffsets += [len(vb), valuestart + vo]
        ko += len(kb) + 1
        vo += len(vb) + 1
    out = struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    out += struct.pack(f"{len(koffsets)}i", *koffsets) + struct.pack(f"{len(voffsets)}i", *voffsets)
    path.write_bytes(out + ids + strs)


class Command(BaseCommand):
    help = "Build locale/kn/LC_MESSAGES/django.po and .mo from locale/kn_strings.py"

    def handle(self, *args, **opts):
        base = Path(settings.BASE_DIR) / "locale"
        kn = load_strings(base / "kn_strings.py")
        out_dir = base / "kn" / "LC_MESSAGES"
        out_dir.mkdir(parents=True, exist_ok=True)
        po = ['# Kannada translations for Verdicto. Generated from locale/kn_strings.py.',
              'msgid ""', 'msgstr ""'] + [f'"{line}\\n"' for line in HEADER.strip().split("\n")]
        for msgid, msgstr in kn.items():
            po += ["", f'msgid "{po_escape(msgid)}"', f'msgstr "{po_escape(msgstr)}"']
        (out_dir / "django.po").write_text("\n".join(po) + "\n", encoding="utf-8")
        write_mo({"": HEADER, **kn}, out_dir / "django.mo")
        self.stdout.write(self.style.SUCCESS(f"Compiled {len(kn)} Kannada strings."))
