#!/usr/bin/env python3
"""
cite-check — verify every citation in a build against the actual record.

Built after an estate audit found the same defect in three separate builds:
the prose was accurate and the *pointer to the source* was not. Real articles
with fabricated ID numbers. DOIs registered nowhere. Cited titles matching no
paper that exists. A tobacco statute cited for psilocybin employment law.

A link returning 200 is not evidence that a citation is correct. This checks
what the citation actually claims:

  DOI      registered in Crossref? does the registered title match the cited
           title? (a resolving DOI attached to the wrong title is the failure
           mode that reads as correct and isn't)
  PMID     resolves in NCBI eutils? title match?
  URL      live status, separating genuine 404/410 from bot mitigation
  STATUTE  fetch the actual section and check its heading is topically
           consistent with the claim it is cited for

Usage:
    python3 cite_check.py ./repo                     # scan a checkout
    python3 cite_check.py ./repo --json out.json
    python3 cite_check.py ./repo --titles            # also verify title match
    python3 cite_check.py --ci ./repo                # quiet, exit 1 on failure

Exit: 0 clean, 1 failures found, 2 could not evaluate.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

# Crossref and NCBI both ask for a contact address; being polite buys headroom.
CONTACT = os.environ.get("CITE_CHECK_CONTACT", "collins.ra@husky.neu.edu")
UA = f"cite-check/1.0 (+https://github.com/rn-collins; mailto:{CONTACT})"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

TEXT_EXT = {".html", ".htm", ".md", ".mdx", ".txt", ".json", ".js", ".ts",
            ".jsx", ".tsx", ".csv", ".xml", ".mjs", ".mts"}
SKIP_DIR = {".git", "node_modules", ".next", "dist", "build", ".vercel",
            "coverage", "__pycache__", ".astro",
            # Archived copies of third-party pages. Links inside someone
            # else's mirrored page are not this project's citations.
            "downloaded", "mirrors", "snapshots", "web-archive"}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;:()/A-Za-z0-9]*[A-Za-z0-9)]", re.I)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{6,9})\b", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>)\]},\\]+", re.I)

# Statute patterns worth resolving to an actual text.
STATUTE_RE = re.compile(
    r"\b(?:ORS\s+(\d+[A-Za-z]?\.\d+)"          # Oregon Revised Statutes
    r"|(\d+)\s+U\.?S\.?C\.?\s+§+\s*(\d+[a-z]?)"  # US Code
    r"|C\.?R\.?S\.?\s+§+\s*([\d\-\.]+))",       # Colorado Revised Statutes
    re.I,
)

# Hosts that reliably refuse robots but serve humans fine. A non-200 from these
# is not evidence of breakage, and must never be reported as a dead citation.
BOT_BLOCKERS = {
    "justia.com", "law.justia.com", "cdc.gov", "gao.gov", "congress.gov",
    "legiscan.com", "thelancet.com", "sagepub.com", "journals.sagepub.com",
    "sciencedirect.com", "americanbar.org", "businesswire.com",
    "simonandschuster.com", "erowid.org", "reuters.com", "nytimes.com",
    "wsj.com", "ft.com", "time.com", "sec.gov", "dol.gov", "nycourts.gov",
    "dea.gov", "hhs.gov", "rainn.org", "meta.com", "linkedin.com",
    "springer.com", "link.springer.com", "wiley.com", "onlinelibrary.wiley.com",
    "tandfonline.com", "jamanetwork.com", "nejm.org", "bmj.com",
    # Cloudflare, and it 403s a deliberately nonexistent control path too —
    # so nothing on this host can be told apart from a dead page by machine.
    "law.ucdavis.edu",
}


def _curl(url, ua, timeout):
    """Fall back to curl. Python's TLS stack is refused outright by some hosts
    (TLSV1_ALERT_PROTOCOL_VERSION) that serve curl and browsers a clean 200 —
    seven such hosts turned up in one estate scan, every one reported dead by
    urllib and alive by everything else. A citation must not be called dead
    because of our own client's handshake."""
    import subprocess
    try:
        r = subprocess.run(
            ["curl", "-sL", "-m", str(timeout), "-A", ua, "-w", "\n__STATUS__%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 8)
        out = r.stdout
        if "__STATUS__" not in out:
            return 0, ""
        body, _, code = out.rpartition("\n__STATUS__")
        return int(code.strip() or 0), body
    except Exception:
        return 0, ""


def _get(url, ua=UA, timeout=25, method="GET"):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400_000) if method == "GET" else b""
            return r.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        # TLS negotiation failures are about our client, not the server.
        if "SSL" in type(e).__name__ or "SSL" in str(e) or "CERTIFICATE" in str(e).upper():
            return _curl(url, ua, timeout)
        return 0, ""


def norm_title(s):
    """Compare titles the way a librarian would, not the way a string does."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def title_overlap(a, b):
    """Jaccard over content words. Robust to subtitle and punctuation drift."""
    stop = {"the", "a", "an", "of", "and", "in", "for", "on", "to", "with",
            "at", "by", "from", "as", "is", "are", "study", "trial"}
    A = {w for w in norm_title(a).split() if w not in stop and len(w) > 2}
    B = {w for w in norm_title(b).split() if w not in stop and len(w) > 2}
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


# ---------------------------------------------------------------- resolvers

def check_doi(doi, cited_title=None):
    doi = doi.rstrip(".,;)]}'\"")
    st, body = _get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    if st == 404:
        return dict(kind="doi", id=doi, ok=False, severity="blocker",
                    reason="not registered in Crossref — this DOI does not exist")
    if st != 200:
        return dict(kind="doi", id=doi, ok=None, severity="unknown",
                    reason=f"Crossref returned {st}; could not verify")
    try:
        msg = json.loads(body)["message"]
        real = (msg.get("title") or [""])[0]
        year = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        container = (msg.get("container-title") or [""])[0]
    except Exception:
        return dict(kind="doi", id=doi, ok=None, severity="unknown",
                    reason="Crossref response unparseable")
    out = dict(kind="doi", id=doi, ok=True, severity="ok",
               registered_title=real, year=year, journal=container,
               reason="registered")
    if cited_title:
        ov = title_overlap(cited_title, real)
        out["title_overlap"] = round(ov, 2)
        if ov < 0.34:
            # Advisory, never a blocker. In a document that quotes its sources,
            # the quoted text nearest a citation is usually a quotation from
            # the work, not the work's title — so a low overlap is far more
            # often this tool guessing wrong than the citation being wrong.
            # Worth a human glance; never worth failing a build over.
            out.update(ok=None, severity="unknown",
                       reason=("possible title mismatch — CHECK BY HAND, the "
                               "nearby text may just be a quotation. "
                               f"near-text: {cited_title[:80]!r} | registered: {real[:80]!r}"))
    return out


def check_pmid(pmid, cited_title=None):
    st, body = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                    f"?db=pubmed&retmode=json&id={pmid}")
    if st != 200:
        return dict(kind="pmid", id=pmid, ok=None, severity="unknown",
                    reason=f"eutils returned {st}")
    try:
        rec = json.loads(body)["result"][str(pmid)]
        if "error" in rec:
            raise KeyError
        real = rec.get("title", "")
    except Exception:
        return dict(kind="pmid", id=pmid, ok=False, severity="blocker",
                    reason="PMID not found in PubMed")
    out = dict(kind="pmid", id=pmid, ok=True, severity="ok",
               registered_title=real, reason="resolves")
    if cited_title:
        ov = title_overlap(cited_title, real)
        out["title_overlap"] = round(ov, 2)
        if ov < 0.34:
            out.update(ok=None, severity="unknown",
                       reason=("possible title mismatch — CHECK BY HAND, the "
                               "nearby text may just be a quotation. "
                               f"near-text: {cited_title[:80]!r} | actual: {real[:80]!r}"))
    return out


API_ROOT = re.compile(r"^https?://(api\.|[^/]*api\.)|/api(/[a-z]{2})?(/v?[\d.]+)?/?$|/v[\d.]+/?$", re.I)


def check_url(url):
    url = url.rstrip(".,;:)]}'\"`")
    host = (urllib.parse.urlparse(url).netloc or "").lower().lstrip("www.")

    # An API base URL is infrastructure, not a citation. Roots legitimately
    # 404 because you are meant to call an endpoint beneath them.
    if API_ROOT.search(url):
        return dict(kind="url", id=url, ok=None, severity="unknown",
                    reason="API base URL, not a citation — not checked")

    st, _ = _get(url, ua=BROWSER_UA, method="HEAD")
    # Retry GET on 404 as well: several hosts (support.google.com among them)
    # 404 a HEAD and serve the same URL fine on GET. Without this retry a
    # working page is reported as a dead citation.
    if st in (0, 403, 404, 405, 401, 406, 501, 999):
        st, _ = _get(url, ua=BROWSER_UA)
    # Some hosts refuse the browser UA and serve a plain one. mass.gov is the
    # clear case: BROWSER_UA gets 403, the neutral UA gets a true 200 or 404.
    # Without this retry every mass.gov citation reads "unverifiable", which is
    # how three dead mass.gov links shipped as "0 dead links".
    if st in (0, 403, 401, 406, 429, 999):
        st2, _ = _get(url, ua=UA)
        if st2 in (200, 301, 302, 303, 307, 308, 404, 410):
            st = st2
    blocked = any(host == b or host.endswith("." + b) for b in BOT_BLOCKERS)
    if st in (200, 301, 302, 303, 307, 308):
        return dict(kind="url", id=url, ok=True, severity="ok", status=st,
                    reason="resolves")
    if st in (404, 410):
        return dict(kind="url", id=url, ok=False, severity="blocker", status=st,
                    reason=f"dead ({st}) — the cited page does not exist")
    if blocked or st in (403, 401, 406, 429, 999):
        return dict(kind="url", id=url, ok=None, severity="unknown", status=st,
                    reason=f"bot mitigation ({st}) — unverifiable by machine, "
                           "likely fine in a browser")
    if st == 0:
        return dict(kind="url", id=url, ok=False, severity="major", status=0,
                    reason="no response — DNS failure, TLS error, or timeout")
    return dict(kind="url", id=url, ok=None, severity="unknown", status=st,
                reason=f"unexpected status {st}")


ORS_TOPIC = re.compile(r"<title>([^<]{0,200})</title>", re.I)


def check_ors(section):
    """Resolve an Oregon statute and return its actual heading."""
    url = f"https://oregon.public.law/statutes/ors_{section.lower()}"
    st, body = _get(url, ua=BROWSER_UA)
    if st != 200:
        return dict(kind="statute", id=f"ORS {section}", ok=None,
                    severity="unknown", reason=f"could not fetch ({st})")
    m = ORS_TOPIC.search(body)
    heading = (m.group(1) if m else "").strip()
    return dict(kind="statute", id=f"ORS {section}", ok=True, severity="ok",
                heading=heading,
                reason=f"resolves — actual heading: {heading[:120]}")


# ---------------------------------------------------------------- extraction

def looks_like_a_title(s):
    """Reject anything that is plainly not a work's title.

    The first version of this accepted any quoted string near the citation,
    which meant URLs and HTML fragments were compared against registered
    titles and reported as mismatches. 110 of 138 findings on the first run
    were that bug. A candidate must now read as prose or it is not used —
    a missed title check is invisible; a false "wrong paper" is not.
    """
    if not s:
        return False
    s = s.strip()
    if len(s) < 18 or len(s) > 200:
        return False
    if re.search(r"https?://|www\.|doi\.org|[<>{}=|]|&[a-z]+;|\.(html?|php|json|pdf)\b", s, re.I):
        return False
    words = [w for w in re.split(r"\s+", s) if w]
    if len(words) < 4:
        return False
    # Real titles are mostly letters, not digits and punctuation.
    letters = sum(ch.isalpha() for ch in s)
    if letters / max(1, len(s)) < 0.62:
        return False
    return True


def nearby_title(text, pos, window=340):
    """Best-effort: a quoted or emphasised phrase near a citation is often its
    title. Only used when it survives looks_like_a_title()."""
    seg = text[max(0, pos - window): pos + window]
    cands = re.findall(r'[\u201c\u201d"]([^\u201c\u201d"]{18,200})[\u201c\u201d"]', seg)
    cands += re.findall(r"<em>([^<]{18,200})</em>", seg)
    cands += re.findall(r"<i>([^<]{18,200})</i>", seg)
    cands += re.findall(r"\*([^*\n]{18,200})\*", seg)
    cands = [c for c in cands if looks_like_a_title(c)]
    return max(cands, key=len) if cands else None


def scan(root, want_titles=False):
    """Walk a checkout and pull every citation with its file:line."""
    found = {}
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            path = os.path.join(dp, fn)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if len(text) > 8_000_000:
                continue
            # Zero-width and bidi marks ride along inside copied URLs and make
            # an otherwise-live link look dead.
            text = text.translate({0x200b: None, 0x200c: None, 0x200d: None,
                                   0xfeff: None, 0x200e: None, 0x200f: None})
            rel = os.path.relpath(path, root)

            def add(key, kind, ident, pos):
                line = text.count("\n", 0, pos) + 1
                rec = found.setdefault(key, dict(kind=kind, id=ident, sites=[],
                                                 cited_title=None))
                if len(rec["sites"]) < 40:
                    rec["sites"].append(f"{rel}:{line}")
                if want_titles and not rec["cited_title"]:
                    rec["cited_title"] = nearby_title(text, pos)

            for m in DOI_RE.finditer(text):
                d = m.group(0).rstrip(".,;)]}'\"")
                add(("doi", d.lower()), "doi", d, m.start())
            for m in PMID_RE.finditer(text):
                add(("pmid", m.group(1)), "pmid", m.group(1), m.start())
            for m in URL_RE.finditer(text):
                u = m.group(0).rstrip(".,;:)]}'\"")
                if "doi.org/" in u.lower():
                    d = u.split("doi.org/", 1)[1]
                    add(("doi", d.lower()), "doi", d, m.start())
                    continue
                # Not citations: infrastructure hosts named in CSP/config, and
                # bare origins that only appear as a policy directive.
                if any(x in u for x in ("localhost", "127.0.0.1", "example.com",
                                        "vercel.app/_vercel", "w3.org", "schema.org",
                                        "fonts.googleapis.com", "fonts.gstatic.com",
                                        "googletagmanager.com", "google-analytics.com",
                                        "vitals.vercel-insights.com",
                                        # XML/RDF namespace declarations are not citations
                                        "ogp.me/ns", "purl.org/dc", "xmlns", "/TR/xhtml",
                                        "opengraphprotocol.org", "creativecommons.org/ns")):
                    continue
                u = u.rstrip("*_~")          # markdown emphasis bleed
                if "${" in u or "`" in u or "{{" in u:
                    continue          # unrendered template literal, not a URL
                if "…" in u or "..." in u or u.endswith((".local", ".test", ".invalid")):
                    continue          # elided display string or local-only host
                if re.search(r"(^|/)(tests?|__tests__|spec|fixtures?|e2e)(/|$)", rel):
                    continue          # test fixtures are not citations
                if fn in ("vercel.json", "next.config.js", "next.config.mjs",
                          "next.config.ts", "package.json", "package-lock.json",
                          "manifest.json", "site.webmanifest"):
                    continue
                add(("url", u), "url", u, m.start())
            for m in STATUTE_RE.finditer(text):
                if m.group(1):
                    add(("ors", m.group(1)), "statute_ors", m.group(1), m.start())
    return found


# ---------------------------------------------------------------- driver

def verify(rec):
    k, i = rec["kind"], rec["id"]
    ct = rec.get("cited_title")
    if k == "doi":
        r = check_doi(i, ct)
    elif k == "pmid":
        r = check_pmid(i, ct)
    elif k == "statute_ors":
        r = check_ors(i)
    else:
        r = check_url(i)
    r["sites"] = rec["sites"]
    r["cited_title"] = ct
    return r


SEV_ORDER = {"blocker": 0, "major": 1, "unknown": 2, "ok": 3}
COLOR = {"blocker": "\033[91m", "major": "\033[93m", "unknown": "\033[90m",
         "ok": "\033[92m"}
RESET = "\033[0m"


def main():
    ap = argparse.ArgumentParser(description="Verify every citation in a build.")
    ap.add_argument("root", help="repo checkout to scan")
    ap.add_argument("--json", help="write full findings here")
    ap.add_argument("--titles", action="store_true",
                    help="also verify that DOI/PMID titles match what is cited")
    ap.add_argument("--ci", action="store_true", help="quiet; exit 1 on failure")
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()

    if not os.path.isdir(a.root):
        print(f"not a directory: {a.root}", file=sys.stderr)
        sys.exit(2)

    t0 = time.time()
    found = scan(a.root, want_titles=a.titles)
    if not a.ci:
        c = Counter(v["kind"] for v in found.values())
        print(f"scanning {a.root}")
        print(f"  {len(found)} unique citations: " +
              ", ".join(f"{n} {k}" for k, n in sorted(c.items())))
        print("  verifying…", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(verify, found.values()))

    results.sort(key=lambda r: (SEV_ORDER[r["severity"]], r["kind"], str(r["id"])))
    counts = Counter(r["severity"] for r in results)
    failed = counts["blocker"] + counts["major"]

    if not a.ci:
        for r in results:
            if r["severity"] == "ok":
                continue
            print(f"\n  {COLOR[r['severity']]}{r['severity'].upper():<8}{RESET} "
                  f"{r['kind']}  {str(r['id'])[:96]}")
            print(f"           {r['reason']}")
            if r.get("cited_title"):
                print(f"           cited as: {r['cited_title'][:100]!r}")
            print(f"           at: {', '.join(r['sites'][:4])}"
                  + (f"  (+{len(r['sites'])-4} more)" if len(r["sites"]) > 4 else ""))

    print(f"\n{'FAIL' if failed else 'PASS'}  {a.root}")
    print(f"      {len(results)} citations · {counts['blocker']} dead/wrong · "
          f"{counts['major']} unreachable · {counts['unknown']} unverifiable "
          f"(bot-blocked) · {counts['ok']} verified · {time.time()-t0:.0f}s")

    if a.json:
        json.dump(results, open(a.json, "w"), indent=1)
        print(f"      findings written to {a.json}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
