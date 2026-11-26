# Verdicto: how the code works

A map of the codebase: what each part does, where every external API is called, and where the main
work happens. Line numbers are correct as of this writing; if they drift, search for the function name.

**In one sentence:** someone pastes a forwarded rumour into the chat; AI researches it on live
sources and gives a provisional answer; 26 AI verifiers research it independently and vote; human
verifiers (routed by field, language, track record and insider knowledge) vote with evidence; a
weighted formula decides the final verdict, which is public with every source.

**Stack:** Django 5.2 (Python 3.13), SQLite (Postgres in production via `DATABASE_URL`), server-rendered
templates + HTMX for live updates, Tailwind CSS built locally (no Node), Groq for all AI calls.
There is no separate front-end app and no JavaScript framework.

---

## 1. Project map

| Folder | What lives there |
|---|---|
| `factcheck/` | Project config: `settings.py`, `urls.py` (every route), middleware, error handlers, template context |
| `chat/` | **Check a Claim** (the home page): chat threads, posting a claim, follow-up questions, the live result card |
| `claims/` | The `Claim` model, News & Verdicts feed, claim page, share page + WhatsApp preview image, duplicate detection, screenshot handling, demo data |
| `research/` | **All AI and web research**: Groq calls (`ai.py`), source connectors (`connectors.py`), the research pipeline (`pipeline.py`), the 26 AI verifiers (`agents.py`), credibility tiers, truth score, safe page fetching |
| `verification/` | Votes and the verdict engine: weighted maths (`engine.py`), casting votes and recomputing (`services.py`), verifier assignment (`assignment.py`), Verify Queue + review screen, JSON API |
| `accounts/` | Users, roles, `VerifierProfile`, phone login, sign-up, optional SMS OTP (Twilio), work-email verification, admin |
| `templates/` | All HTML. `base.html` is the app shell; `research/_card.html` is the live result card |
| `static/` | `css/input.css` (design system source), `css/app.css` (built), `vendor/htmx.min.js` |
| `locale/` | Kannada UI: `kn_strings.py` is the source; `manage.py compilemo` builds the `.po/.mo` |
| `scripts/share.sh` | Share the dev server through a Cloudflare tunnel |
| `tools/` | `tailwindcss` and `cloudflared` binaries (not committed) |

---

## 2. The life of a claim (the main flow)

```
User pastes text / link / screenshot in chat
        │
        ▼
chat/views.py  _new_claim()  ─── screenshot? → claims/uploads.py clean_screenshot()  →  Groq vision OCR
        │                    ─── link?       → research/fetch.py fetch_article()     (reads the page)
        │
        ├─ Groq: canonical_english()  (English restatement, so Kannada/English versions match)
        ├─ claims/duplicates.py find_same_claim()  → same rumour already in? attach to it, stop
        │
        ▼  new claim (status RESEARCHING)
research/pipeline.py  start_research()  → background thread (or a worker process)
        │
        ▼
run_research()
   1. Groq: understand()        → neutral claim, field, urgency, search queries, organisations, moderation
   2. _search_all()             → ~9 source connectors in parallel (news, official, fact-check, research)
   3. _enrich()                 → reads the full text of top pages (safe fetcher)
   4. Groq: assess()            → each source: supports / refutes / neutral (quotes must be verbatim)
   5. scoring.truth_score()     → 0–100 score from a formula (the AI never sets it)
        │
        ▼
_finish()
   ├─ status → PENDING, logged
   ├─ verification/assignment.py assign_verifiers()   → top human verifiers (insiders first)
   └─ research/agents.py run_agents()                 → all 26 AI verifiers research + vote (3 at a time)
        │
        ▼
Human verifiers: Verify Queue → review screen → vote with note + evidence
        │
        ▼
verification/services.py cast_vote() → recompute() → engine.compute_verdict()
        │   FINAL when: ≥3 votes, ≥70% weighted agreement, ≥1 human vote
        ▼
Claim FINAL → chat gets "✅ Verified: FALSE … by <names>", track records scored, public page + WhatsApp share
```

The same steps with exact locations:

