#!/usr/bin/env python3
"""Load the Anand Castle menu — 145 items across 15 sections, with photographs.

Prices were read off the rendered menu pages, not off the PDF's text layer. That
distinction matters: the text layer emits names and prices as two separate blocks and
does not preserve which price belongs to which line, and pairing them in order produced
Tandoori Roti at Rs109 and Tandoori Butter Roti at Rs59 — a plain roti dearer than a
buttered one. The Papad and Breads columns had in fact been swapped. Every price here
comes from looking at the page.

Idempotent: an item whose name is already on the menu is left alone, so a half-finished
run can just be run again.

    python3 setup-anand-castle-menu.py
"""
import getpass
import json
import shutil
import subprocess
import sys

SITE = "https://barflow-33e80.web.app"
API = f"{SITE}/api"

# Unsplash, hotlinked at a size the tablet can render quickly. These are representative
# of the dish, not photographs of Anand Castle's own food — a paneer curry stands in for
# each paneer curry. Swap any of them on the Menu screen; the field takes any image URL.
IMG = {
    "tea":        "https://images.unsplash.com/photo-1571934811356-5cc061b6821f?w=600&q=80&auto=format&fit=crop",
    "greentea":   "https://images.unsplash.com/photo-1627435601361-ec25f5b1d0e5?w=600&q=80&auto=format&fit=crop",
    "coffee":     "https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=600&q=80&auto=format&fit=crop",
    "coldcoffee": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?w=600&q=80&auto=format&fit=crop",
    "milk":       "https://images.unsplash.com/photo-1563636619-e9143da7973b?w=600&q=80&auto=format&fit=crop",
    "lassi":      "https://images.unsplash.com/photo-1626200419199-391ae4be7a41?w=600&q=80&auto=format&fit=crop",
    "shake":      "https://images.unsplash.com/photo-1572490122747-3968b75cc699?w=600&q=80&auto=format&fit=crop",
    "softdrink":  "https://images.unsplash.com/photo-1581006852262-e4307cf6283a?w=600&q=80&auto=format&fit=crop",
    "water":      "https://images.unsplash.com/photo-1560023907-5f339617ea30?w=600&q=80&auto=format&fit=crop",
    "lime":       "https://images.unsplash.com/photo-1621263764928-df1444c5e859?w=600&q=80&auto=format&fit=crop",
    "mojito":     "https://images.unsplash.com/photo-1551538827-9c037cb4f32a?w=600&q=80&auto=format&fit=crop",
    "icedtea":    "https://images.unsplash.com/photo-1499638673689-79a0b5115d87?w=600&q=80&auto=format&fit=crop",
    "omelette":   "https://images.unsplash.com/photo-1525351484163-7529414344d8?w=600&q=80&auto=format&fit=crop",
    "egg":        "https://images.unsplash.com/photo-1607690424560-35d967d6ad7c?w=600&q=80&auto=format&fit=crop",
    "paratha":    "https://images.unsplash.com/photo-1626132647523-66f5bf380027?w=600&q=80&auto=format&fit=crop",
    "puri":       "https://images.unsplash.com/photo-1626500155537-8b2acbaa4dc5?w=600&q=80&auto=format&fit=crop",
    "fries":      "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?w=600&q=80&auto=format&fit=crop",
    "chilli":     "https://images.unsplash.com/photo-1626132647523-66f5bf380027?w=600&q=80&auto=format&fit=crop",
    "manchurian": "https://images.unsplash.com/photo-1585032226651-759b368d7246?w=600&q=80&auto=format&fit=crop",
    "noodles":    "https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=600&q=80&auto=format&fit=crop",
    "friedrice":  "https://images.unsplash.com/photo-1603133872878-684f208fb84b?w=600&q=80&auto=format&fit=crop",
    "tikki":      "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=600&q=80&auto=format&fit=crop",
    "pakora":     "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=600&q=80&auto=format&fit=crop",
    "paneertikka":"https://images.unsplash.com/photo-1567188040759-fb8a883dc6d8?w=600&q=80&auto=format&fit=crop",
    "chaap":      "https://images.unsplash.com/photo-1626074353765-517a681e40be?w=600&q=80&auto=format&fit=crop",
    "tandoori":   "https://images.unsplash.com/photo-1610057099443-fde8c4d50f91?w=600&q=80&auto=format&fit=crop",
    "chickentikka":"https://images.unsplash.com/photo-1599487488170-d11ec9c172f0?w=600&q=80&auto=format&fit=crop",
    "pasta":      "https://images.unsplash.com/photo-1621996346565-e3dbc646d9a9?w=600&q=80&auto=format&fit=crop",
    "maggi":      "https://images.unsplash.com/photo-1612929633738-8fe44f7ec841?w=600&q=80&auto=format&fit=crop",
    "raita":      "https://images.unsplash.com/photo-1631452180519-c014fe946bc7?w=600&q=80&auto=format&fit=crop",
    "soup":       "https://images.unsplash.com/photo-1547592166-23ac45744acd?w=600&q=80&auto=format&fit=crop",
    "chickensoup":"https://images.unsplash.com/photo-1603105037880-880cd4edfb0d?w=600&q=80&auto=format&fit=crop",
    "dal":        "https://images.unsplash.com/photo-1546833999-b9f581a1996d?w=600&q=80&auto=format&fit=crop",
    "aloo":       "https://images.unsplash.com/photo-1596797038530-2c107229654b?w=600&q=80&auto=format&fit=crop",
    "mixveg":     "https://images.unsplash.com/photo-1512058564366-18510be2db19?w=600&q=80&auto=format&fit=crop",
    "paneer":     "https://images.unsplash.com/photo-1631452180519-c014fe946bc7?w=600&q=80&auto=format&fit=crop",
    "paneerbutter":"https://images.unsplash.com/photo-1588166524941-3bf61a9c41db?w=600&q=80&auto=format&fit=crop",
    "mushroom":   "https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=600&q=80&auto=format&fit=crop",
    "chickencurry":"https://images.unsplash.com/photo-1603894584373-5ac82b2ae398?w=600&q=80&auto=format&fit=crop",
    "butterchicken":"https://images.unsplash.com/photo-1588166524941-3bf61a9c41db?w=600&q=80&auto=format&fit=crop",
    "eggcurry":   "https://images.unsplash.com/photo-1607690424560-35d967d6ad7c?w=600&q=80&auto=format&fit=crop",
    "papad":      "https://images.unsplash.com/photo-1626132647523-66f5bf380027?w=600&q=80&auto=format&fit=crop",
    "salad":      "https://images.unsplash.com/photo-1512621776951-a57141f2eefd?w=600&q=80&auto=format&fit=crop",
    "roti":       "https://images.unsplash.com/photo-1626074353765-517a681e40be?w=600&q=80&auto=format&fit=crop",
    "naan":       "https://images.unsplash.com/photo-1601050690597-df0568f70950?w=600&q=80&auto=format&fit=crop",
    "rice":       "https://images.unsplash.com/photo-1596797038530-2c107229654b?w=600&q=80&auto=format&fit=crop",
    "biryani":    "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?w=600&q=80&auto=format&fit=crop",
    "gulabjamun": "https://images.unsplash.com/photo-1601303516534-bf0b1eb70e64?w=600&q=80&auto=format&fit=crop",
    "icecream":   "https://images.unsplash.com/photo-1497034825429-c343d7c6a68f?w=600&q=80&auto=format&fit=crop",
}

