# CareerOS — Core Engine for the Bootstrap Frontend

This wires your existing CareerOS HTML dashboard up to a real backend. The
frontend now calls a Flask server that serves the page itself, so there's no
separate "start two servers" step and no CORS friction.

## Folder structure

```
careeros/
├── app.py                       Flask app: serves frontend/ + all /api/* routes
├── requirements.txt
├── README.md
├── agents/
│   ├── __init__.py
│   ├── scout_agent.py           Scout Agent  — 5 real, public, no-key sources
│   ├── strategist_agent.py      Strategist Agent — real page scrape + skill dissection
│   └── recruiter_match_agent.py Recruiter-Match Agent — deterministic fit scoring
└── frontend/
    └── index.html               Your CareerOS dashboard (lightly modified, see below)
```

## Run it

```bash
cd careeros
pip install -r requirements.txt
# optional, only if you want LLM-written analysis instead of the built-in
# keyword analyzer:
export GEMINI_API_KEY=your_key_here

python app.py
```

Open **http://localhost:5000** — that's it, one server, one URL.

## What I changed in your HTML, and why

1. **Portal names/colors**: `Naukri / Indeed / Glassdoor / Monster` were
   placeholders for fake data generators in your old scout code (they just
   cycled hardcoded company names — not real scraping). I replaced them with
   sources that are real and actually reachable without an API key:
   `RemoteOK`, `Arbeitnow`, and `Devpost` (which covers hackathons too).
   `LinkedIn` and `Unstop` are kept as-is since your original approach for
   those was sound.
2. **Empty-state per portal**: if a portal returns zero results for a search,
   its card now says so instead of just looking broken/blank.
3. **Added a Recruiter-Match card**: after you run "Analysis" on a job, a new
   section lets you enter your skills/experience/location/interests and get
   a fit score against that specific posting — this is the third agent from
   your architecture (Scout → Strategist → Recruiter-Match) that wasn't
   wired into the UI yet. Everything else in your layout/markup/behavior is
   untouched.

## API contract (matches what your HTML already calls)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/search` | `{role, location, sources?}` | `{sources: {"LinkedIn": [...], "Unstop": [...], "RemoteOK": [...], "Arbeitnow": [...], "Devpost": [...]}}` |
| POST | `/api/analyze` | `{title, company, location, link}` | `{analysis: {...}}` |
| POST | `/api/match` | `{job, strategist_analysis, student_profile}` | `{match: {fit_score, verdict, breakdown}}` |

## A bug I found while testing this build

While load-testing this against the frontend, `scout_agent.py`'s shared
`_safe_get()` helper crashed with `got multiple values for keyword argument
'headers'` whenever a caller (Unstop, Devpost) passed its own `headers=`
— because `_safe_get` was also hardcoding `headers=DEFAULT_HEADERS` in the
same call. Fixed by merging the two dicts instead of hardcoding one. This
would have silently 500'd `/api/search` for you the first time you tried it,
so it's fixed in both this build and the earlier `core_engine/` one.

## Same sandbox caveat as before

I verified all three endpoints return clean JSON end-to-end from this
environment, but my network here is allowlisted and doesn't include
linkedin.com / unstop.com / remoteok.com / etc., so live scraping itself
isn't verifiable from my side — run it on your machine with normal internet
to see real listings populate. Errors now surface as a visible red alert in
the UI (`data.error`) instead of a silent console-only failure, so if
something's blocked you'll see it immediately.
