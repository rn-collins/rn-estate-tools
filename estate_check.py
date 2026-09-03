#!/usr/bin/env python3
"""
estate-check — the ship gate for an RN Collins build.

A build is "pristine" only when this exits 0. Every claim about a build's
quality should come from this script, not from anyone's assurance.

Usage:
    python3 estate_check.py https://example.vercel.app
    python3 estate_check.py https://example.vercel.app --json report.json
    python3 estate_check.py --batch urls.txt --json estate.json

Checks, grouped:
  FUNCTION  every route 200 · no href="#" · internal links resolve ·
            external links resolve (bot-blocks reported separately) ·
            in-page anchors point at ids that exist
  PRESENT   title · meta description · canonical · favicon (incl. data: URI
            and implicit /favicon.ico) · og:image (and it loads) · og:title ·
            apple-touch-icon
  SUBSTANCE visible word count vs floor · caveat/hedge density ceiling
  ACCESS    heading order · img alt · form labels · lang attribute

Exit status: 0 = pass, 1 = fail, 2 = could not evaluate.
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import urllib.request
import urllib.error

UA = "Mozilla/5.0 (compatible; estate-check/1.0; +https://github.com/rn-collins)"
# Some hosts refuse a non-browser client and say 404 rather than 403 — accessdata
# .fda.gov does it even for its own root. A 404 from one client is therefore not
# proof of anything, and acting on it would delete a working citation.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
TIMEOUT = 25

# Thresholds. Raise deliberately; never lower to make a build pass.
MIN_WORDS = 400          # below this a page is a stub, not a product
MAX_HEDGE_PCT = 30       # above this it reads as an internal audit memo

# Legal and policy pages are supposed to be full of qualifications — that is the
# genre working correctly, not an audit memo. Applying the prose ceiling to them
# pushes toward deleting caveats from a privacy policy or a terms page, which is
# the opposite of the point. They are exempt from the hedge ceiling and from the
# word floor; a short, plain privacy page is a good privacy page.
POLICY_ROUTE = re.compile(
    r"/(privacy|terms|legal|licen[cs]e|disclaimer|cookies?|accessibility|"
    r"institutional-disclaimer|dmca|imprint)(\.html)?/?$", re.I)

HEDGE = re.compile(
    r"\b(not (?:a|an|intended|vetting|endorsement|legal advice|medical advice|validated|approved)"
    r"|does not|do not|cannot|unvalidated|no warranty|disclaimer|caveat"
    r"|exploratory|prototype|demonstration|illustrative|not established"
    r"|withheld|provisional|preliminary|for informational purposes"
    r"|does not constitute|nothing here)\b",
    re.I,
)


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9's urllib does not follow 308 (added in 3.11). Vercel's
    cleanUrls emits 308 constantly, so without this every clean-URL route
    is misreported as a dead route."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)


_OPENER = urllib.request.build_opener(_Redirect308)


def fetch(url, method="GET", _retry=True, ua=UA):
    """Return (status, body_text, final_url, content_type). status 0 = transport failure."""
    req = urllib.request.Request(url, headers={"User-Agent": ua}, method=method)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            body = b""
            if method == "GET":
                body = r.read(3_000_000)
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return r.status, body.decode("utf-8", "replace"), r.geturl(), ctype
    except urllib.error.HTTPError as e:
        return e.code, "", url, ""
    except Exception:
        # One retry: server-rendered pages behind a pooled DB can exceed the
        # timeout under concurrency, and a single miss must not read as "dead".
        if _retry:
            time.sleep(1.5)
            return fetch(url, method, _retry=False, ua=ua)
        return 0, "", url, ""


def status_only(url):
    st, _, _, _ = fetch(url, "HEAD")
    # Some hosts answer HEAD with 404 for a document that GET serves at 200 —
    # nvlpubs.nist.gov does exactly this for the AI RMF PDFs. A 404 therefore
    # cannot be treated as definitive either, or the gate invents dead links.
    if st in (0, 403, 404, 405, 501):
        st, _, _, _ = fetch(url, "GET")
    return st


class Extract(HTMLParser):
    """Pulls the structure the gate needs in a single pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.ids, self.headings = [], set(), []
        self.imgs, self.inputs, self.labels = [], [], []
        self.meta, self.linkrel = {}, {}
        self.title, self.lang = "", ""
        self._in_title = False
        self._skip = 0
        self._in_label = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("name") and tag == "a":
            self.ids.add(a["name"])
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self.links.append(a.get("href", ""))
        elif tag == "img":
            self.imgs.append(a)
        elif tag in ("input", "select", "textarea"):
            # A control nested inside <label> is labelled implicitly. That is
            # valid HTML and correctly exposed to assistive tech; checking only
            # for/aria-label reported 111 correctly-labelled controls on one
            # build as unlabelled.
            self.inputs.append({**a, "_implicit_label": self._in_label > 0})
        elif tag == "label":
            self.labels.append(a)
            self._in_label += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if rel:
                self.linkrel[rel] = a.get("href", "")

    def handle_endtag(self, tag):
        if tag == "label" and self._in_label:
            self._in_label -= 1
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        elif not self._skip:
            self.text.append(d)

    @property
    def visible(self):
        return re.sub(r"\s+", " ", " ".join(self.text)).strip()


