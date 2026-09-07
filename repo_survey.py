#!/usr/bin/env python3
"""
repo-survey — what is actually in a repo, before deciding to publish it.

Publishing is irreversible in practice: once a history is public it has been
public. The question "is this safe to publish" is not answered by the ship gate
or the contrast sweep, which look at deployed sites. It is answered by reading
what is in the tree — whose work it is, whether it carries anyone's personal
data, and whether it is even a website.

Read-only: shallow-clones, reports, deletes the clone.
"""
import re, shutil, subprocess, sys, tempfile
from pathlib import Path

# names that suggest someone else's data is in here
PII = re.compile(r"""(?ix)
    \b(\d{3}-\d{2}-\d{4}          # ssn
    |\d{3}[-.]\d{3}[-.]\d{4}      # phone
    |[a-z0-9._%+-]+@(?!example|test|localhost)[a-z0-9.-]+\.[a-z]{2,})\b""")
CLIENTY = re.compile(r"(?i)\b(client|invoice|contract|nda|retainer|proposal|engagement|deliverable|sow)\b")

def run(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True).stdout

def survey(owner, name):
    tmp = Path(tempfile.mkdtemp(prefix="rs-"))
    r = tmp / "r"
    c = subprocess.run(["git", "clone", "-q", "--depth", "50",
                        f"https://github.com/{owner}/{name}.git", str(r)], capture_output=True)
    if c.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"name": name, "error": "clone failed"}

    files = [p for p in r.rglob("*") if p.is_file() and ".git" not in p.parts
             and "node_modules" not in p.parts]
    exts = {}
    for f in files:
        exts[f.suffix.lower() or "(none)"] = exts.get(f.suffix.lower() or "(none)", 0) + 1

    is_site = any((r / n).exists() for n in
                  ("vercel.json", "next.config.js", "next.config.ts", "index.html", "app", "pages"))
    readme = ""
    for n in ("README.md", "readme.md"):
        if (r / n).exists():
            readme = " ".join((r / n).read_text(errors="replace").split())[:200]
            break

    emails, clienty = set(), set()
    for f in files:
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".woff", ".woff2",
                                ".ico", ".mp4", ".zip", ".docx", ".pptx", ".xlsx"):
            continue
        try:
            t = f.read_text(errors="replace")
        except Exception:
            continue
        for m in PII.findall(t):
            emails.add(m if isinstance(m, str) else m[0])
        if CLIENTY.search(f.name) or CLIENTY.search(t[:4000]):
            clienty.add(str(f.relative_to(r)))

    commits = run("git", "log", "--oneline", "-5", cwd=r).strip().splitlines()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"name": name, "files": len(files), "site": is_site, "readme": readme,
            "exts": sorted(exts.items(), key=lambda kv: -kv[1])[:5],
            "emails": sorted(emails)[:6], "clienty": sorted(clienty)[:6],
            "commits": commits[:3]}

if __name__ == "__main__":
    names = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    for n in names:
        s = survey("rn-collins", n)
        if s.get("error"):
            print(f"\n=== {n}: {s['error']}"); continue
        print(f"\n=== {s['name']}  ({s['files']} files, {'website' if s['site'] else 'NOT a website'})")
        if s["readme"]: print(f"    readme: {s['readme'][:150]}")
        print(f"    types: {', '.join(f'{k}×{v}' for k,v in s['exts'])}")
        if s["emails"]:  print(f"    EMAILS/PII: {', '.join(s['emails'])}")
        if s["clienty"]: print(f"    client-ish files: {', '.join(s['clienty'][:4])}")
        if s["commits"]: print(f"    last commit: {s['commits'][0][:80]}")
