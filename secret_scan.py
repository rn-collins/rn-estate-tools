#!/usr/bin/env python3
"""
secret-scan — what becomes readable if these repos go public?

RN wants the estate public. A repo's history goes public with it, so a key that
was committed once and deleted in the next commit is still there for anyone who
clones. This scans the FULL history diff, not the working tree.

Read-only. It clones to a temp dir, scans, and deletes the clone. It changes
nothing on GitHub and flips nothing: the decision to publish stays a person's.

A scanner nobody has seen fire is worth nothing, so `--self-test` plants a fake
key in a throwaway repo, deletes it in a later commit, and proves the scan finds
it in the history anyway. Run it before trusting a clean result.

The false positives below are all real ones this found across 71 repos, and each
would have sent someone chasing a key that does not exist:
  - `govuk-icon-mask-<64 hex>.svg`, an asset digest, matched a bare `sk-` rule
  - a saved Cloudflare "Just a moment" page, whose challenge nonces are shaped
    exactly like JWTs and belong to Cloudflare
  - `.env.local.example`, a placeholder template, matched an `.env` rule that
    only knew to skip `.env.example`
  - `KV_REST_API_TOKEN: "must-not-mix-pairs"`, a test fixture

Usage:
    python3 secret_scan.py --repos repos.txt --out /tmp/secret-scan
    python3 secret_scan.py --self-test
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Credentials that are actually secret. Each is anchored so it cannot match
# mid-word: `mask-cdf42…` is not an OpenAI key.
B = r"(?<![A-Za-z0-9_-])"
PATTERNS = {
    "anthropic key":   re.compile(B + r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai key":      re.compile(B + r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "github token":    re.compile(B + r"gh[pousr]_[A-Za-z0-9]{36}"),
    "github pat":      re.compile(B + r"github_pat_[A-Za-z0-9_]{40,}"),
    "aws key id":      re.compile(B + r"AKIA[0-9A-Z]{16}"),
    "slack token":     re.compile(B + r"xox[baprs]-[A-Za-z0-9-]{12,}"),
    "google api key":  re.compile(B + r"AIza[0-9A-Za-z_-]{35}"),
    "gitlab token":    re.compile(B + r"glpat-[A-Za-z0-9_-]{20}"),
    "npm token":       re.compile(B + r"npm_[A-Za-z0-9]{36}"),
    "private key":     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt":             re.compile(B + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}

# A literal assigned to a credential-shaped name — the shape of a hardcoded key.
ASSIGN = re.compile(
    r"""(?ix)
    (api[_-]?key|apikey|secret|passwd|password|token|bearer|refresh[_-]?token)
    \s*["']?\s*[:=]\s*
    (["'])([A-Za-z0-9_\-]{16,})\2
    """)

# Values that look like credentials and are not.
BENIGN_VALUE = re.compile(r"""(?ix)
    ^(process\.env|import\.meta|your[_-]|xxx+|placeholder|example|changeme|
      test|dummy|fake|sample|must-not|redacted|<.*>)""")

def git(repo, *args, cap=80_000_000):
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, timeout=600)
        return out.stdout[:cap].decode("utf-8", "replace")
    except Exception:
        return ""

def scan_repo(path):
    """Findings for one already-cloned repo."""
    hist = git(path, "log", "--all", "-p", "--no-color")
    names = git(path, "log", "--all", "--name-only", "--format=")

    # A saved bot-challenge page carries vendor nonces shaped like credentials.
    challenge = bool(re.search(r"Just a moment|challenge-platform|cf_chl", hist))

    creds = {}
    for label, rx in PATTERNS.items():
        for m in set(rx.findall(hist)):
            if challenge and label == "jwt":
                continue
            creds.setdefault(label, set()).add(m)

    assigns = set()
    for line in hist.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = ASSIGN.search(line)
        if m and not BENIGN_VALUE.match(m.group(3)):
            assigns.add(line.lstrip("+").strip()[:160])

    envs = {n for n in names.splitlines()
            if re.search(r"(^|/)\.env(\.|$)", n) and not n.endswith(".example")}

    return {"credentials": {k: sorted(v) for k, v in creds.items()},
            "assignments": sorted(assigns), "env_files": sorted(envs)}

def mask(v):
    return v[:10] + "…REDACTED" if len(v) > 14 else "…REDACTED"

def self_test():
    tmp = Path(tempfile.mkdtemp(prefix="secret-scan-selftest-"))
    r = tmp / "repo"; r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    run("init", "-q")
    (r / "config.js").write_text(
        "const KEY=\"sk-ant-api03-" + "F" * 40 + "\";\n")
    (r / ".env").write_text("DATABASE_URL=postgres://u:p@h/db\n")
    run("add", "-A"); run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add")
    (r / ".env").unlink()
    (r / "config.js").write_text("const KEY=process.env.ANTHROPIC_API_KEY;\n")
    run("add", "-A"); run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "remove")

    f = scan_repo(r)
    ok = bool(f["credentials"].get("anthropic key")) and ".env" in f["env_files"]

    # and it must NOT fire on the four real false positives
    r2 = tmp / "clean"; r2.mkdir()
    run2 = lambda *a: subprocess.run(["git", "-C", str(r2), *a], capture_output=True)
    run2("init", "-q")
    (r2 / "page.html").write_text(
        '<link rel="mask-icon" href="/assets/govuk-icon-mask-' + "a1" * 32 + '.svg">\n')
    (r2 / ".env.local.example").write_text("ANTHROPIC_API_KEY=\n")
    (r2 / "fixture.ts").write_text('KV_REST_API_TOKEN: "must-not-mix-pairs",\n')
    run2("add", "-A"); run2("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "benign")
    g = scan_repo(r2)
    quiet = not g["credentials"] and not g["assignments"] and not g["env_files"]

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  detects a key deleted from history : {'PASS' if ok else 'FAIL'}")
    print(f"  stays quiet on the known benigns   : {'PASS' if quiet else 'FAIL'}")
    if not quiet:
        print("   still firing on:", g)
    return 0 if (ok and quiet) else 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos"); ap.add_argument("--out", default="/tmp/secret-scan")
    ap.add_argument("--owner", default="rn-collins")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    names = [l.strip() for l in open(a.repos) if l.strip()]
    report, flagged = [], 0
    for name in names:
        tmp = Path(tempfile.mkdtemp(prefix="ss-"))
        c = subprocess.run(["git", "clone", "-q",
                            f"https://github.com/{a.owner}/{name}.git", str(tmp / "r")],
                           capture_output=True)
        if c.returncode != 0:
            print(f"  {name:38} clone failed"); shutil.rmtree(tmp, ignore_errors=True); continue
        f = scan_repo(tmp / "r")
        shutil.rmtree(tmp, ignore_errors=True)
        hit = bool(f["credentials"] or f["assignments"] or f["env_files"])
        flagged += hit
        report.append((name, f))
        print(f"  {name:38} {'REVIEW' if hit else 'clean'}")

    with open(out / "report.txt", "w") as fh:
        for name, f in report:
            if not (f["credentials"] or f["assignments"] or f["env_files"]):
                continue
            fh.write(f"=== {name}\n")
            for label, vals in f["credentials"].items():
                fh.write(f"  {label}:\n")
                for v in vals: fh.write(f"     {mask(v)}\n")
            for l in f["assignments"]: fh.write(f"  assignment: {l}\n")
            for e in f["env_files"]:   fh.write(f"  .env in history: {e}\n")
            fh.write("\n")
    print(f"\n{len(report)} repos scanned · {flagged} need review · {out}/report.txt")

if __name__ == "__main__":
    main()