BAR, KIT = "bar", "kitchen"

# (name, category, price, station, image key[, [(portion label, price), ...]])
# A row with no portion list is a single-price dish, which is most of them.
MENU = [
    # ---------------- Beverages
    ("Tea", "Beverages", 59, BAR, "tea"),
    ("Lemon Tea", "Beverages", 69, BAR, "tea"),
    ("Green Tea", "Beverages", 69, BAR, "greentea"),
    ("Hot Coffee", "Beverages", 89, BAR, "coffee"),
    ("Hot Milk", "Beverages", 99, BAR, "milk"),
    ("Chocolate Milk", "Beverages", 129, BAR, "milk"),
    ("Lassi (Sweet/Salty)", "Beverages", 109, BAR, "lassi"),
    ("Strawberry Lassi", "Beverages", 129, BAR, "lassi"),
    ("Blueberry Lassi", "Beverages", 129, BAR, "lassi"),
    ("Cold Coffee", "Beverages", 129, BAR, "coldcoffee"),
    ("Cold Coffee with Ice-Cream", "Beverages", 159, BAR, "coldcoffee"),
    ("Soft Drink", "Beverages", 69, BAR, "softdrink"),
    ("Mineral Water", "Beverages", 39, BAR, "water"),
    ("Fresh Lime Soda", "Beverages", 129, BAR, "lime"),
    ("Fresh Lime Water", "Beverages", 99, BAR, "lime"),
    ("Mint Mojito", "Beverages", 149, BAR, "mojito"),
    ("Lemon Iced Tea", "Beverages", 169, BAR, "icedtea"),
    ("Blueberry Shake", "Beverages", 169, BAR, "shake"),
    ("Strawberry Shake", "Beverages", 169, BAR, "shake"),
    ("Chocolate Shake", "Beverages", 169, BAR, "shake"),
    ("Oreo Shake", "Beverages", 169, BAR, "shake"),

    # ---------------- Breakfast (7:30am to 10:30am)
    ("Plain Omelette with Toast", "Breakfast", 129, KIT, "omelette"),
    ("Masala Omelette with Toast", "Breakfast", 149, KIT, "omelette"),
    ("Boiled Egg (2 pcs)", "Breakfast", 89, KIT, "egg"),
    ("Aloo Paratha (2 pcs) with Curd & Pickle", "Breakfast", 169, KIT, "paratha"),
    ("Onion Paratha (2 pcs) with Curd & Pickle", "Breakfast", 169, KIT, "paratha"),
    ("Aloo Pyaz Paratha (2 pcs) with Curd & Pickle", "Breakfast", 179, KIT, "paratha"),
    ("Paneer Paratha (2 pcs) with Curd & Pickle", "Breakfast", 199, KIT, "paratha"),
    ("Gobhi Paratha (2 pcs) with Curd & Pickle", "Breakfast", 179, KIT, "paratha"),
    ("Mix Paratha (2 pcs) with Curd & Pickle", "Breakfast", 299, KIT, "paratha"),
    ("Puri Bhaji", "Breakfast", 149, KIT, "puri"),

    # ---------------- Chinese
    ("French Fries", "Chinese", 189, KIT, "fries"),
    ("Chilli Potato", "Chinese", 249, KIT, "chilli"),
    ("Chilli Chicken", "Chinese", 399, KIT, "chilli"),
    ("Chilli Paneer", "Chinese", 329, KIT, "chilli"),
    ("Veg. Manchurian", "Chinese", 299, KIT, "manchurian"),
    ("Chilli Mushroom", "Chinese", 329, KIT, "mushroom"),
    ("Veg. Noodles", "Chinese", 179, KIT, "noodles"),
    ("Veg. Hakka Noodles", "Chinese", 189, KIT, "noodles"),
    ("Paneer Noodles", "Chinese", 199, KIT, "noodles"),
    ("Chicken Noodles", "Chinese", 219, KIT, "noodles"),
    ("Egg Noodles", "Chinese", 199, KIT, "noodles"),
    ("Chicken Hakka Noodles", "Chinese", 249, KIT, "noodles"),
    ("Veg. Fried Rice", "Chinese", 189, KIT, "friedrice"),
    ("Paneer Fried Rice", "Chinese", 199, KIT, "friedrice"),
    ("Egg Fried Rice", "Chinese", 199, KIT, "friedrice"),
    ("Chicken Fried Rice", "Chinese", 249, KIT, "friedrice"),

    # ---------------- Veg Starters
    ("Adrak Ki Tikki (8 pcs)", "Veg Starters", 199, KIT, "tikki"),
    ("Veg. Cutlet", "Veg Starters", 199, KIT, "tikki"),
    ("Mix Pakora", "Veg Starters", 169, KIT, "pakora"),
    ("Paneer Pakora", "Veg Starters", 199, KIT, "pakora"),
    ("Veg. Cutlett", "Veg Starters", 199, KIT, "tikki"),
    ("Paneer Tikka (8 pcs)", "Veg Starters", 249, KIT, "paneertikka"),
    ("Paneer Achari Tikka (8 pcs)", "Veg Starters", 259, KIT, "paneertikka"),
    ("Paneer Malai Tikka (8 pcs)", "Veg Starters", 279, KIT, "paneertikka"),
    ("Soya Masala Chaap (8 pcs)", "Veg Starters", 199, KIT, "chaap"),
    ("Soya Malai Chaap (8 pcs)", "Veg Starters", 239, KIT, "chaap"),
    ("Soya Achari Tikka (8 pcs)", "Veg Starters", 219, KIT, "chaap"),

    # ---------------- Non-Veg Starters
    # Tandoori Chicken is the only one the menu prices by portion; the rest show a
    # single (full, 8pc) price. Both halves are listed so the till can ring either.
    ("Tandoori Chicken", "Non-Veg Starters", 279, KIT, "tandoori",
     [("Half (4 pcs)", 279), ("Full (8 pcs)", 529)]),
    ("Chicken Masala Tikka (8 pcs)", "Non-Veg Starters", 349, KIT, "chickentikka"),
    ("Chicken Achari Tikka (8 pcs)", "Non-Veg Starters", 349, KIT, "chickentikka"),
    ("Chicken Malai Tikka (8 pcs)", "Non-Veg Starters", 389, KIT, "chickentikka"),
    ("Afghani Chicken (8 pcs)", "Non-Veg Starters", 389, KIT, "chickentikka"),
    ("Chicken Pakora", "Non-Veg Starters", 369, KIT, "pakora"),

    # ---------------- Pasta
    ("Veg. White Sauce Pasta", "Pasta", 249, KIT, "pasta"),
    ("Veg. Red Sauce Pasta", "Pasta", 239, KIT, "pasta"),
    ("Pasta Home Style", "Pasta", 199, KIT, "pasta"),
    ("Non Veg. White Sauce Pasta", "Pasta", 289, KIT, "pasta"),
    ("Non Veg. Red Sauce Pasta", "Pasta", 279, KIT, "pasta"),

    # ---------------- Maggi
    ("Veg. Maggi", "Maggi", 119, KIT, "maggi"),
    ("Veg. Maggi with Butter", "Maggi", 149, KIT, "maggi"),
    ("Paneer Maggi", "Maggi", 169, KIT, "maggi"),
    ("Cheese Corn Maggi", "Maggi", 159, KIT, "maggi"),
    ("Egg Maggi", "Maggi", 159, KIT, "maggi"),
    ("Chicken Maggi", "Maggi", 179, KIT, "maggi"),

    # ---------------- Raita
    ("Boondi Raita", "Raita", 139, KIT, "raita"),
    ("Mix Raita", "Raita", 159, KIT, "raita"),
    ("Pineapple Raita", "Raita", 179, KIT, "raita"),

    # ---------------- Soups
    ("Veg. Hot & Sour Soup", "Soups", 149, KIT, "soup"),
    ("Veg. Sweet Corn Soup", "Soups", 159, KIT, "soup"),
    ("Veg. Manchow Soup", "Soups", 149, KIT, "soup"),
    ("Cream of Veg. Soup", "Soups", 169, KIT, "soup"),
    ("Cream of Mushroom Soup", "Soups", 179, KIT, "soup"),
    ("Lemon Coriander Soup", "Soups", 149, KIT, "soup"),
    ("Veg. Clear Soup", "Soups", 149, KIT, "soup"),
    ("Chicken Hot & Sour Soup", "Soups", 169, KIT, "chickensoup"),
    ("Chicken Manchow Soup", "Soups", 169, KIT, "chickensoup"),
    ("Cream of Chicken Soup", "Soups", 189, KIT, "chickensoup"),
    ("Chicken Clear Soup", "Soups", 179, KIT, "chickensoup"),

    # ---------------- Veg Main Course
    ("Dal Makhani", "Veg Main Course", 299, KIT, "dal"),
    ("Dal Tadka", "Veg Main Course", 239, KIT, "dal"),
    ("Dal Fry", "Veg Main Course", 249, KIT, "dal"),
    ("Jeera Aloo", "Veg Main Course", 199, KIT, "aloo"),
    ("Aloo Gobhi", "Veg Main Course", 239, KIT, "aloo"),
    ("Masala Chaap Gravy", "Veg Main Course", 289, KIT, "chaap"),
    ("Kali Mirch Chaap Gravy", "Veg Main Course", 319, KIT, "chaap"),
    ("Mix Veg.", "Veg Main Course", 249, KIT, "mixveg"),
    ("Paneer Do Pyaza", "Veg Main Course", 299, KIT, "paneer"),
    ("Paneer Kali Mirch", "Veg Main Course", 349, KIT, "paneer"),
    ("Kadhai Paneer", "Veg Main Course", 319, KIT, "paneer"),
    ("Matar Paneer", "Veg Main Course", 289, KIT, "paneer"),
    ("Shahi Paneer", "Veg Main Course", 319, KIT, "paneerbutter"),
    ("Paneer Butter Masala", "Veg Main Course", 319, KIT, "paneerbutter"),
    ("Paneer Lababdar", "Veg Main Course", 319, KIT, "paneerbutter"),
    ("Paneer Bhurji", "Veg Main Course", 269, KIT, "paneer"),
    ("Matar Mushroom", "Veg Main Course", 309, KIT, "mushroom"),
    ("Mushroom Masala", "Veg Main Course", 329, KIT, "mushroom"),
    ("Mushroom Do Pyaza", "Veg Main Course", 329, KIT, "mushroom"),

    # ---------------- Non-Veg Main Course (half 4pc / full 8pc)
    ("Karahi Chicken", "Non-Veg Main Course", 349, KIT, "chickencurry",
     [("Half (4 pcs)", 349), ("Full (8 pcs)", 669)]),
    ("Chicken Curry", "Non-Veg Main Course", 349, KIT, "chickencurry",
     [("Half (4 pcs)", 349), ("Full (8 pcs)", 669)]),
    ("Chicken Masala", "Non-Veg Main Course", 349, KIT, "chickencurry",
     [("Half (4 pcs)", 349), ("Full (8 pcs)", 669)]),
    ("Chicken Do Pyaza", "Non-Veg Main Course", 349, KIT, "chickencurry",
     [("Half (4 pcs)", 349), ("Full (8 pcs)", 669)]),
    ("Kali Mirch Chicken", "Non-Veg Main Course", 389, KIT, "chickencurry",
     [("Half (4 pcs)", 389), ("Full (8 pcs)", 669)]),
    ("Butter Chicken", "Non-Veg Main Course", 379, KIT, "butterchicken",
     [("Half (4 pcs)", 379), ("Full (8 pcs)", 689)]),
    ("Chicken Lababdar", "Non-Veg Main Course", 349, KIT, "chickencurry",
     [("Half (4 pcs)", 349), ("Full (8 pcs)", 669)]),
    ("Egg Curry (2 pcs)", "Non-Veg Main Course", 229, KIT, "eggcurry"),

    # ---------------- Papad & Salad
    ("Roasted Papad (2 pcs)", "Papad & Salad", 59, KIT, "papad"),
    ("Fried Papad (2 pcs)", "Papad & Salad", 69, KIT, "papad"),
    ("Masala Papad (2 pcs)", "Papad & Salad", 129, KIT, "papad"),
    ("Green Salad", "Papad & Salad", 149, KIT, "salad"),
    ("Cucumber Salad", "Papad & Salad", 139, KIT, "salad"),
    ("Kimchi Salad", "Papad & Salad", 159, KIT, "salad"),

    # ---------------- Breads
    ("Tandoori Roti", "Breads", 35, KIT, "roti"),
    ("Tandoori Butter Roti", "Breads", 40, KIT, "roti"),
    ("Lachha Paratha", "Breads", 59, KIT, "roti"),
    ("Plain Nan", "Breads", 69, KIT, "naan"),
    ("Butter Nan", "Breads", 89, KIT, "naan"),
    ("Garlic Nan", "Breads", 99, KIT, "naan"),
    ("Stuffed Nan", "Breads", 109, KIT, "naan"),

    # ---------------- Rice
    ("Steam Rice", "Rice", 159, KIT, "rice"),
    ("Jeera Rice", "Rice", 169, KIT, "rice"),
    ("Matar Pulao", "Rice", 179, KIT, "rice"),
    ("Veg. Biryani with Raita", "Rice", 299, KIT, "biryani"),
    ("Chicken Biryani with Raita", "Rice", 359, KIT, "biryani"),

    # ---------------- Desserts
    ("Gulab Jamun (2 pcs)", "Desserts", 99, KIT, "gulabjamun"),
    ("Gulab Jamun (1 pc) with Ice-Cream", "Desserts", 99, KIT, "gulabjamun"),
    ("Ice-Cream (2 Scoop)", "Desserts", 89, KIT, "icecream"),
]

