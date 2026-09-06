#!/usr/bin/env python3
"""
og-card — build a 1200x630 Open Graph image that looks like the site it belongs to.

The ship gate flags a missing og:image as a major finding, and the estate's
existing cards are all 1200x630 PNGs named og-image.png. There was no generator:
each had been made by hand. This makes one from a build's own palette, so the
share card is branded to that build rather than a generic template — which is
the whole point, since eleven builds sharing one card is the same failure as
eleven builds sharing one typeface.

Renders through headless Chrome, into an isolated --user-data-dir so it never
touches the user's running browser or profile.

Usage:
    python3 og_card.py spec.json out.png

spec.json keys: title (required), kicker, meta, bg, panel, ink, muted,
                accent, accent2, font
"""

import json
import subprocess
import time
import sys
import tempfile
from html import escape
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

TEMPLATE = """<!doctype html><meta charset="utf-8">{fontcss}<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1200px;height:630px}}
body{{background:{bg};font-family:{font};display:flex;
 align-items:center;justify-content:center}}
.card{{width:1104px;height:534px;background:{panel};border:1px solid {line};
 border-radius:22px;padding:70px 76px;display:flex;flex-direction:column;
 justify-content:space-between;position:relative;overflow:hidden}}
.glow{{position:absolute;width:620px;height:620px;right:-230px;top:-300px;
 border-radius:50%;background:radial-gradient(circle,{accent}2e 0%,transparent 68%)}}
.kicker{{font-size:23px;letter-spacing:.20em;text-transform:uppercase;
 color:{accent};font-weight:700}}
h1{{font-size:{size}px;line-height:1.05;color:{ink};font-weight:800;
 letter-spacing:-.022em;max-width:22ch}}
.rule{{height:5px;width:132px;background:{accent};border-radius:3px;margin:30px 0 26px}}
.meta{{font-size:25px;color:{muted};line-height:1.45;max-width:46ch}}
.foot{{display:flex;align-items:center;gap:14px;font-size:21px;color:{muted}}}
.dot{{width:11px;height:11px;border-radius:50%;background:{accent2}}}
</style><div class="card"><div class="glow"></div>
<div><div class="kicker">{kicker}</div><div class="rule"></div>
<h1>{title}</h1></div>
<div><p class="meta">{meta}</p>
<div class="foot" style="margin-top:26px"><span class="dot"></span><span>{foot}</span></div></div>
</div>"""


def build(spec, out):
    title = spec["title"]
    # Long titles have to shrink or they overflow the card; the sizes are the
    # three that survived visual checks at 1200x630.
    size = 84 if len(title) <= 34 else 70 if len(title) <= 58 else 58
    # A build whose voice is a webfont has to load it here too, or the card
    # renders in a system fallback and stops looking like the site it fronts.
    fc = spec.get("fontcss", "")
    html = TEMPLATE.format(
        fontcss=f'<link rel="stylesheet" href="{fc}">' if fc else "",
        bg=spec.get("bg", "#0b0b0c"),
        panel=spec.get("panel", "#141416"),
        line=spec.get("line", "#2a2a2e"),
        ink=spec.get("ink", "#f5f5f4"),
        muted=spec.get("muted", "#a8a8a5"),
        accent=spec.get("accent", "#7cf0bd"),
        accent2=spec.get("accent2", spec.get("accent", "#7cf0bd")),
        font=spec.get("font", "system-ui,-apple-system,sans-serif"),
        size=size,
        kicker=escape(spec.get("kicker", "")),
        title=escape(title),
        meta=escape(spec.get("meta", "")),
        foot=escape(spec.get("foot", "")),
    )
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", "--window-size=1200,630",
             f"--user-data-dir={tmp}/profile", f"--screenshot={out}", page.as_uri()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Chrome writes the screenshot and then, in new-headless, does not
        # always exit. Wait for the file rather than for the process, and stop
        # only this child — never a name-matched kill, which once took down the
        # user's real browser.
        target = Path(out)
        for _ in range(600):
            if proc.poll() is not None:
                break
            if target.exists() and target.stat().st_size > 0:
                time.sleep(0.4)   # let the write finish
                break
            time.sleep(0.1)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        err = proc.stderr.read()[-800:] if proc.stderr else ""
    if not Path(out).exists():
        raise SystemExit(f"chrome produced no image\n{err}")
    print(f"wrote {out} ({Path(out).stat().st_size} bytes)")


if __name__ == "__main__":
    build(json.load(open(sys.argv[1])), sys.argv[2])
