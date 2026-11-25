# Verdicto: AI-assisted, crowd-verified fact checking

Implements [PRD2.md](PRD2.md) (chat-first, role-based sign-up, community votes) on top of
[PRD.md](PRD.md) and the original [SPEC.md](SPEC.md) build.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY, GROQ_API_KEY, RESEARCH_CONTACT
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo            # --reset to reseed, --research to run live AI research on open claims
.venv/bin/python manage.py test
.venv/bin/python manage.py runserver
```

Without `GROQ_API_KEY` everything still runs: sources are collected, but the AI can't judge their
stances, so the provisional label shows "Unclear".

## Demo flow
1. Open the site: logged-out visitors only see **Log in / Sign up**. Sign up as **"I want to check
   claims"** (name, phone, fields of interest). You land on **Check a Claim**, the chat home.
2. Paste a rumour (Kannada or English). The card shows live progress, then the **AI-provisional**
   label, truth score and cited sources. Status stays **PENDING VERIFICATION**.
3. **Give your verdict** (community vote): 0.05 weight each, capped at 20% of the total combined.
4. Log in as an assigned verifier (e.g. `+919000000005`) → **Verify Queue** → two-pane review
   (claim + community signal | full AI research) → vote with a note and evidence.
5. After 3 verifier votes at 70% or more weighted agreement, the claim is **Verified**. The user's
   chat gets a "✅ Verified" message, and **News & Verdicts → Past verdicts** shows it.

Log in with a **phone number**. `seed_demo` prints all demo phones; they share one password,
`DEMO_PASSWORD`. The admin account (username `admin`, at `/admin/`) uses `ADMIN_PASSWORD` from `.env`.

## Where things live
| Area | Files |
|---|---|
| Sign-up / login | `accounts/forms.py` (role, name, phone, interests, verifier details), `accounts/backends.py` (phone login); site-wide `LoginRequiredMiddleware` (claim and share pages stay public) |
| Community votes | `verification/engine.py` (`community_vote_weight`: 0.05 each, max 20% combined), `CommunityVote` model |
| Chat + history | `chat/` (threads per user; system messages when a claim is finalised) |
| AI research pipeline | `research/pipeline.py`, `research/ai.py` (Groq), `research/connectors.py` |
| Live sources | Groq web search (gpt-oss `browser_search`), Google News RSS, official-sites news (Google News limited to gov domains), Wikipedia, OpenAlex, PubMed; optional with keys: Google Fact Check, Semantic Scholar, Reddit (official API). Crossref and GDELT are implemented but off by default. |
| Cross-language duplicates | `claims/duplicates.py:find_same_claim`: fuzzy match, plus the AI's English restatement (`Claim.canonical_en`) and an AI "same claim?" check, so Kannada and English versions of a rumour merge. `manage.py backfill_canonical` fills old claims. |
| Source credibility + truth score | `research/credibility.py`, `research/scoring.py` (a formula; the LLM never sets the score) |
| Safe fetching | `research/fetch.py` (http/https only, blocks private/internal IPs on every redirect, size + time limits) |
| Verifier assignment | `verification/assignment.py` (domain × language × reliability × workload × locality). Overdue assignments are reassigned automatically (checked at most every 10 minutes during normal traffic); `manage.py reassign_stale` also works from cron. Admin approvals record `approved_by` / `approved_at`. |
| Human verdict engine | `verification/engine.py` (weighted Beta-prior votes, unchanged from SPEC §6) |
| Feed ranking | `claims/ranking.py` (urgency × reach × domain risk × 24 h decay) |
| Moderation | AI holds claims about private individuals' personal lives; admins can hide/unhide in `/admin/` |
| Screenshots | `claims/uploads.py` (Pillow checks the real type, max 5 MB, re-encodes to strip EXIF/GPS, random file names) + `research/ai.py:extract_text_from_image` (Groq vision, `GROQ_VISION_MODEL`) |
| WhatsApp preview image | `claims/og_image.py` → `/c/<slug>/og.png` (1200×630; Kannada claims get an English caption because Pillow can't shape Kannada without libraqm) |
| Profiles | `/profile/` (details, track record, work-email check), `/verifiers/<id>/` (public: badges, accuracy per field/language, recent verdicts), `/my-votes/` |
| Work-email verification | `accounts/work_email.py`: free providers rejected, domain must resolve, 6-digit code stored only as an HMAC, 10-minute expiry, 5 attempts, rate-limited. In development the code prints in the server terminal. Verified email gives the 0.6 starting weight. |
| Source feedback | Verifiers mark AI sources 👍 useful / 👎 wrong (`SourceFeedback`); counts show to other verifiers |
| Community accuracy | When a claim is finalised, regular users' votes are scored too (`User.community_correct/total`, no double counting); shown on their profile |
| Re-check, listen, time left | "↻ Re-check with latest sources" on the research card; "🔊 Listen" reads the verdict aloud (kn-IN / en-IN); the Verify Queue shows time left before reassignment |
| Kannada UI | EN / ಕನ್ನಡ switch in the top bar and on the login page; users' preferred language is the default. Translations live in `locale/kn_strings.py`; run `python manage.py compilemo` after editing (builds the `.po`/`.mo` without GNU gettext). |

| Phone OTP (optional) | `PHONE_OTP_ENABLED=True` adds a 6-digit SMS step to sign-up (`accounts/phone_otp.py`); in development the code prints in the server log; set `SMS_BACKEND=twilio` and the `TWILIO_*` values to send real SMS. |

## Production
See the "Production" block in `.env.example`: `DEBUG=False` (HTTPS cookies, HSTS, SSL redirect),
`DATABASE_URL` for Postgres, `CACHE_BACKEND=db` or `redis://…` for shared rate limits,
`RESEARCH_MODE=worker` plus `python manage.py run_worker --pause 30` as a separate process, SMTP
settings for work-email codes, and `collectstatic`. `python manage.py check --deploy` passes apart from
the optional HSTS preload. Progress and decisions are tracked in [PROGRESS.md](PROGRESS.md).

## Deliberate limits
- **Google Scholar and Web of Science are not used.** Scholar has no API and forbids scraping; Web of
  Science needs an institutional key. OpenAlex and Crossref cover scholarly literature legally.
- **Social media:** we don't scrape it (against platform terms). Social posts come in only when web
  search surfaces them, and they get the lowest credibility tier.
- The AI's quotes must appear verbatim in the source text, or they are dropped and the stance falls
  back to neutral. Page text is treated as untrusted data (prompt-injection guard).
- Research runs in an in-process background thread (SQLite, single server). For production, use a task
  queue as described in PRD §14.
