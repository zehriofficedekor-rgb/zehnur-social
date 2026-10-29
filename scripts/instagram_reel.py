"""Instagram Reels pipeline.

  python instagram_reel.py generate   -> picks a product, makes a 9:16 scene image (FLUX Kontext),
                                         animates it (Wan 2.2), burns the hook text on screen,
                                         writes caption, saves to docs/reels/ and marks it pending.
  python instagram_reel.py publish    -> publishes the pending Reel to Instagram.

The video is served from GitHub Pages because Instagram must fetch it from a public URL.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request

from common import (BRAND_SYSTEM, ROOT, SHOP_URL, claude_json, download, http, load_state,
                    next_product, replicate_run, require_env, save_state)

GRAPH = "https://graph.facebook.com/" + os.environ.get("GRAPH_VERSION", "v26.0")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "black-forest-labs/flux-kontext-pro")
VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "wan-video/wan-2.2-i2v-fast")
PAGES_BASE = os.environ.get("PAGES_BASE", "https://zehriofficedekor-rgb.github.io/zehnur-social").rstrip("/")
MOTION_MODE = os.environ.get("MOTION_MODE") or "kenburns"   # "kenburns" (safe) or "wan" (AI video)
REELS_DIR = ROOT / "docs" / "reels"
INBOX = ROOT / "docs" / "stories" / "inbox"          # Nuri drops Gemini story clips here
STORIES_FILE = ROOT / "stories.json"                  # episode briefs, in posting order
STORY_DAYS = {int(d) for d in (os.environ.get("STORY_DAYS") or "1,3,5").split(",")}  # Tue, Thu, Sat
FAMILY_STORY = (
    "Our story (true, you may use it): the oranges come from our own family orchard in Antalya on Turkey's "
    "Mediterranean coast; we slice and dry them in the sun. The cinnamon, cloves and star anise come from my "
    "uncle's wholesale spice shop in Eminonu, Istanbul's historic spice bazaar. We blend and pack every kraft "
    "pouch by hand in Istanbul and ship to homes in the US and worldwide through our Etsy shop ZehnurFavors."
)
FONT = os.environ.get("HOOK_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


# ---------------------------------------------------------------- copy
def reel_copy(product):
    return claude_json(
        BRAND_SYSTEM,
        f"""Plan a short silent Instagram Reel (5 seconds, product slowly animated) for this Etsy listing.
Product title: {product['title']}

Return JSON with:
- "hook": on-screen text shown over the video, max 7 words, scroll-stopping, no emojis, no hashtags
  (examples of the style: "Your kitchen will smell like Christmas", "The teacher gift they'll actually use")
- "scene": one sentence describing a cozy realistic vertical scene to place the product in
- "motion": one sentence describing gentle camera/steam motion for the animation (e.g. steam rising from a simmering pot, slow push-in)
- "caption": 300-700 characters. First line repeats the hook idea in a fresh way. Then 2-3 short lines on why it is a great bulk gift (who it is for, personalized labels, minimum 12). End with: "Tap the link in our bio to order 🎄". Use 1-3 emojis total.
- "hashtags": list of 10 relevant hashtags without the # sign, mix of broad and niche (e.g. simmerpot, stovetoppotpourri, teachergifts)""",
    )


# ---------------------------------------------------------------- media
def make_video(product, copy, workdir):
    scene_prompt = (
        f"Place this exact product in this setting: {copy['scene']}. Keep the pouch, its label text and the "
        "ingredients exactly as they are; do not add any text. No people, no hands. Realistic warm photo, "
        "vertical 9:16 framing, product in the lower-middle of the frame, empty space at the top."
    )
    still = replicate_run(IMAGE_MODEL, {"prompt": scene_prompt, "input_image": product["img"],
                                        "aspect_ratio": "9:16", "output_format": "jpg"})
    if MOTION_MODE != "wan":
        # Safe motion: slow push-in on the clean still. No AI-invented hands, label stays sharp.
        img = download(still, workdir / "still.jpg")
        raw = workdir / "raw.mp4"
        frames = 150  # 5 s at 30 fps
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(img),
            "-vf", ("scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                    f"zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)-ih*0.02*on/{frames}':d={frames}:s=720x1280:fps=30"),
            "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(raw)], check=True)
        return still, raw
    clip = replicate_run(VIDEO_MODEL, {"image": still, "prompt": copy["motion"] + " Realistic, subtle, static product. No people, no hands, no text.",
                                       "num_frames": 81, "resolution": "480p", "frames_per_second": 16},
                         max_wait=1200)
    raw = download(clip, workdir / "raw.mp4")
    return still, raw