| Step | Where |
|---|---|
| Chat form posts here | `chat/views.py:48` `send()` → `:67` `_new_claim()` |
| Screenshot validation (real type, 5 MB, EXIF stripped, random name) | `claims/uploads.py:13` `clean_screenshot()`, called at `chat/views.py:73` |
| OCR of the screenshot | `research/ai.py:203` `extract_text_from_image()`, called at `chat/views.py:76` |
| Link → page text | `research/fetch.py:104` `fetch_article()`, called at `chat/views.py:88` |
| English restatement | `research/ai.py:160` `canonical_english()`, called at `chat/views.py:100` |
| Duplicate / cross-language match | `claims/duplicates.py:54` `find_same_claim()` (fuzzy match, then `ai.pick_same_claim()` at `:73`) |
| Start research | `research/pipeline.py:36` `start_research()`, called at `chat/views.py:115` |
| The research run | `research/pipeline.py:84` `run_research()` |
| Understand the claim | `pipeline.py:91` → `research/ai.py:112` `understand()` |
| Search all sources in parallel | `pipeline.py:153` `_search_all()` (relevance ranking at `:252` `_key_terms` / `:276` `_relevance`) |
| Read full pages | `pipeline.py:281` `_enrich()` → `fetch.py:104` |
| Judge sources | `pipeline.py:123` → `research/ai.py:271` `assess()` |
| Truth score | `research/scoring.py:20` `truth_score()`; credibility tiers in `research/credibility.py:45` `classify()` |
| Moderation (private individuals) | `pipeline.py:315` `_moderate()` |
| Hand-off to verifiers | `pipeline.py:329` `_finish()` → `assign_verifiers()` (`:342`) and `run_agents()` (`:345`) |
| Live card in the chat (HTMX polling) | `chat/views.py:143` `card()` renders `templates/research/_card.html`; polls every 2 s while researching, 4 s while AI verifiers answer, 20 s until final |
| Follow-up questions | `chat/views.py:126` `_followup()` → `research/ai.py:333` `answer_followup()` |
| Re-check with latest sources | `research/views.py:28` `recheck()` → `start_research()` |

---

## 3. Every external connection

### 3a. Groq (all AI) — `research/ai.py`

One client, created in `ai.py:32` `client()` with `GROQ_API_KEY`. Every function degrades gracefully: no
key, a timeout or bad output → a fallback, never a crash. Text from users and web pages is wrapped in
tags and treated as untrusted data (prompt-injection guard, `ai.py` `GUARD`).

| Function | What it does | Model (setting) | Called from |
|---|---|---|---|
| `chat_json()` `:47` | The shared JSON chat call (`completions.create` at `:49`) | per caller | everything below except OCR / web search |
| `understand()` `:112` | Neutral claim, field, urgency, search queries, organisations, moderation flag | `GROQ_FAST_MODEL` (qwen/qwen3.8-27b) | `research/pipeline.py:91` |
| `canonical_english()` `:160` | One-sentence English restatement | `GROQ_FAST_MODEL` | `chat/views.py:100`, `claims/views.py:107`, `backfill_canonical` |
| `pick_same_claim()` `:181` | "Is this the same rumour?" check for duplicates | `GROQ_FAST_MODEL` | `claims/duplicates.py:73` |
| `extract_text_from_image()` `:203` | OCR of screenshots (`completions.create` at `:210`) | `GROQ_VISION_MODEL` | `chat/views.py:76` |
| `web_search()` `:235` | Live web search via the `browser_search` tool (`completions.create` at `:245`) | `GROQ_SEARCH_MODEL` (openai/gpt-oss-20b) | `research/connectors.py:44` (`groq_web` connector) |
| `assess()` `:271` | Stance of each source + summary; quotes not found verbatim are dropped | `GROQ_MODEL` (openai/gpt-oss-120b) for main research; `GROQ_AGENT_MODELS` (rotated) for AI verifiers | `research/pipeline.py:123`, `research/agents.py:332` |
| `answer_followup()` `:333` | Answers follow-up questions in chat from the found sources | `GROQ_MODEL` | `chat/views.py:131` |

Limits to know: Groq's free tier allows roughly 200k tokens per model per day and ~8k tokens per minute.
That is why work is spread over three models and AI verifiers retry after a minute when rate-limited.

### 3b. Research sources — `research/connectors.py`

