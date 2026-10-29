"""Weekly Pinterest batch without the Pinterest API.

Makes a week of Pins (default 28 = one every 6 hours), saves the images to docs/pins/<week>/
(served by GitHub Pages) and writes docs/pins/<week>/pins.csv in Pinterest's bulk-upload format.
Upload that CSV once a week: Pinterest > Settings > Import content > Upload .csv.
Each Pin carries a future Publish date, so Pinterest publishes them on schedule by itself.
"""
import csv
import datetime as dt
import os
import sys

from common import (BRAND_SYSTEM, ROOT, claude_json, download, listing_url, load_state, next_product,
                    replicate_run, save_state)

KONTEXT = os.environ.get("IMAGE_MODEL", "black-forest-labs/flux-kontext-pro")
PAGES_BASE = os.environ.get("PAGES_BASE", "https://zehriofficedekor-rgb.github.io/zehnur-social").rstrip("/")
BOARD = os.environ.get("PINTEREST_BOARD") or "Christmas Simmer Pot Gifts"
COUNT = int(os.environ.get("PIN_COUNT") or 28)
HOURS_UTC = [2, 8, 14, 20]  # every 6 hours; 14:00 and 20:00 UTC hit US morning and afternoon
AI_IMAGES = os.environ.get("USE_AI_PIN_IMAGE", "1") == "1"


def pin_copy(product, angle):
    return claude_json(
        BRAND_SYSTEM,
        f"""Write a Pinterest Pin for this Etsy listing.
Product title: {product['title']}
Angle for this Pin (make it different from other Pins of the same product): {angle}

Return JSON with:
- "title": max 90 characters, keyword-rich (e.g. simmer pot, stovetop potpourri, Christmas gift), no hashtags
- "description": 250-450 characters, starts with a benefit, includes 3-4 natural search phrases people type on Pinterest, ends with a soft call to action. No hashtags, no emojis.
- "keywords": list of 5 search phrases
- "scene": one sentence describing a cozy, realistic setting to place the product in (for an image editor)""",
    )


ANGLES = [
    "teacher appreciation / classroom Christmas gift", "office and coworker gift in bulk",
    "winter wedding or holiday party favor", "hostess gift for a holiday dinner",
    "neighbor and friends gift exchange", "how it makes the whole house smell like Christmas",
    "natural ingredients: Antalya oranges and spices from Istanbul's spice bazaar",
    "personalized labels with names or a logo",
]


def pin_image(product, scene, out):
    """Restage the product photo in a vertical 2:3 scene; fall back to the Etsy photo."""
    if AI_IMAGES:
        prompt = (
            f"Place this exact product in this setting: {scene}. Keep the pouch, its label text and the "
            "ingredients exactly as they are; do not change or add any text. No people, no hands. Natural "
            "warm light, realistic photo, vertical composition with the product as the clear focus."
        )
        try:
            url = replicate_run(KONTEXT, {"prompt": prompt, "input_image": product["img"],
                                          "aspect_ratio": "2:3", "output_format": "jpg"})
            download(url, out)
            return True
        except Exception as e:
            print(f"AI image failed, using Etsy photo: {e}")
    try:
        download(product["img"], out)
        return True
    except Exception as e:
        print(f"Could not download Etsy photo: {e}")
        return False


def slots(start, n):
    day, out = start, []
    while len(out) < n:
        for h in HOURS_UTC:
            out.append(dt.datetime.combine(day, dt.time(h, 0)))
        day += dt.timedelta(days=1)
    return out[:n]


def main():
    state = load_state()
    start = dt.date.fromisoformat(os.environ["START_DATE"]) if os.environ.get("START_DATE") \
        else dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
    week = start.isoformat()
    folder = ROOT / "docs" / "pins" / week
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, when in enumerate(slots(start, COUNT)):
        product = next_product("pinterest", state, today=when.date())
        angle = ANGLES[(state.get("pinterest_angle", 0)) % len(ANGLES)]
        state["pinterest_angle"] = state.get("pinterest_angle", 0) + 1
        try:
            copy = pin_copy(product, angle)
        except Exception as e:
            print(f"Copy failed for {product['id']}: {e}")
            continue
        name = f"{i + 1:02d}-{product['id']}.jpg"
        if not pin_image(product, copy.get("scene", "a cozy holiday kitchen"), folder / name):
            continue
        rows.append({
            "Title": copy["title"][:100],
            "Media URL": f"{PAGES_BASE}/pins/{week}/{name}",
            "Pinterest board": BOARD,
            "Thumbnail": "",
            "Description": copy["description"][:500],
            "Link": listing_url(product["id"], "pinterest"),
            "Publish date": when.strftime("%Y-%m-%dT%H:%M:%S"),
            "Keywords": ", ".join(copy.get("keywords", [])[:6]),
        })
        print(f"{when:%a %d %b %H:%M} UTC  {product['id']}  {copy['title']}")
        save_state(state)
    with open(folder / "pins.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Title", "Media URL", "Pinterest board", "Thumbnail",
                                          "Description", "Link", "Publish date", "Keywords"])
        w.writeheader()
        w.writerows(rows)
    state["pinterest_last_batch"] = {"week": week, "pins": len(rows), "csv": f"{PAGES_BASE}/pins/{week}/pins.csv"}
    save_state(state)
    print(f"\n{len(rows)} Pins ready. CSV: {PAGES_BASE}/pins/{week}/pins.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
