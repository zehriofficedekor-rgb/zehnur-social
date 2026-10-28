"""Shared helpers: HTTP, Claude, Replicate, product rotation, state."""
import datetime as dt
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state.json"
PRODUCTS_FILE = ROOT / "content_bank.json"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
SHOP_URL = "https://www.etsy.com/shop/ZehnurFavors"


# ---------------------------------------------------------------- HTTP
def http(method, url, headers=None, body=None, form=None, timeout=120):
    """Small JSON HTTP client on the standard library. Returns parsed JSON (or text)."""
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:1500]
        raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def download(url, path, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "zehnur-social/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"Missing required secret(s): {', '.join(missing)}")


# ---------------------------------------------------------------- Claude
def claude_json(system, prompt, max_tokens=1200):
    """Ask Claude for a JSON object and return it as a dict."""
    require_env("ANTHROPIC_API_KEY")
    res = http(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        body={
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system + "\nReply with ONE valid JSON object only. No markdown, no commentary.",
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"Claude did not return JSON: {text[:300]}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------- Replicate
def replicate_run(model, inputs, max_wait=900):
    """Run an official Replicate model and return its output (usually a URL)."""
    require_env("REPLICATE_API_TOKEN")
    headers = {"Authorization": f"Bearer {os.environ['REPLICATE_API_TOKEN']}", "Prefer": "wait=60"}
    pred = http("POST", f"https://api.replicate.com/v1/models/{model}/predictions",
                headers=headers, body={"input": inputs})
    waited = 0
    while pred.get("status") not in ("succeeded", "failed", "canceled"):
        if waited > max_wait:
            raise RuntimeError(f"Replicate {model} timed out")
        time.sleep(5)
        waited += 5
        pred = http("GET", pred["urls"]["get"], headers={"Authorization": headers["Authorization"]})
    if pred["status"] != "succeeded":
        raise RuntimeError(f"Replicate {model} {pred['status']}: {pred.get('error')}")
    out = pred["output"]
    return out[0] if isinstance(out, list) else out


# ---------------------------------------------------------------- products & state
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def season_ok(title, today=None):
    """Skip products that are out of season."""
    t = title.lower()
    today = today or dt.date.today()
    md = (today.month, today.day)
    autumn = any(k in t for k in ("fall", "thanksgiving", "pumpkin", "autumn"))
    new_year = "new year" in t or "january" in t
    if autumn and md > (11, 27):          # after Thanksgiving week
        return False
    if new_year and (10, 1) <= md <= (12, 15):  # too early for New Year items
        return False
    return True


def next_product(channel, state, today=None):
    products = [p for p in json.loads(PRODUCTS_FILE.read_text())["products"] if season_ok(p["title"], today)]
    idx = state.get(f"{channel}_index", 0) % len(products)
    state[f"{channel}_index"] = idx + 1
    return products[idx]


def listing_url(product_id, source):
    return f"https://www.etsy.com/listing/{product_id}?utm_source={source}&utm_medium=social&utm_campaign=zehnur_auto"


BRAND_SYSTEM = (
    "You write social media copy for ZehnurFavors, a small Etsy shop in Istanbul that hand-makes "
    "Christmas simmer pot kits (stovetop potpourri: dried orange slices, cinnamon, cloves, star anise, "
    "cranberries, pine) in kraft window pouches with personalized labels. Buyers are mostly in the US: "
    "people buying bulk gifts for teachers, coworkers, clients, wedding guests and holiday hostesses. "
    "Minimum order is 12 pouches; personalization is available; ships DDP worldwide. "
    "Voice: warm, cozy, specific, never pushy. Never invent prices, discounts, reviews or delivery dates. "
    "Write in natural US English."
)
