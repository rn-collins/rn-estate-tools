# rn-estate-tools

Two checkers for the RN Collins build estate, plus a CI workflow.

They exist because an audit of six builds found the same thing each time: the
prose was accurate and the *pointer to the source* was not. Real articles with
fabricated ID numbers. DOIs registered nowhere. Cited titles matching no paper
that exists. Oregon's tobacco statute cited as the source of a psilocybin
employment protection that does not exist in Oregon law.

None of that is catchable by a link checker, because a link checker only asks
whether a URL returns 200 — not whether the source says what the citation
claims it says.

The point of both tools is that a quality claim should be **reproducible by
someone who does not trust the person making it**. If a build is described as
clean, these should say so on your machine.

---

## `cite_check.py` — citation integrity

```sh
python3 cite_check.py ./repo --titles
python3 cite_check.py ./repo --titles --json report.json
python3 cite_check.py ./repo --ci            # quiet; exit 1 on failure
```

| Kind | What is verified |
|---|---|
| **DOI** | Registered in Crossref — and with `--titles`, whether the registered title matches the title it is cited under. A DOI that resolves to a *different paper* is the failure mode that reads as correct and isn't. |
| **PMID** | Resolves in NCBI eutils; same title check. |
| **URL** | Live status, separating genuine 404/410 from bot mitigation. |
| **Statute** | Fetches the section and reports its **actual heading**, so a mis-cited statute is visible. |

Severities: `blocker` (dead or wrong), `major` (unreachable), `unknown`
(bot-blocked — reported, never failed), `ok`.

### What it deliberately does not do

- **Bot-blocked hosts never fail the build.** Reuters, NYT, SEC, DEA, Justia,
  ScienceDirect and ~30 others refuse robots and serve humans fine. A 403 from
  those is not evidence of breakage. They are listed and reported separately.
- **Config files are skipped.** `vercel.json`, `next.config.*`, `package.json`,
  manifests. A CSP directive naming `fonts.googleapis.com` is not a citation.
- **Titles are only compared when the extracted candidate reads like prose.**
  No URLs, no markup, four words minimum, 62% letters minimum.

That last rule is load-bearing. The first version accepted any quoted string
near a citation, so URLs and HTML fragments were compared against registered
titles and reported as mismatches: **110 of 138 findings on the first run were
that bug**. A missed title check is invisible; a false "wrong paper" sends
someone chasing a ghost through a document they wrote correctly.

---

## `estate_check.py` — ship gate

```sh
python3 estate_check.py https://example.vercel.app
python3 estate_check.py --batch urls.txt --json estate.json
python3 estate_check.py https://example.vercel.app --shallow   # skip link resolution
```

A build passes only when it exits 0.

| Group | Checks |
|---|---|
| **Function** | every route 200 · no `href="#"` · internal and external links resolve · in-page anchors point at ids that exist |
| **Present** | title · description · canonical · favicon · og:image that actually loads · og:title · apple-touch-icon |
| **Substance** | ≥400 visible words · ≤30% caveat/hedge density |
| **Access** | heading order · alt text · form labels · `lang` |

The substance thresholds are the interesting ones. They encode two failures
that recur across the estate: pages that are stubs pretending to be products,
and pages whose process vocabulary crowds out their content. Raise them
deliberately; never lower one to make a build pass.

### Known handling

- **Follows 308.** Python < 3.11 does not, and Vercel's `cleanUrls` emits 308
  constantly — without this, every clean-URL route reads as a dead route.
  (It reported 17 working routes as blockers before this was fixed.)
- **Retries once on transport failure.** A server-rendered page behind a pooled
  database can exceed the timeout under concurrency; one miss is not "dead".
- **Grades HTML only.** Data and asset routes are checked for reachability, not
  for having a `<title>`.
- **`data:` URI favicons count**, and a site serving `/favicon.ico` without a
  `<link rel=icon>` is not missing a favicon — browsers request it anyway.

---

## CI

`ci/citation-check.yml` → drop at `.github/workflows/citation-check.yml`.

Runs on push, PR, and a **weekly cron**. Citations rot without anyone touching
the repo — agencies reorganise, DOIs move, postings expire — so a green build
in March means nothing in September. A scheduled failure opens a labelled issue
rather than quietly turning a cron run red, because a red cron nobody looks at
is the same as no check.

---

## Baseline, 2026-08-28

Six builds audited. Citation defects found:

| Build | Real defects |
|---|---|
| `ownership-platform` | 43 dead on live pages about named people, +28 in draft dossiers |
| `destig-toolkit` | 25, incl. 7 DOIs registered nowhere |
| `law-communication-library` | 1 URL — plus six articles resting on the wrong statute |

Independent agreement is the reason to trust these: on `destig-toolkit`, a
reasoning agent reading the document and this script querying Crossref
converged on the **same six unregistered DOIs**. On `ownership-platform` the
agent found 41 dead links on live pages and this found 43.

---

## Adding a bot-blocked host

If a host is reported dead but loads in a browser, add it to `BOT_BLOCKERS` in
`cite_check.py`. Confirm in a real browser first — the whole value of the tool
is that a `blocker` means something.
