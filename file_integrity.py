#!/usr/bin/env python3
"""
file-integrity — which committed text files are actually binary garbage?

Four times in this estate a file returned 200, looked fine to every other check,
and was unreadable bytes:

  - a package-lock.json that npm ci could never parse, so CI had never passed
  - an index.html that was base64 inside a JSON envelope and could never render
  - an index.css 54% non-printable, truncating inside a .hamburger rule, which
    left a site unstyled for a fortnight
  - a second package-lock.json, 77.6% non-printable, found only because a build
    happened to rewrite it

Nothing caught any of them. The route returned 200, the asset returned 200, and
a typeface survey read the families out of the readable prefix and called the
site styled. Corruption of this kind is only visible in the bytes.

estate_check's asset_integrity covers CSS and JS that a live page references.
It cannot see a lockfile, a config, or a fixture — those never appear in HTML.
This checks the repo instead.

Usage:
    python3 file_integrity.py --repos repos.txt [--out report.txt]
    python3 file_integrity.py --self-test
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEXT_EXT = {".json", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html",
            ".htm", ".md", ".txt", ".yml", ".yaml", ".svg", ".xml", ".sh", ".py",
            ".toml", ".lock", ".csv", ".env", ".gitignore"}

def printable_share(raw):
    if not raw:
        return 1.0
    ok = sum(1 for b in raw if 9 <= b <= 13 or 32 <= b <= 126)
    # UTF-8 multi-byte sequences are legitimate text; count them as printable
    # rather than flagging every em dash and diacritic in the estate.
    try:
        raw.decode("utf-8")
        return 1.0
    except UnicodeDecodeError:
        pass
    return ok / len(raw)

def scan_tree(root, floor=0.85):
    bad = []
    for f in root.rglob("*"):
        if not f.is_file() or ".git" in f.parts or "node_modules" in f.parts:
            continue
        if f.suffix.lower() not in TEXT_EXT:
            continue
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        share = printable_share(raw)
        if share < floor:
            bad.append((str(f.relative_to(root)), len(raw), round(100 * (1 - share))))
    return sorted(bad, key=lambda r: -r[2])

def self_test():
    tmp = Path(tempfile.mkdtemp(prefix="fi-selftest-"))
    (tmp / "good.json").write_bytes(b'{"a":1,"b":"em dash \xe2\x80\x94 fine"}')
    (tmp / "good.css").write_text("body{color:#111}\n")
    (tmp / "broken.json").write_bytes(bytes(range(256)) * 40)
    (tmp / "image.png").write_bytes(bytes(range(256)) * 40)   # not a text ext
    bad = scan_tree(tmp)
    names = {b[0] for b in bad}
    ok = names == {"broken.json"}
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  flags binary content in a .json      : {'PASS' if 'broken.json' in names else 'FAIL'}")
    print(f"  leaves valid UTF-8 and images alone  : {'PASS' if ok else 'FAIL'} ({sorted(names)})")
    return 0 if ok else 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos"); ap.add_argument("--owner", default="rn-collins")
    ap.add_argument("--out"); ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())

    lines, flagged = [], 0
    for name in [l.strip() for l in open(a.repos) if l.strip()]:
        tmp = Path(tempfile.mkdtemp(prefix="fi-"))
        c = subprocess.run(["git", "clone", "-q", "--depth", "1",
                            f"https://github.com/{a.owner}/{name}.git", str(tmp / "r")],
                           capture_output=True)
        if c.returncode != 0:
            print(f"  {name:38} clone failed"); shutil.rmtree(tmp, ignore_errors=True); continue
        bad = scan_tree(tmp / "r")
        shutil.rmtree(tmp, ignore_errors=True)
        if bad:
            flagged += 1
            print(f"  {name:38} {len(bad)} CORRUPT")
            lines.append(f"=== {name}")
            for path, size, pct in bad:
                print(f"      {path}  {size}b  {pct}% non-printable")
                lines.append(f"   {path}  {size}b  {pct}% non-printable")
        else:
            print(f"  {name:38} clean")
    print(f"\n{flagged} repo(s) with corrupt committed text files")
    if a.out and lines:
        Path(a.out).write_text("\n".join(lines) + "\n")
        print(f"report written to {a.out}")

if __name__ == "__main__":
    main()
