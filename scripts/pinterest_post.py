"""Create one Pin for the next product. Runs every 6 hours from GitHub Actions."""
import os
import sys

from common import (BRAND_SYSTEM, claude_json, http, listing_url, load_state, next_product,
                    replicate_run, save_state)

API = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5")
KONTEXT = os.environ.get("IMAGE_MODEL", "black-forest-labs/flux-kontext-pro")


def pin_copy(product):
    return claude_json(
        BRAND_SYSTEM,
        f"""Write a Pinterest Pin for this Etsy listing.
Product title: {product['title']}

Return JSON with:
- "title": max 90 characters, keyword-rich (e.g. simmer pot, stovetop potpourri, Christmas gift), no hashtags
- "description": 250-450 characters, starts with a benefit, includes 3-4 natural search phrases people type on Pinterest, ends with a soft call to action. No hashtags, no emojis.
- "alt_text": max 200 characters, plainly describes what is in the photo
- "scene": one sentence describing a cozy, realistic setting to place the product in (for an image editor)""",
    )


def pin_image(product, scene):
    """Optionally restage the product photo in a vertical lifestyle scene."""
    if os.environ.get("USE_AI_PIN_IMAGE", "1") != "1" or not os.environ.get("REPLICATE_API_TOKEN"):
        return product["img"]
    prompt = (
        f"Place this exact product in this setting: {scene}. Keep the pouch, its label text and the "
        "ingredients exactly as they are; do not change or add any text. Natural warm light, realistic "
        "photo, vertical composition with the product as the clear focus."
    )
    try:
        return replicate_run(KONTEXT, {"prompt": prompt, "input_image": product["img"],
                                       "aspect_ratio": "2:3", "output_format": "jpg"})
    except Exception as e:  # fall back to the Etsy photo rather than skipping the pin
        print(f"AI image failed, using Etsy photo: {e}")
        return product["img"]


def main():
    token, board = os.environ.get("PINTEREST_ACCESS_TOKEN"), os.environ.get("PINTEREST_BOARD_ID")
    if not token or not board:
        print("Pinterest token or board not set yet (waiting for API approval) - skipping.")
        return 0
    state = load_state()
    product = next_product("pinterest", state)
    copy = pin_copy(product)
    image = pin_image(product, copy.get("scene", "a cozy holiday kitchen"))
    pin = http("POST", f"{API}/pins", headers={"Authorization": f"Bearer {token}"}, body={
        "board_id": board,
        "title": copy["title"][:100],
        "description": copy["description"][:500],
        "alt_text": copy.get("alt_text", "")[:500],
        "link": listing_url(product["id"], "pinterest"),
        "media_source": {"source_type": "image_url", "url": image},
    })
    state.setdefault("pinterest_log", []).append({"pin": pin.get("id"), "listing": product["id"]})
    state["pinterest_log"] = state["pinterest_log"][-50:]
    save_state(state)
    print(f"Pinned {product['id']} -> pin {pin.get('id')}: {copy['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
