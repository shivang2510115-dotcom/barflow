#!/usr/bin/env python3
"""Make a printable PDF of table QR codes — one card per table, four to an A4 page.

Each card carries one table's own link. The link contains that table's id and nothing
else, so a guest who scans table 5 can order to table 5 and has no route to any other:
the ids are uuid4 (not guessable, not sequential) and there is no endpoint that lists
tables without an account. Getting a different table's QR is the only way to order to a
different table — which is exactly the physical situation you want.

Cards are cut apart and placed on the tables. Print at 100% scale, no "fit to page":
scaling shrinks the QR and a shrunk QR is a QR that phones fail to read across a table.

    python3 make-table-qr-pdf.py            # every table
    python3 make-table-qr-pdf.py T01 T05    # only these labels
"""
import getpass
import io
import json
import shutil
import subprocess
import sys

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

SITE = "https://barflow-33e80.web.app"
API = f"{SITE}/api"
OUT = "table-qr-codes.pdf"

# Four cards to an A4 sheet, in a 2x2 grid, with a margin wide enough to cut inside.
PAGE_W, PAGE_H = A4
MARGIN = 12 * mm
COLS, ROWS = 2, 2

INK = HexColor("#1c1917")     # stone-900
MUTED = HexColor("#78716c")   # stone-500
ACCENT = HexColor("#ea580c")  # orange-600

token = None


def call(path, body=None):
    """Through curl — the python.org build on macOS ships without root certificates."""
    cmd = ["curl", "-s", "--max-time", "90", "-w", "\n%{http_code}", API + path]
    cmd += ["-X", "POST" if body is not None else "GET"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    payload, _, code = out.rpartition("\n")
    try:
        return int(code or 0), json.loads(payload or "{}")
    except (ValueError, json.JSONDecodeError):
        return int(code or 0), payload


def qr_image(url: str):
    """A QR at the highest error correction, so it still scans with a thumbprint or a
    splash of dal on it. These live on restaurant tables."""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_H, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def draw_card(c, x, y, w, h, table, hotel_name):
    """One cuttable card: the hotel, the table, the QR, and what to do with it."""
    # A hairline to cut along. Light enough not to dominate the card if left uncut.
    c.setStrokeColor(HexColor("#d6d3d1"))
    c.setLineWidth(0.4)
    c.rect(x, y, w, h)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(x + w / 2, y + h - 12 * mm, hotel_name.upper())

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(x + w / 2, y + h - 24 * mm, f"TABLE {table['label']}")

    # The QR is the reason the card exists, so it gets the space. 52mm reads reliably
    # from across a table on a phone held at arm's length.
    size = 52 * mm
    c.drawImage(qr_image(f"{SITE}/t/{table['id']}"),
                x + (w - size) / 2, y + (h - size) / 2 - 8 * mm,
                width=size, height=size, mask=None)

    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x + w / 2, y + 17 * mm, "SCAN TO SEE THE MENU AND ORDER")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(x + w / 2, y + 12 * mm, "Point your camera at the code")
    c.drawCentredString(x + w / 2, y + 8 * mm, "No app needed")


def main():
    global token
    if not shutil.which("curl"):
        print("curl is not on PATH — this script needs it.")
        sys.exit(1)

    wanted = {a.strip().upper() for a in sys.argv[1:]}

    print("Table QR codes — a printable PDF, four cards to a page.")
    print("Sign in as the hotel's admin (not the platform operator).\n")
    email = input("  email or phone: ").strip()
    password = getpass.getpass("  password: ")

    status, body = call("/auth/login", {"email": email, "password": password})
    if status != 200:
        print(f"\nSign-in failed ({status}). {body}")
        sys.exit(1)
    token = body["token"]

    hotel = call("/property")[1].get("name") or "Restaurant"
    status, tables = call("/tables")
    if status != 200:
        print(f"Could not read the tables ({status}). {tables}")
        sys.exit(1)
    if wanted:
        tables = [t for t in tables if t["label"].upper() in wanted]
    tables.sort(key=lambda t: t["label"])

    if not tables:
        print("No tables to print. Add tables first, on the Restaurant -> Tables screen.")
        sys.exit(1)

    print(f"\n{hotel}: {len(tables)} table(s)\n")

    c = canvas.Canvas(OUT, pagesize=A4)
    cw = (PAGE_W - 2 * MARGIN) / COLS
    ch = (PAGE_H - 2 * MARGIN) / ROWS
    per_page = COLS * ROWS

    for i, table in enumerate(tables):
        slot = i % per_page
        if slot == 0 and i:
            c.showPage()
        col, row = slot % COLS, slot // COLS
        x = MARGIN + col * cw
        # Top-left to bottom-right, so the printed order matches how a page is read.
        y = PAGE_H - MARGIN - (row + 1) * ch
        draw_card(c, x + 3 * mm, y + 3 * mm, cw - 6 * mm, ch - 6 * mm, table, hotel)
        print(f"  {table['label']:6} {SITE}/t/{table['id']}")

    c.save()
    print(f"\nWritten to {OUT} — {len(tables)} card(s), "
          f"{(len(tables) + per_page - 1) // per_page} page(s).")
    print("\nPrint at 100% scale. Do not use 'fit to page': it shrinks the code, and a")
    print("shrunk QR is one a phone fails to read from across the table.")


if __name__ == "__main__":
    main()