Each connector is `name(query, language) -> [{url, title, snippet, published}]`, registered in
`CONNECTORS` (`connectors.py:209`). Which ones run: `RESEARCH_CONNECTORS` in `settings.py:156`.
Academic ones only run for health/science/technology claims in the main research.

| Connector | Service and endpoint | Key needed | Line |
|---|---|---|---|
| `groq_web` | Groq web search (above) | `GROQ_API_KEY` | `:41` |
| `google_news` | `https://news.google.com/rss/search` (RSS) | no | `:47` |
| `official_news` | Google News restricted to government domains | no | `:174` |
| `factcheck` | `https://factchecktools.googleapis.com/v1alpha1/claims:search` | `GOOGLE_FACTCHECK_API_KEY` | `:67` |
| `wikipedia` | `https://{en,kn}.wikipedia.org/w/api.php` | no (needs `RESEARCH_CONTACT` in User-Agent) | `:87` |
| `openalex` | `https://api.openalex.org/works` | no | `:96` |
| `crossref` | `https://api.crossref.org/works` (off by default) | no | `:112` |
| `gdelt` | `https://api.gdeltproject.org/api/v2/doc/doc` (off by default) | no | `:127` |
| `pubmed` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/` | no | `:135` |
| `semantic_scholar` | `https://api.semanticscholar.org/graph/v1/paper/search` | `SEMANTIC_SCHOLAR_API_KEY` | `:155` |
| `reddit` | `https://www.reddit.com/api/v1/access_token` + `https://oauth.reddit.com/search` | `REDDIT_CLIENT_ID/SECRET` | `:184` |

Connectors and page reading go through `research/fetch.py:41` `get()`, which blocks private/internal
addresses on every redirect (SSRF guard, `fetch.py:26` `assert_public_host`), caps size and time, and
sends a `User-Agent` with `RESEARCH_CONTACT`. The exceptions are `semantic_scholar` and `reddit`, which
call their fixed public APIs with `httpx` directly (they need auth headers).

### 3c. Other outside services

| Service | What for | Where | Setting |
|---|---|---|---|
| Twilio SMS | Optional phone OTP at sign-up (`POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json`) | `accounts/phone_otp.py:29` `send_sms()` (`:34`) | `PHONE_OTP_ENABLED`, `SMS_BACKEND=twilio`, `TWILIO_*`; default `console` prints the code |
| SMTP email | Work-email verification codes | `accounts/work_email.py:59` `send_code()` (`send_mail` at `:66`) | `EMAIL_*`; in development codes print to the terminal |
| DNS (MX lookup) | Checks a work-email domain can receive mail | `accounts/work_email.py:43` `has_mail_server()` | none |
| Cloudflare tunnel | Public link for friends | `scripts/share.sh`, `tools/cloudflared` | `TUNNEL_HOSTS` |
| Google Fonts | Fraunces, Inter, JetBrains Mono, Noto Kannada | `templates/base.html`, `auth_base.html` | none (falls back to system fonts) |

Check which keys work: `python manage.py checkkeys` (`research/management/commands/checkkeys.py`).

---

## 4. Routes (all in `factcheck/urls.py` + `chat/urls.py`)

| URL | View | Who |
|---|---|---|
| `/` | `chat/views.py:35` `chat_home` — Check a Claim | logged in |
| `/chat/send/`, `/chat/<id>/send/` | `chat/views.py:48` `send` — new claim / follow-up | logged in |
| `/chat/<id>/` | a chat thread | owner |
| `/chat/card/<claim_id>/` | `chat/views.py:143` `card` — **HTMX partial** for the live result card | logged in |
| `/my-checks/` | `chat/views.py` `my_checks` — every claim the user has checked, with AI answer, AI verifiers' tally and final verdict; filters and search | logged in |
| `/news/`, `/news/past/` | `claims/views.py:44` `feed`, `:70` `archive` | logged in |
| `/claims/<id>/` | `claims/views.py:142` `detail` — public claim page | **public** |
| `/claims/<id>/opinion/` | `claims/views.py:82` community vote | regular users |
| `/claims/<id>/vote/` | `verification/views.py:20` expert vote | approved verifiers |
| `/claims/<id>/recheck/` | `research/views.py:28` re-run research | logged in |
| `/claims/<id>/override/` | `verification/views.py:59` admin override (reason required, logged) | admins |
| `/verify/`, `/verify/<id>/` | `verification/views.py:80` Verify Queue, `:105` two-pane review with the **AI brief** | approved verifiers |
| `/sources/<id>/feedback/` | `research/views.py:18` 👍/👎 on an AI source | verifiers |
| `/c/<slug>/`, `/c/<slug>/og.png` | `claims/views.py:192` share page, `:171` WhatsApp preview image | **public** |
| `/api/claims/<id>/` | `verification/views.py:70` JSON status | **public** |
| `/api/claims/<id>/votes/` | `verification/views.py:38` JSON vote (rate-limited) | verifiers |
| `/accounts/login/`, `/signup/`, `/signup/verify/`, `/become-verifier/` | `accounts/views.py` | public |
| `/profile/`, `/my-votes/`, `/verifiers/<id>/` | `accounts/views.py:98`, `:153`, `:139` | logged in |
| `/i18n/setlang/` | English ↔ Kannada switch | public |
| `/admin/` | Django admin (only on this laptop when shared through a tunnel) | staff |

