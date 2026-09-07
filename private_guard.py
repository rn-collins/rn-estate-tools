#!/usr/bin/env python3
"""
private-guard — assert that a confidential repo is still confidential.

institutions-of-one solves the public/private problem by splitting: a public
reader, a private project record, and a build-time boundary check that fails if
an unlisted file reaches `public/`. That works because the project HAS a public
artifact.

The client repos have no public artifact. Their root index.html is a blank page
(241 bytes, background and text set to the same colour) and every real document
lives behind an unguessable directory like `g-336bb6b3/`. The whole repository is
the private record. So the boundary to assert is not "what may be published" but
"none of this may be", and the two ways it breaks are:

  1. the repo is flipped public, which publishes the unguessable paths, or
  2. the deployment serves those paths to anyone who has one.

On 6 September 2026 both rn-clients and ospace were serving client engagement
terms, a content plan, a proposal, a year plan and a 1,169-word invoice with
sixteen dollar figures — HTTP 200, no authentication, obscurity the only control.

Read-only. Reports; changes nothing.

    python3 private_guard.py --repos private.txt
"""
import argparse, json, re, shutil, subprocess, sys, tempfile, urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (compatible; private-guard/1.0)"

def gh_visibility(owner, name):
    out = subprocess.run(["gh", "repo", "view", f"{owner}/{name}", "--json", "visibility"],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out)["visibility"]
    except Exception:
        return "UNKNOWN"

# Pages that answer 200 and are not the document: a framework placeholder, a
# login wall, a bot interstitial. Counting one as an exposed document is a false
# alarm; missing a 308 to the extensionless URL is a false all-clear. The first
# run of this tool did both — it cleared rn-clients, which was serving a client
# invoice behind one redirect, and flagged ospace, which is a stray vue-template
# occupying that hostname and not RN's build at all.
NOT_A_DOCUMENT = ("vue-template", "just a moment", "sign in", "log in",
                  "authentication required", "vercel authentication", "404")

class _Redirect308(urllib.request.HTTPRedirectHandler):
    """Python 3.9's urllib raises on 308 instead of following it.

    This deployment 308s `/doc.html` to `/doc`, so without this the guard sees a
    308, records nothing, and reports a client invoice as unreachable. That is
    the same defect that made the ship gate call Brotli-compressed scripts
    corrupt: the transport detail decided the verdict, and the verdict was the
    opposite of the truth.
    """
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)

_OPENER = urllib.request.build_opener(_Redirect308)

def reachable(url):
    """(status, bytes, title) following redirects, as a browser would."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _OPENER.open(req, timeout=20) as r:
            body = r.read(300_000).decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>([^<]*)", body, re.I)
            return r.status, len(body), (m.group(1).strip() if m else "")
    except urllib.error.HTTPError as e:
        return e.code, 0, ""
    except Exception:
        return 0, 0, ""

def check(owner, name):
    vis = gh_visibility(owner, name)
    tmp = Path(tempfile.mkdtemp(prefix="pg-"))
    r = tmp / "r"
    c = subprocess.run(["git", "clone", "-q", "--depth", "1",
                        f"https://github.com/{owner}/{name}.git", str(r)], capture_output=True)
    paths = []
    if c.returncode == 0:
        tracked = subprocess.run(["git", "ls-files"], cwd=r, capture_output=True, text=True).stdout.split()
        # a document living under a hashed directory is protected only by its name
        paths = [p for p in tracked if "/" in p and p.endswith(".html")]
    shutil.rmtree(tmp, ignore_errors=True)

    exposed = []
    for p in paths[:8]:
        # the deployment 308s .html to the extensionless path; follow it
        code, size, title = reachable(f"https://{name}.vercel.app/{p}")
        if code != 200 or size <= 800:
            continue
        if any(k in title.lower() for k in NOT_A_DOCUMENT):
            continue
        exposed.append((p, size, title))
    return vis, len(paths), exposed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    ap.add_argument("--owner", default="rn-collins")
    a = ap.parse_args()
    bad = 0
    for name in [l.strip() for l in open(a.repos) if l.strip()]:
        vis, n, exposed = check(a.owner, name)
        flag = []
        if vis != "PRIVATE":
            flag.append(f"REPO IS {vis} — the unguessable paths are published")
        if exposed:
            flag.append(f"{len(exposed)} document(s) served unauthenticated")
        status = "OK" if not flag else "REVIEW"
        print(f"  {name:16} repo={vis:8} docs={n:3}  {status}")
        for f in flag:
            print(f"      ! {f}")
        for p, sz, title in exposed[:4]:
            print(f"        {p}  ({sz:,} bytes, HTTP 200, no auth)  title: {title[:52]}")
        bad += bool(flag)
    print(f"\n{bad} repo(s) need attention")

if __name__ == "__main__":
    main()
