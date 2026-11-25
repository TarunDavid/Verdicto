# Progress

Verdicto: a chat-first fact-check assistant for Karnataka. The AI researches a claim on live sources
and gives a provisional answer; approved verifiers give the final, weighted verdict.

## Status: feature-complete against SPEC.md, PRD.md and PRD2.md (113 tests passing, incl. 2 Playwright browser tests)

| Area | Status | Notes |
|---|---|---|
| Roles, phone login, sign-up (regular user / verifier) | ✅ | Site-wide login; claim & share pages public |
| Optional phone OTP at sign-up | ✅ | Off by default (`PHONE_OTP_ENABLED`); `SMS_BACKEND=console` (dev, prints to the log) or `twilio` |
| Chat home (design 1), threads, follow-ups, voice input | ✅ | |
| Screenshot checks (OCR) | ✅ | Groq vision (`qwen/qwen3.8-27b`); EXIF stripped, 5 MB cap, random names |
| AI research pipeline + truth score | ✅ | Formula-based score; AI never sets the verdict |
| Live sources | ✅ | Groq web search, Google News, official-sites news, Wikipedia, OpenAlex, PubMed, Fact Check API*, Semantic Scholar*, Reddit* (*need keys) |
| Duplicate detection incl. Kannada ↔ English | ✅ | Fuzzy match + AI English restatement + AI confirmation |
| Verifier assignment, auto-reassignment | ✅ | Overdue assignments reassigned automatically (throttled, no cron needed) |
| Weighted verdict engine, community votes (0.05, ≤20%) | ✅ | One vote per verifier per claim in the DB |
| Append-only verdict log | ✅ | Blocked in model, queryset and admin |
| Verify Queue + two-pane review + source 👍/👎 | ✅ | Time left before reassignment |
| News & Verdicts (important / latest / past) | ✅ | |
| Profiles, public verifier profiles, My votes | ✅ | Community accuracy tracked |
| Work-email OTP + MX check + trust bonus | ✅ | Hashed codes, expiry, attempt limit |
| Admin approval audit (approved_by / at) | ✅ | |
| WhatsApp share + generated OG image | ✅ | |
| Listen button, re-check with latest sources | ✅ | |
| Kannada UI (247 strings) | ✅ | `locale/kn_strings.py` → `manage.py compilemo` |
| Moderation (private individuals) | ✅ | AI auto-hold + admin hide/unhide |
| Browser tests (Playwright) | ✅ | `claims/test_browser.py`: sign-up, chat check, community vote, Kannada switch, verifier review in real Chromium; CSS checked |
| Local front-end assets | ✅ | Tailwind built locally (`buildcss`, standalone CLI), HTMX vendored; `USE_CDN_ASSETS` to switch back |
| VerifierProfile table | ✅ | Verifier details moved off `User` with a data migration (0005–0007) |
| API key checker | ✅ | `manage.py checkkeys` |
| Insider assignment (org match) | ✅ | VTU staff get VTU claims first, Nvidia staff get Nvidia claims; abbreviations, work-email domain, AI-extracted organisations |
| Track records ("_% accurate in _ verdicts") | ✅ | Shown everywhere a verifier appears; drives weight and assignment order |
| AI agent verifiers | ✅ | `research/agents.py`, 3 agents with separate sources; vote with all sources as evidence; abstain on thin evidence; ≥1 human vote still needed to finalise (`FINAL_MIN_HUMAN_VOTES`) |
| Public per-verifier verdicts + sources | ✅ | Claim page "Verified by" cards, share page |
| End-to-end journey test | ✅ | `claims/test_journey.py`: sign-up → chat → research → votes → final → notice → archive/share/Kannada |
| Production setup | ✅ | `RESEARCH_MODE=worker` + `run_worker`, `Procfile`, `gunicorn.conf.py`, `.env.production.example`, `DEPLOY.md` (nginx + checklist), Postgres via `DATABASE_URL`, shared cache, SMTP, HTTPS settings; `check --deploy` clean except optional HSTS preload |

## Decisions and simplifications
- `User.domains` holds fields of interest for everyone (and expertise for verifiers); verifier-only details are in `VerifierProfile`.
- Google Scholar / Web of Science aren't used (no API / institutional key); OpenAlex, PubMed and Crossref instead.
- PIB blocks automated clients, so official sources come from a Google News search restricted to government domains.
- Social media isn't scraped; posts appear only via search (or Reddit's official API with keys).
- No Groq embedding model is available on this account, so cross-language matching uses an English restatement plus an AI "same claim?" check.
- Kannada text on the OG image is replaced by an English caption (Pillow lacks Kannada shaping without libraqm).
- Tailwind is built with the standalone CLI (no Node), keeping the SPEC's no-build-step spirit for running the app.
- Fonts (Inter, Noto Sans Kannada) still load from Google Fonts; the app falls back to system fonts offline.

## Waiting on you
- Paste the optional keys (Google Fact Check, Semantic Scholar, Reddit, Twilio) into `.env`, then run `python manage.py checkkeys`.