def has_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def bar_crop(raw):
    """Crop filter that removes black letterbox bars (AI clips sometimes have them), or ''."""
    try:
        r = subprocess.run(["ffmpeg", "-i", str(raw), "-vf", "cropdetect=24:2:0", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        if not crops:
            return ""
        w, h, x, y = max(set(crops), key=crops.count)
        dims = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", str(raw)],
                              capture_output=True, text=True).stdout.strip().split(",")
        W, H = int(dims[0]), int(dims[1])
        if int(w) * int(h) < 0.95 * W * H and int(w) > W * 0.6 and int(h) > H * 0.6:
            print(f"Removing black bars: crop={w}:{h}:{x}:{y}")
            return f"crop={w}:{h}:{x}:{y},"
    except Exception as e:
        print(f"Bar detection skipped ({e})")
    return ""


def burn_hook(raw, hook, out, until=None):
    """Fit to 720x1280 and draw the hook text at the top.
    Keeps the clip's own audio (e.g. Gemini voice-over); adds a silent track if there is none.
    `until` = seconds to show the hook (story clips), None = whole video."""
    # One drawtext per line: multi-line text files render a stray box glyph at each line break.
    lines = textwrap.wrap(hook.upper(), 16)[:4]
    enable = f":enable='lt(t,{until})'" if until else ""
    textfiles, filters = [], [bar_crop(raw) + "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"]
    for i, line in enumerate(lines):
        tf = out.parent / f"{out.stem}_hook{i}.txt"
        tf.write_text(line)
        textfiles.append(tf)
        filters.append(
            f"drawtext=fontfile='{FONT}':textfile='{tf}':fontcolor=white:fontsize=54:"
            f"box=1:boxcolor=black@0.45:boxborderw=14:x=(w-text_w)/2:y=h*0.10+{i * 78}{enable}"
        )
    vf = ",".join(filters)
    audio = has_audio(raw)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw)]
    if not audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True)
    except Exception as e:
        print(f"Hook overlay failed ({e}); using video without text.")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], check=True)
    finally:
        for tf in textfiles:
            tf.unlink(missing_ok=True)


def full_caption(copy):
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in copy.get("hashtags", [])[:12])
    return f"{copy['caption'].strip()}\n\n{tags}"[:2150]


