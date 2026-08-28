#!/usr/bin/env python3
"""Make a printable PDF of in-room housekeeping QR codes — one card per room.

A guest in the room scans it and asks for housekeeping, saying what they need. No app,
no account, and the page shows them nothing but the hotel's name and their own room
number — see the guest route in backend/routers/housekeeping.py for what it discloses.

Each card carries one room's own link, containing that room's id and nothing else. The
ids are uuid4, and no endpoint lists rooms without an account, so a card for 204 is a
card for 204 only.

Cards are cut apart and placed in the rooms — beside the phone, or inside the folder. Print at 100% scale, no "fit to page":
scaling shrinks the QR and a shrunk QR is a QR that phones fail to read across a table.

    python3 make-room-qr-pdf.py            # every room
    python3 make-room-qr-pdf.py 101 204    # only these room numbers
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
OUT = "room-qr-codes.pdf"

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
    c.drawCentredString(x + w / 2, y + h - 24 * mm, f"ROOM {table['number']}")

    # The QR is the reason the card exists, so it gets the space. 52mm reads reliably
    # from across a table on a phone held at arm's length.
    size = 52 * mm
    c.drawImage(qr_image(f"{SITE}/room/{table['id']}"),
                x + (w - size) / 2, y + (h - size) / 2 - 8 * mm,
                width=size, height=size, mask=None)

    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x + w / 2, y + 17 * mm, "NEED HOUSEKEEPING? SCAN HERE")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(x + w / 2, y + 12 * mm, "Tell us what you need and we will come")
    c.drawCentredString(x + w / 2, y + 8 * mm, "No app needed")


def main():
    global token
    if not shutil.which("curl"):
        print("curl is not on PATH — this script needs it.")
        sys.exit(1)

    wanted = {a.strip().upper() for a in sys.argv[1:]}

    print("In-room housekeeping QR codes — a printable PDF, four cards to a page.")
    print("Sign in as the hotel's admin (not the platform operator).\n")
    email = input("  email or phone: ").strip()
    password = getpass.getpass("  password: ")

    status, body = call("/auth/login", {"email": email, "password": password})
    if status != 200:
        print(f"\nSign-in failed ({status}). {body}")
        sys.exit(1)
    token = body["token"]

    hotel = call("/property")[1].get("name") or "Restaurant"
    status, tables = call("/rooms")
    if status != 200:
        print(f"Could not read the rooms ({status}). {tables}")
        sys.exit(1)
    if wanted:
        tables = [t for t in tables if str(t["number"]).upper() in wanted]
    # By floor then number, so the printed order matches how you walk the building.
    tables.sort(key=lambda t: (str(t.get("floor") or ""), str(t["number"])))

    if not tables:
        print("No rooms to print. Add rooms first, on the Hotel -> Rooms screen.")
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
        print(f"  room {str(table['number']):6} floor {str(table.get('floor') or '-'):3} {SITE}/room/{table['id']}")

    c.save()
    print(f"\nWritten to {OUT} — {len(tables)} card(s), "
          f"{(len(tables) + per_page - 1) // per_page} page(s).")
    print("\nPrint at 100% scale. Do not use 'fit to page': it shrinks the code, and a")
    print("shrunk QR is one a phone struggles with in dim room lighting.")


if __name__ == "__main__":
    main()
