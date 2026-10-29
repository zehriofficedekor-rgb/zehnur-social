"""Generate story-episode clips with Google Veo on Replicate (instead of Nuri making them in Gemini).

Clips land in docs/stories/inbox/epNN.mp4, where the daily Instagram run picks them up
on story days (Tue/Thu/Sat) exactly like clips Nuri uploads by hand.

Usage: python scripts/story_video.py [episodes]
  episodes: "" = next episode not yet made, "3" = next three, "1,2,3" = those ids.
Veo 3.1 Fast costs about $0.15 per second, so one 8-second clip is about $1.20.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_state, replicate_run, save_state  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "docs" / "stories" / "inbox"
STORIES = json.loads((ROOT / "stories.json").read_text())
MODELS = [m for m in (os.environ.get("VEO_MODEL") or "google/veo-3.1-fast,google/veo-3-fast").split(",") if m]
MAX_PER_RUN = 4  # cost guard: at most ~$5 per run


def pick(arg, made):
    eps = [e for e in STORIES["episodes"] if e.get("gemini_prompt")]
    arg = (arg or "").strip()
    if "," in arg or (arg.isdigit() and int(arg) > MAX_PER_RUN):
        ids = {int(x) for x in arg.split(",") if x.strip()}
        return [e for e in eps if e["id"] in ids]
    n = int(arg) if arg.isdigit() else 1
    return [e for e in eps if e["id"] not in made][:n]


def make(ep):
    inputs = {"prompt": ep["gemini_prompt"], "aspect_ratio": "9:16", "duration": 8,
              "resolution": "720p", "generate_audio": True}
    last = None
    for model in MODELS:
        try:
            print(f"Episode {ep['id']}: generating with {model}")
            return replicate_run(model, inputs, max_wait=900)
        except RuntimeError as e:
            print(f"  {model} failed: {e}")
            last = e
    raise last


def main():
    state = load_state()
    made = set(state.get("story_videos_made", []))
    todo = pick(sys.argv[1] if len(sys.argv) > 1 else "", made)[:MAX_PER_RUN]
    if not todo:
        print("No episode with a ready prompt is left to generate. Add gemini_prompt to the next episodes in stories.json.")
        return
    INBOX.mkdir(parents=True, exist_ok=True)
    for ep in todo:
        url = make(ep)
        out = INBOX / f"ep{ep['id']:02d}.mp4"
        with urllib.request.urlopen(url, timeout=300) as r:
            out.write_bytes(r.read())
        print(f"  saved {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
        made.add(ep["id"])
        state["story_videos_made"] = sorted(made)
        save_state(state)  # saved after each clip so a later failure does not redo paid work


if __name__ == "__main__":
    main()
