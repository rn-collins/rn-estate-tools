#!/usr/bin/env python3
"""
design-survey — does every build look like itself?

The ship gate can tell you a link resolves and a page has words. It cannot tell
you that eleven builds are the same page with a different accent colour. This
does that one job: it reads each site's real typeface and palette and reports
which builds share a signature.

Why it exists: a survey of 67 live builds found 32 on Georgia and 28 on
system-ui, and seven unrelated builds rendering as the same cream-paper,
Georgia-headline, rounded-card page. Each looked fine alone. The collision was
only visible across the estate.

WHAT THIS IS NOT: a judgement of quality. It flagged rn-portfolio as "Arial, no
choice made" when Arial Black at 153px against mono and acid green is a
deliberate Swiss idiom and one of the best things in the estate; and it missed
bm-intel entirely, which was create-next-app's untouched default. Use it to
narrow where to look. Then look.

Usage:
    python3 design_survey.py --batch urls.txt
    python3 design_survey.py --batch urls.txt --json report.json
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
import urllib.request

UA = "Mozilla/5.0 (compatible; design-survey/1.0; +https://github.com/rn-collins)"
TIMEOUT = 45

# Values that are a fallback rather than a decision. A build resting only on
# these has not chosen a typeface; it has accepted whatever the browser had.
DEFAULTISH = {"system-ui", "-apple-system", "arial", "helvetica", "sans-serif",
              "serif", "ui-sans-serif", "ui-serif", "ui-monospace", "monospace",
              "segoe ui", "times new roman"}

# font-family, plus the `font:` shorthand, which a first pass missed entirely
# and which is where nsag-m1 was hiding its Georgia.
FAM = re.compile(r'font-family\s*:\s*([^;}"]+)|font\s*:\s*(?:[\w.%/-]+\s+){1,4}([A-Za-z"][^;}]*)')
JUNK = ("clamp(", "calc(", "min(", "max(", "var(", "inherit", "initial", "unset")


def _get(url, cap=1_200_000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=TIMEOUT).read(cap).decode("utf-8", "replace")
    except Exception:
        return ""


def _norm(v):
    if not v:
        return None
    f = v.split(",")[0].strip().strip("\"'").lower()
    if not f or f.startswith(JUNK) or f[0].isdigit() or f.startswith("."):
        return None
    return f


def signature(url):
    """Typefaces and dominant palette for one site, following its stylesheets."""
    html = _get(url)
    if not html:
        return None
    css = " ".join(m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.S))
    for m in re.finditer(r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*>', html):
        href = re.search(r'href=["\']([^"\']+)', m.group(0))
        if href and "fonts.googleapis" not in href.group(1):
            css += " " + _get(urljoin(url, href.group(1)), 700_000)

    fams = {f for m in FAM.finditer(css) if (f := _norm(m.group(1) or m.group(2)))}
    google = set()
    for m in re.finditer(r'fonts\.googleapis\.com/css2?\?family=([^&"\'>]+)', html + css):
        google.update(x.split(":")[0].replace("+", " ").lower()
                      for x in m.group(1).split("&family="))

    chosen = sorted((fams | google) - DEFAULTISH)
    # Keep the unfiltered list too. Filtering Arial as a fallback is right for
    # a build that never chose, and wrong for rn-portfolio, which sets Arial
    # Black at 153px on purpose. Never hide the raw evidence behind the verdict.
    raw = sorted(fams | google)
    palette = [c for c, _ in Counter(c.lower() for c in re.findall(r"#([0-9a-fA-F]{6})\b", css)).most_common(5)]
    return {
        "site": url.replace("https://", "").replace(".vercel.app", ""),
        "url": url,
        "typefaces": chosen,
        "typefaces_raw": raw,
        "fallback_only": not chosen,
        "palette": palette,
    }


def survey(urls, workers=8):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [r for r in ex.map(signature, urls) if r]


def report(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r["typefaces"])].append(r["site"])

    shared = {k: v for k, v in groups.items() if len(v) > 1}
    unique = [v[0] for k, v in groups.items() if len(v) == 1]
    fallback = [r["site"] for r in rows if r["fallback_only"]]

    print(f"\n{len(rows)} builds surveyed\n")
    print(f"  own signature      {len(unique)}")
    print(f"  sharing one        {len(rows) - len(unique)}")
    print(f"  no typeface chosen {len(fallback)}")

    if shared:
        print("\nSHARED SIGNATURES — these builds look like each other:")
        for k, v in sorted(shared.items(), key=lambda kv: -len(kv[1])):
            label = ", ".join(k) if k else "(browser default only)"
            print(f"\n  {len(v)} builds :: {label}")
            for s in sorted(v):
                print(f"      {s}")

    # Paper is the other half of the tell: four builds shared a cream within a
    # couple of hex points of each other while using different accent hues.
    papers = Counter()
    for r in rows:
        for c in r["palette"][:3]:
            v = int(c[:2], 16) + int(c[2:4], 16) + int(c[4:], 16)
            if v > 690:
                papers[c] += 1
    near = {c: n for c, n in papers.items() if n > 1}
    if near:
        print("\nSHARED LIGHT GROUNDS — the same paper under different accents:")
        for c, n in sorted(near.items(), key=lambda kv: -kv[1]):
            print(f"  #{c}  on {n} builds")

    return 1 if shared else 0


def main():
    ap = argparse.ArgumentParser(description="Does every build look like itself?")
    ap.add_argument("--batch", required=True, help="file with one URL per line")
    ap.add_argument("--json", help="write the full survey here")
    a = ap.parse_args()

    urls = [l.strip() for l in open(a.batch) if l.strip()]
    rows = survey(urls)
    if a.json:
        json.dump(rows, open(a.json, "w"), indent=1)
    sys.exit(report(rows))


if __name__ == "__main__":
    main()
