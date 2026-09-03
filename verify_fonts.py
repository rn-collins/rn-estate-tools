#!/usr/bin/env python3
"""Serve a build with its OWN vercel.json headers and check that the browser
actually fetches every @font-face file the pages declare.

Why this exists: a fonts.googleapis.com <link> renders correctly on a plain
static server and is silently blocked in production by `style-src 'self'`,
which every one of these builds ships. The page then falls back to Georgia or
system-ui and nothing reports it. Serving the real policy and watching which
font files the browser asks for is the check that catches it — a browser
requests a woff2 only when a rule that survived the CSP needs it.

usage: verify_fonts.py <sitedir> <page.html> [<page.html> ...]
"""
import functools, http.server, json, os, re, shutil, signal, socket
import socketserver, subprocess, sys, tempfile, threading, time

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
site = sys.argv[1]
pages = sys.argv[2:] or ["index.html"]

cfg = json.load(open(os.path.join(site, "vercel.json")))
HDRS = [(k["key"], k["value"])
        for h in cfg.get("headers", []) for k in h.get("headers", [])]
csp = next((v for k, v in HDRS if k.lower() == "content-security-policy"), "NONE")
print("serving with the build's own %d header(s)" % len(HDRS))
print("CSP: %s" % csp)

root = os.path.join(site, cfg["outputDirectory"]) if cfg.get("outputDirectory") else site

# every font file the pages declare, and the family it belongs to
want = {}
for p in pages:
    h = open(os.path.join(root, p), encoding="utf-8").read()
    # follow same-origin <link rel=stylesheet> too: clerking-site keeps its
    # @font-face rules in styles.css, not inline
    srcs = [h]
    for href in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="(/[^"]+)"', h):
        try:
            srcs.append(open(os.path.join(root, href.lstrip("/")), encoding="utf-8").read())
        except OSError:
            pass
    for s_ in srcs:
        for fam, url in re.findall(
                r"@font-face\{font-family:'([^']+)'[^}]*?url\('([^']+)'\)", s_):
            want[url] = fam
    if "fonts.googleapis.com" in h or "fonts.gstatic.com" in h:
        print("!! %s still references Google Fonts — blocked by style-src 'self'" % p)
        sys.exit(1)
print("declared font files: %d across %s"
      % (len(want), ", ".join(sorted(set(want.values()))) or "no families"))

stage = tempfile.mkdtemp(prefix="cspcheck-")
srvroot = os.path.join(stage, "site")
shutil.copytree(root, srvroot, ignore=shutil.ignore_patterns(".git"))

seen, missing404 = set(), []
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        try:
            code = int(a[1])
        except Exception:
            code = 0
        path = self.path.split("?")[0]
        if path.endswith(".woff2"):
            seen.add(path)
            if code >= 400:
                missing404.append((path, code))
    def end_headers(self):
        for k, v in HDRS:
            self.send_header(k, v)
        super().end_headers()

class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True; daemon_threads = True

sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
srv = S(("127.0.0.1", port), functools.partial(H, directory=srvroot))
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.6)

prof = tempfile.mkdtemp(prefix="cspprof-")
for i, p in enumerate(pages):
    shot = os.path.join(prof, "s%d.png" % i)
    proc = subprocess.Popen(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         "--no-first-run", "--no-default-browser-check", "--disable-extensions",
         "--user-data-dir=%s-%d" % (prof, i), "--window-size=1280,1400",
         "--screenshot=" + shot, "http://127.0.0.1:%d/%s" % (port, p)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    t = time.time() + 40
    while time.time() < t:
        if os.path.exists(shot) and os.path.getsize(shot) > 0:
            time.sleep(0.5); break
        time.sleep(0.25)
    try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception: proc.kill()
srv.shutdown()

fams_seen = sorted({want[u] for u in want if u in seen})
fams_all = sorted(set(want.values()))
print("\nfont files actually requested by the browser: %d" % len(seen))
for u in sorted(seen):
    print("   %-58s %s" % (u, want.get(u, "(unexpected)")))
bad = 0
for f in fams_all:
    ok = f in fams_seen
    print("   family %-24s served under CSP: %s" % (f, "YES" if ok else "NO"))
    if not ok:
        bad += 1
for p, c in missing404:
    print("!! %s returned %d" % (p, c)); bad += 1
print("\nRESULT:", "every declared family loads under the build's own CSP"
      if not bad else "%d PROBLEM(S)" % bad)
sys.exit(1 if bad else 0)