Every page needs a login (`LoginRequiredMiddleware`) except those marked public with `@login_not_required`.

---

## 5. Data model

| Model | File | Key fields / role |
|---|---|---|
| `User` | `accounts/models.py:22` | `role` (citizen / verifier / admin), `phone` (login), `preferred_language`, `domains` (interests/expertise), `district`, community accuracy |
| `VerifierProfile` | `accounts/models.py:123` | one per verifier: `status` (pending/approved/rejected), languages, profession, **organisation**, work email, LinkedIn, `is_ai_agent`, `approved_by/at` |
| `WorkEmailOTP` | `accounts/models.py:152` | hashed 6-digit code, expiry, attempts |
| `Claim` | `claims/models.py:33` | text, `canonical_en`, `entities` (organisations), language, domain, urgency, `status`, `verdict`, `leading`, `confidence`, screenshot, `share_slug` |
| `ChatThread` / `ChatMessage` | `chat/models.py:7`, `:21` | a user's conversation; messages from user / bot / system |
| `ResearchRun` | `research/models.py:16` | one AI research pass: queries, label, truth score, summary, models used, connector status. `agent` + `vote` set when it belongs to an AI verifier. `ResearchRun.objects` = main runs only; `ResearchRun.all_runs` = all |
| `Source` | `research/models.py:75` | one source found: url, publisher, tier, stance, verbatim quote |
| `SourceFeedback` | `research/models.py:103` | verifier 👍/👎 on a source |
| `Vote` + `Evidence` | `verification/models.py:20`, `:37` | an expert vote (one per verifier per claim), weight frozen at vote time, evidence links |
| `CommunityVote` | `verification/models.py:126` | a regular user's opinion (low weight, changeable until final) |
| `VerdictLog` | `verification/models.py:57` | **append-only** audit trail of every status/verdict change |
| `VerifierStat` | `verification/models.py:87` | track record per verifier × field × language (correct / total) |
| `Assignment` | `verification/models.py:102` | a claim routed to a verifier, with score and reason |

---

## 6. How verdicts are decided — `verification/engine.py` (pure functions)

- **Verifier weight** (`:14` `beta_weight`): `(correct + 2) / (total + 4)` → a new verifier is 0.5;
  a verified work email starts at 0.6. Looked up per field + language, then field, then overall record
  (`:20` `lookup_record`).
- **Community votes** (`:43`): 0.05 each, all together at most 20% of the total.
- **AI verifiers** (`:55` `agent_vote_scale`): all agents together at most `AI_AGENT_WEIGHT_CAP` (50%).
- **Final** (`:79` `compute_verdict`): at least 3 votes, at least 70% weighted agreement, at least
  `FINAL_MIN_HUMAN_VOTES` (1) human vote.
- The arithmetic shown on every claim page comes from `:119` `explain()`.

Database side — `verification/services.py`: `cast_vote()` `:28` → `recompute()` `:52` (logs changes,
notifies chat threads `:125`) → `score_votes()` `:152` (updates track records when a claim becomes
final, without double counting). Admin override `:133`.

