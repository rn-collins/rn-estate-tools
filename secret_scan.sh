#!/usr/bin/env bash
# secret-scan — what would become readable if these repos went public?
#
# RN wants the estate public. A repo's history goes public with it, so a key
# that was committed once and deleted in the next commit is still there for
# anyone who clones. This scans the FULL history diff, not the working tree.
#
# Read-only: clones to a temp dir, scans, deletes the clone. Changes nothing on
# GitHub and flips nothing. The flip stays a human decision.
#
# Usage: secret_scan.sh repos.txt outdir
set -u
LIST="$1"; OUT="${2:-/tmp/secret-scan}"
mkdir -p "$OUT"; : > "$OUT/findings.txt"; : > "$OUT/summary.txt"

# Patterns for credentials that are actually secret. `process.env.FOO` and
# `.env.example` placeholders are not, and must not drown out the real thing.
PAT='(sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{12,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|glpat-[A-Za-z0-9_-]{20}|npm_[A-Za-z0-9]{36}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})'
# assignment of a literal that is not read from the environment
ASSIGN='(api[_-]?key|apikey|secret|passwd|password|token|bearer)["'"'"' ]*[:=][ ]*["'"'"'][A-Za-z0-9_\-]{16,}["'"'"']'

while read -r repo; do
  [ -z "$repo" ] && continue
  d="$OUT/clone"; rm -rf "$d"
  if ! git clone -q "https://github.com/rn-collins/$repo.git" "$d" 2>/dev/null; then
    echo "$repo :: CLONE FAILED" >> "$OUT/summary.txt"; continue
  fi
  hist=$( (cd "$d" && git log --all -p --no-color 2>/dev/null) | head -c 60000000 )
  hits=$(printf '%s' "$hist" | grep -aoE "$PAT" 2>/dev/null | sort -u | head -20)
  # A saved Cloudflare/Akamai interstitial carries vendor challenge nonces that
  # match the JWT shape and are not anyone's credential. If the history contains
  # such a page, drop the bare three-part-dotted matches and keep the rest.
  if printf '%s' "$hist" | grep -aq 'Just a moment\|challenge-platform\|cf_chl'; then
    hits=$(printf '%s\n' "$hits" | grep -avE '^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$')
  fi
  assigns=$(printf '%s' "$hist" | grep -aiE "^\+.*$ASSIGN" 2>/dev/null \
            | grep -av 'process\.env' | grep -av 'example' | grep -av 'placeholder' \
            | grep -av 'YOUR_' | grep -av 'xxx' | sed 's/^\+//' | sort -u | head -10)
  envfiles=$( (cd "$d" && git log --all --name-only --format= 2>/dev/null) \
              | grep -aE '(^|/)\.env(\.|$)' | grep -avE '\.env[^/]*\.example$' | sort -u | head -10)
  n=0
  { [ -n "$hits" ] && n=$((n+$(printf '%s\n' "$hits" | wc -l))); } 2>/dev/null
  if [ -n "$hits$assigns$envfiles" ]; then
    { echo "=== $repo"
      [ -n "$hits" ]     && { echo "  credential-shaped strings:"; printf '%s\n' "$hits" | sed 's/^/     /'; }
      [ -n "$assigns" ]  && { echo "  literal assignments:";       printf '%s\n' "$assigns" | cut -c1-150 | sed 's/^/     /'; }
      [ -n "$envfiles" ] && { echo "  .env files in history:";     printf '%s\n' "$envfiles" | sed 's/^/     /'; }
      echo
    } >> "$OUT/findings.txt"
    echo "$repo :: REVIEW" >> "$OUT/summary.txt"
  else
    echo "$repo :: clean" >> "$OUT/summary.txt"
  fi
  rm -rf "$d"
done < "$LIST"
echo "done. $(grep -c REVIEW "$OUT/summary.txt" 2>/dev/null || echo 0) repo(s) need review; see $OUT/findings.txt"