# ---------------------------------------------------------------- preview page
def write_preview_page(state):
    items = []
    pending = state.get("instagram_pending")
    if pending:
        items.append(("Waiting for your OK", pending))
    for p in reversed(state.get("instagram_log", [])[-6:]):
        items.append(("Published", p))
    cards = []
    for label, p in items:
        link = f'<a href="{html.escape(p["permalink"])}">Open on Instagram</a>' if p.get("permalink") else ""
        cards.append(f"""<article><p class="tag">{label} · {html.escape(p['created'])}</p>
<video src="reels/{html.escape(p['file'])}" controls playsinline muted loop></video>
<p class="hook">{html.escape(p['hook'])}</p><pre>{html.escape(p['caption'])}</pre>{link}</article>""")
    body = "\n".join(cards) or "<p>No Reels yet.</p>"
    (ROOT / "docs" / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Zehnur Reels</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:24px auto;padding:0 16px;background:#fffdf9;color:#2b2b2b}}
article{{border:1px solid #e6dccf;border-radius:12px;padding:12px;margin:16px 0;background:#fff}}
video{{width:100%;border-radius:8px;background:#000}}.tag{{font-size:.85em;color:#8a4b1f;margin:0 0 8px}}
.hook{{font-weight:700}}pre{{white-space:pre-wrap;font-family:inherit;font-size:.9em}}</style></head>
<body><h1>Zehnur Reels</h1>{body}</body></html>
""")


# ---------------------------------------------------------------- commands
def inbox_videos():
    """Story clips Nuri uploaded, oldest upload first (git add time; file name as tie-breaker)."""
    if not INBOX.exists():
        return []
    vids = [p for p in INBOX.iterdir() if p.suffix.lower() in (".mp4", ".mov", ".m4v")]

    def added(p):
        r = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "-1", "--", str(p)],
                           capture_output=True, text=True, cwd=ROOT)
        return int(r.stdout.strip() or 0)
    return sorted(vids, key=lambda p: (added(p), p.name))


def story_copy(episode):
    return claude_json(
        BRAND_SYSTEM + " " + FAMILY_STORY,
        f"""Write the Instagram copy for one episode of our Reel series "From Antalya to Your Stove".
Episode brief: {episode.get('brief', 'A behind-the-scenes clip of how our simmer pot kits are made and enjoyed.')}

Return JSON with:
- "hook": on-screen text for the first 3 seconds, max 7 words, curiosity-driven, no emojis
- "caption": 300-700 characters. Tell this episode's moment in a warm first-person-plural voice ("we", "my uncle"),
  connect it to how the kit feels in a customer's home, mention it is a bulk-friendly gift (minimum 12, personalized labels).
  {episode.get('cta', 'End with: "Tap the link in our bio to order 🎄"')}. Use 1-3 emojis. Only state facts given above.
- "hashtags": list of 10 relevant hashtags without the # sign""",
    )


def make_story(state):
    vids = inbox_videos()
    if not vids:
        return False
    episodes = json.loads(STORIES_FILE.read_text())["episodes"] if STORIES_FILE.exists() else []
    idx = state.get("story_index", 0)
    episode = episodes[idx] if idx < len(episodes) else {}
    copy = story_copy(episode)
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"{stamp}-story{idx + 1:02d}.mp4"
    src = vids[0]
    burn_hook(src, copy["hook"], REELS_DIR / name, until=3)
    src.unlink()  # consumed; the committed workflow removes it from the inbox
    state["story_index"] = idx + 1
    state["instagram_pending"] = {
        "file": name, "listing": "story", "episode": episode.get("id", idx + 1), "hook": copy["hook"],
        "caption": full_caption(copy), "created": stamp, "source": src.name,
    }
    write_preview_page(state)
    save_state(state)
    print(f"Story episode {idx + 1} ready from {src.name}: {PAGES_BASE}/reels/{name}")
    return True


def generate():
    require_env("ANTHROPIC_API_KEY", "REPLICATE_API_TOKEN")
    state = load_state()
    if state.get("instagram_pending") and os.environ.get("IG_MODE", "preview") != "auto":
        print("A Reel is still waiting for approval - not generating another one.")
        return 0
    story_day = dt.datetime.now(dt.timezone.utc).weekday() in STORY_DAYS
    if (story_day or os.environ.get("FORCE_STORY") == "1") and make_story(state):
        return 0
    product = next_product("instagram", state)
    copy = reel_copy(product)
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M")
    name = f"{stamp}-{product['id']}.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        still, raw = make_video(product, copy, pathlib.Path(tmp))
        burn_hook(raw, copy["hook"], REELS_DIR / name)
    state["instagram_pending"] = {
        "file": name, "listing": product["id"], "hook": copy["hook"], "caption": full_caption(copy),
        "created": stamp, "still": still,
    }
    write_preview_page(state)
    save_state(state)
    print(f"Reel ready for review: {PAGES_BASE}/reels/{name}")
    print(f"Preview page: {PAGES_BASE}/")
    return 0


def wait_public(url, timeout=600):
    """GitHub Pages needs a minute or two to serve a newly pushed file."""
    waited = 0
    while waited <= timeout:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(15)
        waited += 15
    raise RuntimeError(f"Video is not publicly reachable yet: {url}")


def publish():
    require_env("IG_ACCESS_TOKEN")
    ig_user = os.environ.get("IG_USER_ID") or "17841423562721220"
    token = os.environ["IG_ACCESS_TOKEN"]
    state = load_state()
    pending = state.get("instagram_pending")
    if not pending:
        print("Nothing waiting to publish.")
        return 0
    video_url = f"{PAGES_BASE}/reels/{pending['file']}"
    wait_public(video_url)
    container = http("POST", f"{GRAPH}/{ig_user}/media", form={
        "media_type": "REELS", "video_url": video_url, "caption": pending["caption"],
        "share_to_feed": "true", "access_token": token,
    })["id"]
    for _ in range(60):  # up to ~10 minutes of processing
        status = http("GET", f"{GRAPH}/{container}?fields=status_code,status&access_token={token}")
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process the video: {status}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram processing timed out")
    media_id = http("POST", f"{GRAPH}/{ig_user}/media_publish",
                    form={"creation_id": container, "access_token": token})["id"]
    permalink = http("GET", f"{GRAPH}/{media_id}?fields=permalink&access_token={token}").get("permalink", "")
    pending.update({"media_id": media_id, "permalink": permalink})
    state.setdefault("instagram_log", []).append(pending)
    state["instagram_log"] = state["instagram_log"][-50:]
    state["instagram_pending"] = None
    write_preview_page(state)
    save_state(state)
    print(f"Published Reel: {permalink or media_id}")
    return 0


def discard():
    """Drop the pending Reel (use when you don't like it); the next run makes a new one."""
    state = load_state()
    pending = state.get("instagram_pending")
    if pending:
        (REELS_DIR / pending["file"]).unlink(missing_ok=True)
        state["instagram_pending"] = None
        write_preview_page(state)
        save_state(state)
        print(f"Discarded {pending['file']}")
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "generate"
    sys.exit({"generate": generate, "publish": publish, "discard": discard}[action]())