def icon_href(ex):
    for rel, href in ex.linkrel.items():
        if "icon" in rel and "apple" not in rel:
            return href
    return None


def check_page(base, url, deep=True):
    """Evaluate one page. Returns a dict of findings."""
    st, html, final, ctype = fetch(url)
    # status 0 is a transport failure, which is a statement about this run and
    # not about the route. Concurrent sweeps against one host produced five of
    # them on a site that answers every route in 0.4s, and they were reported as
    # blockers — the most severe class the tool has. Escalate the effort before
    # believing it: a genuinely unreachable route fails all three attempts, a
    # loaded one succeeds on a longer timeout.
    if st == 0:
        for extra in (2, 4):
            time.sleep(1.5 * extra)
            global TIMEOUT
            _saved, TIMEOUT = TIMEOUT, TIMEOUT * extra
            try:
                st, html, final, ctype = fetch(url, _retry=False)
            finally:
                TIMEOUT = _saved
            if st:
                break
    if st != 200 or not html:
        why = (f"route returns HTTP {st}" if st else
               "route did not respond on three attempts with escalating timeouts")
        return {"url": url, "status": st, "fatal": True, "skipped": False,
                "findings": [("blocker", why)]}
    # Only HTML routes are graded as pages; data/assets are reachability-only.
    if ctype and ctype not in ("text/html", "application/xhtml+xml"):
        return {"url": url, "status": st, "fatal": False, "skipped": True,
                "findings": [], "note": f"not a page ({ctype})"}

    ex = Extract()
    try:
        ex.feed(html)
    except Exception:
        pass

    f = []

    # ---- PRESENT ---------------------------------------------------------
    if not ex.title.strip():
        f.append(("major", "no <title>"))
    if not ex.meta.get("description"):
        f.append(("major", "no meta description"))
    if "canonical" not in ex.linkrel:
        f.append(("minor", "no canonical URL"))

    ic = icon_href(ex)
    if not ic:
        if status_only(urljoin(base, "/favicon.ico")) != 200 and status_only(urljoin(base, "/favicon.svg")) != 200:
            f.append(("major", "no favicon (no <link rel=icon> and no /favicon.*)"))
    elif not ic.startswith("data:"):
        if status_only(urljoin(final, ic)) != 200:
            f.append(("major", f"favicon link is broken: {ic}"))

    og = ex.meta.get("og:image")
    if not og:
        f.append(("major", "no og:image — link previews will render blank"))
    elif status_only(urljoin(final, og)) != 200:
        f.append(("major", f"og:image does not load: {og}"))
    if not ex.meta.get("og:title"):
        f.append(("minor", "no og:title"))
    if not any("apple-touch" in r for r in ex.linkrel):
        f.append(("minor", "no apple-touch-icon"))

    # ---- SUBSTANCE -------------------------------------------------------
    words = ex.visible.split()
    sents = [s for s in re.split(r"(?<=[.!?])\s+", ex.visible) if len(s.split()) > 3]
    hedged = [s for s in sents if HEDGE.search(s)]
    pct = round(100 * len(hedged) / len(sents)) if sents else 0
    policy = bool(POLICY_ROUTE.search(urlparse(final).path or "/"))
    if len(words) < MIN_WORDS and not policy:
        f.append(("major", f"stub: {len(words)} visible words (floor {MIN_WORDS})"))
    if pct > MAX_HEDGE_PCT and not policy:
        f.append(("major", f"reads as an audit memo: {pct}% of sentences are caveats (ceiling {MAX_HEDGE_PCT}%)"))

    # ---- ACCESS ----------------------------------------------------------
    if not ex.lang:
        f.append(("minor", "no lang attribute on <html>"))
    noalt = [i for i in ex.imgs if i.get("alt") is None and not i.get("aria-hidden") and not i.get("role") == "presentation"]
    if noalt:
        f.append(("major", f"{len(noalt)} image(s) with no alt attribute"))
    if ex.headings:
        if ex.headings[0] != 1:
            f.append(("minor", f"first heading is h{ex.headings[0]}, not h1"))
        for a, b in zip(ex.headings, ex.headings[1:]):
            if b - a > 1:
                f.append(("minor", f"heading level jumps h{a} to h{b}"))
                break
    labelled = {l.get("for") for l in ex.labels if l.get("for")}
    unlabelled = [
        i for i in ex.inputs
        if i.get("type") not in ("hidden", "submit", "button", "image", "reset")
        and not i.get("aria-label") and not i.get("aria-labelledby")
        and not i.get("_implicit_label")
        and not (i.get("id") and i["id"] in labelled)
    ]
    if unlabelled:
        f.append(("major", f"{len(unlabelled)} form control(s) with no label"))

    # ---- FUNCTION --------------------------------------------------------
    dead_hash = sum(1 for h in ex.links if h.strip() in ("#", ""))
    if dead_hash:
        f.append(("blocker", f"{dead_hash} link(s) with href=\"#\" or empty — they navigate nowhere"))

    anchors = [h[1:] for h in ex.links if h.startswith("#") and len(h) > 1]
    broken_anchor = sorted({a for a in anchors if a not in ex.ids})
    if broken_anchor:
        f.append(("major", f"{len(broken_anchor)} anchor(s) target a missing id: {', '.join(broken_anchor[:6])}"))

    internal, external = set(), set()
    for h in ex.links:
        h = h.strip()
        if not h or h.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        u = urljoin(final, h).split("#")[0]
        # RFC 2606 / RFC 6761 reserve these for documentation and examples. They
        # are supposed not to resolve, so grading them as citations reports a
        # correct placeholder as a dead source — rn-portfolio uses example.org
        # for demo records it labels "Synthetic" on the page.
        host = urlparse(u).netloc.lower().split(":")[0]
        if host in ("example.com", "example.org", "example.net", "example.edu") \
           or host.endswith((".example", ".invalid", ".test", ".localhost")):
            continue
        (internal if host == urlparse(base).netloc else external).add(u)

    if deep:
        # The cap exists so one link-farm page cannot stall a whole run. It must
        # never be silent: truncating alphabetically and reporting "0 dead
        # links" is how a page with 411 links passed while three of them 404'd.
        for label, urls, cap in (("internal", internal, 600), ("external", external, 600)):
            total = len(urls)
            urls = sorted(urls)[:cap]
            if not urls:
                continue
            if total > cap:
                f.append(("major",
                          f"{total} {label} links exceed the {cap} checked here — "
                          f"{total - cap} were NOT verified. This page's link "
                          f"count is not a clean result."))
            with ThreadPoolExecutor(max_workers=6) as ex_:
                codes = list(ex_.map(status_only, urls))
            bad = [(u, c) for u, c in zip(urls, codes) if c not in (200, 301, 302, 303, 307, 308)]
            # Confirm every hard-dead verdict with a second, independent request.
            # A WAF can answer 404 once and 403 the next second: publiccounsel.net
            # did exactly that, and the gate reported the Massachusetts public
            # defender's real homepage as a dead link. Calling something dead is
            # the finding most likely to make someone delete a correct link, so
            # it is the one that must never rest on a single observation.
            hard = []
            for u, c in bad:
                if c not in (404, 410):
                    continue
                time.sleep(0.4)
                if status_only(u) not in (404, 410):
                    continue
                # Second opinion from a browser UA before anything is called dead.
                st2, _, _, _ = fetch(u, "GET", ua=BROWSER_UA)
                if st2 in (200, 301, 302, 303, 307, 308):
                    continue
                hard.append((u, c))
            confirmed_dead = {u for u, _ in hard}
            bad = [(u, c) for u, c in bad if u not in confirmed_dead or c in (404, 410)]
            soft = [(u, c) for u, c in bad if u not in confirmed_dead]
            if hard:
                sev = "blocker" if label == "internal" else "major"
                f.append((sev, f"{len(hard)} dead {label} link(s): " + "; ".join(f"{c} {u}" for u, c in hard[:5])))
            if soft:
                f.append(("minor", f"{len(soft)} {label} link(s) unverifiable (bot-block/timeout): " + "; ".join(f"{c} {u}" for u, c in soft[:3])))

    return {
        "url": url, "status": st, "fatal": False, "findings": f,
        "words": len(words), "hedge_pct": pct,
        "links": len(ex.links), "internal": len(internal), "external": len(external),
    }