**Assignment** — `verification/assignment.py`: `rank_verifiers()` `:124` scores
field × language × reliability × workload × locality, ×3 for insiders. **Insiders** (someone who works
at the organisation the claim is about — matched by `org_match()` `:108` using names, abbreviations
like VTU, work-email domain and AI-extracted organisations) always come first. `assign_open_claims_to()`
`:180` gives a newly approved verifier their organisation's open claims. Overdue assignments are
reassigned by `reassign_stale()` `:202`, run automatically (throttled) from `factcheck/context.py:17`.

---

## 7. The 26 AI verifiers — `research/agents.py`

- Roster: `AGENTS` list with names in `NAMES` (Satya = News Desk, Pramana = Fact-check, Sakshi =
  Indian fact-checkers, Arogya = Health, Vidya = Education, Raksha = Scams, …). Each has its own
  connectors and query templates (mostly Google News restricted to trusted sites).
- Accounts are created/updated by `ensure_agents()` `:177` (no phone, no password: they can't log in).
- `run_agents()` `:231`: runs all of them (or the best-suited `AI_AGENTS_PER_CLAIM`, picked by
  `pick_agents()` `:199`) in **3 parallel lanes**, each on a different Groq model, sharing searches and
  pages; rate-limited agents are retried after `AI_AGENT_RETRY_DELAY`.
- Per agent, `run_agent()` `:300`: search → read pages → `ai.assess()` → truth score → **vote** (summary
  as note, every source as evidence) or **abstain** when evidence is thin (`_record()` `:339`).
- Database writes go one at a time through `DB_LOCK` (SQLite has a single writer).
- Live tally shown in chat and on the claim page: `claims/templatetags/verdicto.py:19` `agent_tally`.
- Run on older claims: `python manage.py run_agents`.
- **AI brief for human verifiers** (`research/brief.py` `ai_brief()`, shown by
  `templates/verification/_ai_brief.html` on the review screen): the AI research result, the AI
  verifiers' tally and each one's verdict, and a suggested verdict (weighted by the agents' track
  records, or from the AI research if no agent voted) with the 3 best evidence links. "Use AI
  suggestion" only pre-fills the form (verdict, draft note, links); the verifier checks, edits and
  submits. Nothing is voted on their behalf.

---

## 8. Background work and concurrency

| What | How |
|---|---|
| AI research | `RESEARCH_MODE=thread` (default): a daemon thread per claim, started after the DB commit (`pipeline.py:36`). `RESEARCH_MODE=worker`: runs stay queued and `manage.py run_worker` processes them (production). `RESEARCH_SYNC=True` runs inline (tests). |
| AI verifiers | Inside the research thread, after research finishes: 3 worker threads (`AI_AGENT_WORKERS`). |
| Stuck runs | `run_worker` marks runs interrupted by a crash as failed (`pipeline.py:72` `recover_stuck`) so claims still reach verifiers. |
| Reassignment | Throttled to every 10 minutes during normal traffic (`factcheck/context.py`), or `manage.py reassign_stale` from cron. |
| SQLite | WAL mode + `IMMEDIATE` transactions + 20 s timeout (`settings.py` `_database`) so background writers don't collide. |

---

## 9. Security and safety

- **Login** by phone + password (`accounts/backends.py:7`); admin uses username + password.
- **Rate limits** (`django-ratelimit`) on login, sign-up, posting claims and opinions, votes, re-checks,
  source feedback, work-email codes and the JSON vote API.
- **SSRF guard** on every fetched URL (`research/fetch.py:26`); only http/https links accepted
  (`claims/validators.py`).
- **Prompt-injection guard**: user/page text is tagged as untrusted; AI quotes must appear verbatim in
  the source or they are dropped and the stance falls back to neutral (`ai.py:271` `assess`).
- **The AI never sets a verdict or score**: scores come from `scoring.py`; verdicts from people
  (AI verifiers capped at 50% and at least one human vote required).
- **Append-only audit log** (`VerdictLog`, blocked in model, queryset and admin).
- **Codes** (SMS OTP, work email) are stored only as HMAC hashes with expiry and attempt limits.
- **Uploads**: real image type checked, 5 MB cap, re-encoded to strip EXIF/GPS, random file names.
- **Moderation**: claims about private individuals' personal lives are held automatically.
- **Tunnel sharing**: `factcheck/middleware.py:73` `TunnelGuardMiddleware` hides `/admin/` and
  Django debug pages from anyone not on this laptop.
- **Production** (`DEBUG=False`): HTTPS cookies, HSTS, SSL redirect; see `DEPLOY.md`.

---

## 10. Configuration (`.env`, read in `factcheck/settings.py`)

| Setting | Purpose |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `SITE_URL` | Django basics |
| `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_FAST_MODEL`, `GROQ_SEARCH_MODEL`, `GROQ_VISION_MODEL`, `GROQ_AGENT_MODELS` | AI |
| `RESEARCH_CONNECTORS`, `RESEARCH_CONTACT`, `RESEARCH_MODE` | Research sources and how research runs |
| `GOOGLE_FACTCHECK_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, `REDDIT_CLIENT_ID/SECRET` | Optional sources |
| `AI_AGENTS_ENABLED`, `AI_AGENTS_PER_CLAIM` (0 = all 26), `AI_AGENT_WORKERS`, `AI_AGENT_RETRIES`, `AI_AGENT_RETRY_DELAY`, `AI_AGENT_WEIGHT_CAP`, `FINAL_MIN_HUMAN_VOTES` | AI verifiers and how much they count |
| `PHONE_OTP_ENABLED`, `SMS_BACKEND`, `TWILIO_*` | SMS codes at sign-up |
| `EMAIL_*` | Work-email codes |
| `DATABASE_URL`, `CACHE_BACKEND` | Production database and shared cache |
| `ADMIN_PASSWORD`, `DEMO_PASSWORD` | Demo data passwords (`seed_demo`) |
| `USE_CDN_ASSETS`, `TUNNEL_HOSTS` | Front-end assets from CDN; hosts allowed for tunnels |

---

## 11. Commands

| Command | What it does |
|---|---|
| `manage.py runserver` | Run the app (http://127.0.0.1:8000) |
| `manage.py seed_demo [--reset] [--research]` | Demo users, verifiers, claims and votes |
| `manage.py checkkeys` | Tests every API key |
| `manage.py run_agents [--claim ID]` | AI verifiers on open claims |
| `manage.py run_worker [--once]` | Research worker (production, `RESEARCH_MODE=worker`) |
| `manage.py reassign_stale` | Reassign overdue verifier assignments |
| `manage.py backfill_canonical` | English restatements for old claims |
| `manage.py buildcss` | Rebuild `static/css/app.css` after template changes |
| `manage.py compilemo` | Rebuild Kannada translations after editing `locale/kn_strings.py` |
| `./scripts/share.sh` | Public link through a Cloudflare tunnel |

---

## 12. Tests (`manage.py test`, 130 tests)

| File | Covers |
|---|---|
| `verification/tests.py` | Verdict maths, weights, caps, assignment, append-only log |
| `research/tests.py` | Pipeline, scoring, connectors, quote guard, relevance ranking |
| `research/test_agents.py` | Insider matching, track records, 26 AI verifiers (incl. real parallel threads), weight cap, retries, AI brief, My checks |
| `claims/tests.py` | Pages, feed, duplicates, uploads, share/OG image, Kannada (every template string translated) |
| `claims/test_journey.py` | Full journey: sign-up → chat → research → votes → final → share; Twilio |
| `claims/test_browser.py` | Real Chromium (Playwright): sign-up, check, vote, Kannada, verifier review |
| `factcheck/test_tunnel.py` | Tunnel guard |

External services are stubbed in tests, so they run offline.

---

## 13. Where to change things

| I want to… | Edit |
|---|---|
| Add a research source | a function in `research/connectors.py`, register it in `CONNECTORS`, add to `RESEARCH_CONNECTORS` |
| Change an AI prompt | `research/ai.py` (`understand`, `assess`, …) |
| Add or change an AI verifier | `AGENTS` and `NAMES` in `research/agents.py` |
| Change how much a vote counts / when a verdict is final | `verification/engine.py` + settings `AI_AGENT_WEIGHT_CAP`, `FINAL_MIN_HUMAN_VOTES` |
| Change who gets assigned | `verification/assignment.py` (`rank_verifiers`, `org_match`) |
| Change source credibility tiers | `research/credibility.py` |
| Change the look | `static/css/input.css`, `tailwind.config.js`, templates; then `manage.py buildcss` |
| Add/translate UI text | templates + `locale/kn_strings.py`; then `manage.py compilemo` |
