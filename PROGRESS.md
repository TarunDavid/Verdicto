# Progress

Verdicto: a chat-first fact-check assistant for Karnataka. The AI researches a claim on live sources
and gives a provisional answer; approved verifiers give the final, weighted verdict.

## Status: feature-complete against SPEC.md, PRD.md and PRD2.md (97 tests passing)

| Area | Status | Notes |
|---|---|---|
| Roles, phone login, sign-up (regular user / verifier) | ✅ | Site-wide login; claim & share pages public |
| Optional phone OTP at sign-up | ✅ | Off by default (`PHONE_OTP_ENABLED`); dev "SMS" prints to the server log |
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
| Production setup | ✅ | `RESEARCH_MODE=worker` + `run_worker`, Postgres via `DATABASE_URL`, shared cache, SMTP, HTTPS settings; `check --deploy` clean except optional HSTS preload |

## Decisions and simplifications
- Verifier profile fields live on `User` (one table) instead of a separate `VerifierProfile`; same data, simpler queries.
- Google Scholar / Web of Science aren't used (no API / institutional key); OpenAlex, PubMed and Crossref instead.
- PIB blocks automated clients, so official sources come from a Google News search restricted to government domains.
- Social media isn't scraped; posts appear only via search (or Reddit's official API with keys).
- No Groq embedding model is available on this account, so cross-language matching uses an English restatement plus an AI "same claim?" check.
- Kannada text on the OG image is replaced by an English caption (Pillow lacks Kannada shaping without libraqm).
- Tailwind and HTMX load from CDNs (no Node build step, per the SPEC's stack).

## Not done / future
- Browser end-to-end tests (needs Selenium/Playwright, not in the approved stack); server-side tests cover the flows.
- A real SMS gateway for phone OTP (`accounts/phone_otp.send_sms`).
- Merging verifier data into a separate profile table, if the team prefers the SPEC's original model.