def discover(base, limit=200):
    """Homepage links + sitemap.xml, same-origin only.

    Returns (urls, total_found). The cap must never be silent: sampling 40
    routes alphabetically out of 572 and printing PASS is how a site can be
    called clean on 7% of itself."""
    seen = {base.rstrip("/") or base}
    st, html, final, _ = fetch(base)
    if st == 200:
        ex = Extract()
        try:
            ex.feed(html)
        except Exception:
            pass
        for h in ex.links:
            h = h.strip()
            if not h or h.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            u = urljoin(final, h).split("#")[0].rstrip("/")
            if urlparse(u).netloc == urlparse(base).netloc:
                seen.add(u)
    st, xml, _, _ = fetch(urljoin(base, "/sitemap.xml"))
    if st == 200:
        for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml):
            if urlparse(m).netloc == urlparse(base).netloc:
                seen.add(m.split("#")[0].rstrip("/"))
    allu = sorted(seen)
    return allu[:limit], len(allu)


def run(base, deep=True, routes=None):
    t0 = time.time()
    if routes:
        urls, found = routes, len(routes)
    else:
        urls, found = discover(base)
    pages = [check_page(base, u, deep=deep) for u in urls]
    if found > len(urls):
        pages.append({
            "url": base, "status": 200, "fatal": False, "skipped": False,
            "findings": [("major",
                          f"{found} same-origin routes discovered but only {len(urls)} "
                          f"checked — {found - len(urls)} were NOT graded. This site's "
                          f"result does not describe the whole site.")],
            "words": 0, "hedge_pct": 0, "links": 0, "internal": 0, "external": 0,
        })
    graded = [p for p in pages if not p.get("skipped")]
    counts = Counter()
    for p in graded:
        for sev, _ in p["findings"]:
            counts[sev] += 1
    return {
        "site": base,
        "routes_checked": len(graded),
        "assets_skipped": len(pages) - len(graded),
        "blocker": counts["blocker"], "major": counts["major"], "minor": counts["minor"],
        "passed": counts["blocker"] == 0 and counts["major"] == 0,
        "elapsed_s": round(time.time() - t0, 1),
        "pages": pages,
    }