token = None


def call(path, body=None, method=None):
    """One request, through curl — the python.org build on macOS has no root
    certificates unless its Install Certificates.command has been run."""
    cmd = ["curl", "-s", "--max-time", "90", "-w", "\n%{http_code}", API + path]
    cmd += ["-X", method or ("POST" if body is not None else "GET")]
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


def main():
    global token
    if not shutil.which("curl"):
        print("curl is not on PATH — this script needs it.")
        sys.exit(1)

    cats = {}
    for row in MENU:
        cats[row[1]] = cats.get(row[1], 0) + 1
    portioned = sum(1 for row in MENU if len(row) > 5)
    print(f"Anand Castle menu — {len(MENU)} items in {len(cats)} sections, "
          f"{portioned} priced by portion:")
    for c, n in cats.items():
        print(f"   {c:22} {n:>3}")
    print("\nPrices were read off the menu pages, not the PDF text layer, which pairs")
    print("names and prices wrongly. Check a few against the printed menu afterwards.\n")

    print("Sign in as the Anand Castle admin (not the platform operator).")
    email = input("  email or phone: ").strip()
    password = getpass.getpass("  password: ")

    status, body = call("/auth/login", {"email": email, "password": password})
    if status != 200:
        print(f"\nSign-in failed ({status}). {body}")
        sys.exit(1)
    token = body["token"]
    print(f"\nSigned in as {body['user']['name']}.")
    prop = call("/property")[1]
    print(f"Property: {prop.get('name')}\n")

    have = {i["name"].strip().casefold() for i in call("/menu")[1]}
    made = skipped = 0
    failed = []
    for row in MENU:
        name, category, price, station, img = row[:5]
        portions = row[5] if len(row) > 5 else []
        if name.strip().casefold() in have:
            skipped += 1
            continue
        body = {
            "name": name, "category": category, "price": float(price),
            "station": station, "image": IMG.get(img, ""),
        }
        if portions:
            # One dish, priced by portion, chosen when it is ordered. Half is listed
            # first so the scalar `price` the API mirrors is the cheaper one — an
            # untaught reader then shows "from Rs379", which is incomplete rather
            # than wrong.
            body["variants"] = [{"label": lbl, "price": float(amt)}
                                for lbl, amt in portions]
        s, b = call("/menu", body)
        if s == 200:
            made += 1
        else:
            failed.append((name, s, str(b)[:80]))

    print(f"  {made} added, {skipped} already on the menu")
    if failed:
        print(f"  {len(failed)} failed:")
        for name, s, msg in failed[:10]:
            print(f"    {name:44} {s} {msg}")

    items = call("/menu")[1]
    print(f"\nThe menu now has {len(items)} items.")
    print("Check them at Restaurant -> Menu. Any price or photograph is editable there.")


if __name__ == "__main__":
    main()
