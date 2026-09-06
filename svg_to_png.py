#!/usr/bin/env python3
"""
svg-to-png — render an SVG to a square PNG at a given size, on a solid ground.

Written for apple-touch-icon, which the ship gate reports as a minor on every
build that lacks one. iOS applies its own rounded mask, so an icon whose corners
are transparent (as every favicon.svg in this estate is, because they carry
their own rx) composites against whatever the OS puts behind it. Rendering
full-bleed on the brand colour avoids that.

No rsvg/cairo on this machine; headless Chrome is the available renderer, run
in an isolated profile so it never touches the user's browser.

Usage:
    python3 svg_to_png.py icon.svg out.png --size 180 --bg '#07120f'
"""

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def render(svg_path, out, size, bg, inset):
    svg = Path(svg_path).read_text(encoding="utf-8")
    pad = round(size * inset)
    html = (
        "<!doctype html><meta charset='utf-8'><style>"
        f"html,body{{margin:0;padding:0;width:{size}px;height:{size}px;overflow:hidden}}"
        f"body{{background:{bg};display:flex;align-items:center;justify-content:center}}"
        f"svg{{width:{size - 2 * pad}px;height:{size - 2 * pad}px;display:block}}"
        "</style>" + svg
    )
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={size},{size}",
             f"--user-data-dir={tmp}/profile", f"--screenshot={out}", page.as_uri()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        target = Path(out)
        for _ in range(600):
            if proc.poll() is not None:
                break
            if target.exists() and target.stat().st_size > 0:
                time.sleep(0.4)
                break
            time.sleep(0.1)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        err = proc.stderr.read()[-800:] if proc.stderr else ""
    if not target.exists():
        raise SystemExit(f"chrome produced no image\n{err}")
    print(f"wrote {out} ({target.stat().st_size} bytes, {size}x{size})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("svg"); ap.add_argument("out")
    ap.add_argument("--size", type=int, default=180)
    ap.add_argument("--bg", default="#ffffff")
    # the glyph is inset slightly because iOS crops to a rounded mask and a
    # full-bleed mark loses its corners
    ap.add_argument("--inset", type=float, default=0.0)
    a = ap.parse_args()
    render(a.svg, a.out, a.size, a.bg, a.inset)