COLOR = {"blocker": "\033[91m", "major": "\033[93m", "minor": "\033[90m"}
RESET = "\033[0m"


def render(rep, verbose=True):
    ok = rep["passed"]
    print(f"\n{'PASS' if ok else 'FAIL'}  {rep['site']}")
    print(f"      {rep['routes_checked']} routes · {rep['blocker']} blocker · {rep['major']} major · {rep['minor']} minor · {rep['elapsed_s']}s")
    if not verbose:
        return
    for p in rep["pages"]:
        if not p["findings"]:
            continue
        path = urlparse(p["url"]).path or "/"
        print(f"\n  {path}")
        for sev, msg in sorted(p["findings"], key=lambda x: ("blocker", "major", "minor").index(x[0])):
            print(f"    {COLOR[sev]}{sev:<8}{RESET} {msg}")


def main():
    ap = argparse.ArgumentParser(description="Ship gate for an RN Collins build.")
    ap.add_argument("url", nargs="?", help="site root, e.g. https://example.vercel.app")
    ap.add_argument("--batch", help="file with one URL per line")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--shallow", action="store_true", help="skip link resolution (fast)")
    ap.add_argument("--quiet", action="store_true", help="summary lines only")
    a = ap.parse_args()

    targets = []
    if a.batch:
        targets = [l.strip() for l in open(a.batch) if l.strip() and not l.startswith("#")]
    elif a.url:
        targets = [a.url]
    else:
        ap.error("give a URL or --batch")

    reports = [run(t, deep=not a.shallow) for t in targets]
    for r in reports:
        render(r, verbose=not a.quiet)

    if a.json:
        with open(a.json, "w") as fh:
            json.dump(reports if len(reports) > 1 else reports[0], fh, indent=1)
        print(f"\nreport written to {a.json}")

    failed = sum(0 if r["passed"] else 1 for r in reports)
    if failed:
        print(f"\n{failed} of {len(reports)} build(s) failed the gate.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
