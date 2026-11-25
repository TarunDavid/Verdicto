# SPEC: Crowd-Verified Fact-Check Board (Team TENET, Avishkar'26)

You are building a working full-stack web app for a 24-hour hackathon. I am the only developer. Work in phases, keep the app runnable after every phase, and stop after each phase so I can test it.

## 1. What we are building
Forwarded WhatsApp messages spread local rumours (health scares, fake circulars, exam "leaks") faster than anyone can verify them, especially in Kannada. Our app:
1. Citizens submit a doubtful claim as text, a screenshot or a link, with its language.
2. Approved verifiers, tagged by language and domain, vote TRUE / FALSE / UNVERIFIED with evidence links and a short note.
3. A weighted verdict engine combines the votes using each verifier's track record.
4. Every claim gets a public result page and a WhatsApp-shareable card, so the correction travels back to the group where the rumour started.
5. Everything is transparent: public verifier credibility profiles and a verdict-change log with reasons.

Required flow for the demo: **submit claim → verifiers respond → weighted verdict shown → shareable result page.**

## 2. Hard constraints (from the problem statement; all are judged)
- One vote per verifier per claim, enforced in the **database** (UniqueConstraint).
- Every verdict change is logged with a reason (append-only log).
- Sanitize claim text and evidence URLs. Allow only `http`/`https` URLs, and explicitly reject `javascript:` and `data:` links.
- Role-based access control (citizen / verifier / admin), server-side validation, and structured error handling.
- Secrets live only in `.env` (provide `.env.example`; never commit `.env`).
- Use seed scripts and synthetic data (Faker). No static mockups.

## 3. Tech stack (do not add other packages without asking me)
- Python 3.11+, **Django 5**, **SQLite** (Django ORM)
- Frontend: Django templates + **Tailwind CSS (CDN)** + **HTMX (CDN)**. No Node build step.
- i18n: Django i18n (`gettext`), languages **English (en)** and **Kannada (kn)**, with a language switcher
- `python-decouple` (settings from `.env`)
- `rapidfuzz` (duplicate detection)
- `nh3` (HTML sanitising of user text)
- `dnspython` (MX check for work emails)
- `django-ratelimit` (rate limits)
- `Pillow` (image validation + OG share image)
- `Faker` (seed data)
- `google-genai` (Gemini; optional, the app must work fully without a key)
- `django-allauth` (only for Sign in with LinkedIn via OpenID Connect; optional, the app must work without LinkedIn credentials)

## 4. Project structure
```
factcheck/            # project settings
accounts/             # User, roles, verifier onboarding, background check
claims/               # Claim, duplicate detection, feed, share page
verification/         # Vote, Evidence, verdict engine, VerdictLog, VerifierStat
insights/             # leaderboard, stats dashboard, trending
ai/                   # Gemini service with safe fallbacks
templates/  static/  locale/  fixtures/official_notices.json
```

## 5. Data models
- **User** (custom, extends AbstractUser): `role` (citizen / verifier / admin), `preferred_language`.
- **VerifierProfile**: user (1:1), `languages` (JSON list, e.g. ["kn","en"]), `domains` (JSON list: health, education, government, local-news, technology, other), `district`, `status` (pending / approved / rejected), `linkedin_url`, `linkedin_linked` (bool), `work_email`, `work_email_verified` (bool), `org_domain`, `proof_document` (file), `approved_by`, `approved_at`.
- **WorkEmailOTP**: user, email, `code_hash`, `expires_at`, `attempts`.
- **Claim**: `text`, `normalized_text`, `screenshot` (optional), `source_url` (optional), `language`, `domain`, `district`, `summary` (optional, from AI), `status` (UNDER_REVIEW / FINAL), `verdict` (TRUE / FALSE / UNVERIFIED / null), `confidence` (float), `submitted_by`, `submission_count` (times reported), `duplicate_of` (FK self, null), `share_slug` (unique short id), `embedding` (JSON, null), `urgency` (low / medium / high), `created_at`, `finalized_at`.
- **Vote**: claim, verifier, `verdict` (TRUE / FALSE / UNVERIFIED), `note` (max 500 chars), `weight_at_vote`, `scored_as` (the final verdict this vote was last scored against; null if not scored), `created_at`. **UniqueConstraint(claim, verifier).** Votes cannot be edited or deleted by users.
- **Evidence**: vote (FK), `url`, `is_official` (bool, true for trusted domains).
- **VerdictLog** (append-only: block updates and deletes in `save()`/`delete()` and in the admin): claim, `old_status`, `new_status`, `old_verdict`, `new_verdict`, `old_confidence`, `new_confidence`, `reason` (required), `changed_by` (null = system), `created_at`.
- **VerifierStat**: verifier, `domain`, `language`, `correct`, `total`. Unique on (verifier, domain, language).
- **OfficialNotice** (local data): `title`, `source` (e.g. VTU, Karnataka Govt, Chikkamagaluru District, PIB), `url`, `published_on`, `text`, `language`. Loaded from `fixtures/official_notices.json`.
- **Report** (abuse): claim, reporter, reason, created_at.
- **ClaimFollow**: user, claim (unique together).

## 6. Weighted verdict engine (core innovation; implement carefully and unit-test it)
Put it in `verification/engine.py` as pure functions so it is easy to test.

**Verifier weight** for a claim's (domain, language):
- `weight = (correct + a) / (total + a + b)`, using a Beta prior with `a = 2`, `b = 2`, so a new verifier starts at **0.5**.
- **Trust bonus:** if `work_email_verified` is true, use `a = 3`, so a new verified verifier starts at **0.6**.
- Look up the stat for (domain, language). If there is none, use the sum of the verifier's stats for that domain across languages. If there is still none, use the prior.

**Verdict:**
- Sum the weights per option (TRUE / FALSE / UNVERIFIED).
- `confidence = top_option_weight / total_weight`.
- If **votes ≥ 3 and confidence ≥ 0.70**, set status FINAL and verdict = the top option. Otherwise status is UNDER_REVIEW, and the page shows the currently leading option.
- Recompute after every vote. If status or verdict changes, write a VerdictLog entry with a system reason, e.g. "Vote by <verifier> raised FALSE confidence to 75%".

**Admin override:** an admin can set the verdict, and must give a reason (logged).

**Track record (no double counting):**
- When a claim becomes FINAL, or its final verdict changes, update VerifierStat for every voter.
- If `vote.scored_as` is set, first undo that previous scoring.
- Then apply: `total += 1`, and `correct += 1` if `vote.verdict == final verdict`. Set `scored_as`.

**Explainability:** the claim page shows a breakdown table: verifier, record, weight, vote, and the arithmetic (e.g. "FALSE = 0.83 + 0.67 = 1.50; TRUE = 0.50; confidence = 75%").

**Required unit test (worked example):**
- Verifier A: 8/8 correct → weight 0.83, votes FALSE.
- Verifier B: 2/2 correct → weight 0.67, votes FALSE.
- Verifier C: new → weight 0.50, votes TRUE.
- Expected result: FALSE, confidence 0.75, status FINAL.

## 7. Duplicate detection
- `normalize(text)`: lowercase; strip URLs, emojis, punctuation and "forwarded"/"forwarded many times" boilerplate; collapse whitespace. Keep Kannada characters.
- While the user types, an HTMX endpoint shows "Similar claims already checked": rapidfuzz `token_set_ratio ≥ 80` against recent claims. Also match an exact `source_url`.
- If the user picks an existing claim, increment its `submission_count` and redirect them to it instead of creating a duplicate.
- **AI upgrade** (if `GEMINI_API_KEY` is set): store a Gemini embedding per claim and also match on cosine similarity ≥ 0.85, so Kannada and English versions of the same rumour match. Always fall back to rapidfuzz.

## 8. Verifier background check (consent-based; NEVER scrape LinkedIn or any personal data)
1. **Work-email verification:**
   - Reject free providers (gmail, yahoo, outlook, hotmail, rediffmail, proton, icloud, etc.).
   - Check that the domain has MX records with `dnspython`.
   - Send a 6-digit OTP. Store only a hash, expire it after 10 minutes, and allow at most 5 attempts.
   - Rate-limit OTP requests.
   - In dev, use the console email backend (the OTP prints in the terminal).
   - On success, set `work_email_verified` and `org_domain`.
2. **Sign in with LinkedIn (optional):** use django-allauth's LinkedIn OpenID Connect provider. It only gives name, email and photo, so set `linkedin_linked` and store the profile URL the verifier provides for the admin to check manually. If no LinkedIn credentials are configured, hide the button and just accept a profile URL.
3. **Proof upload:** an ID card or appointment letter (image or PDF, max 5 MB), reviewed by the admin.
4. **Admin approval** in Django Admin, with approve/reject actions.
5. **Trust badges** on profiles and votes: "Work email verified · <org>", "LinkedIn linked", "New verifier".

## 9. Local data: official source check
- Seed `fixtures/official_notices.json` with about 15 realistic **synthetic** notices: VTU exam schedules, Karnataka school holiday orders, Chikkamagaluru district advisories, health department advisories. Mark them clearly as demo data.
- On the claim page and the submit form, show "Matching official notices" (rapidfuzz, plus AI similarity if available).
- **Trusted evidence domains:** `.gov.in`, `.nic.in`, `vtu.ac.in`, `pib.gov.in`, plus known fact-checkers. Links on these domains get `is_official = True` and an "Official source" tag.
- Do **not** scrape live websites in the demo path. Everything comes from the fixture.

## 10. AI features (in `ai/service.py`; every function returns a safe fallback on any error or when there is no key)
- Read env vars `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`) and `GEMINI_EMBED_MODEL` (default `gemini-embedding-001`).
- `extract_text_from_image(screenshot)`: OCR plus language detection for uploaded screenshots. Pre-fill the claim text.
- `embed(text)`: used for multilingual duplicate detection.
- `classify(text)`: returns `{summary (one neutral line), domain, urgency}` and is used to route claims to verifiers with matching tags.
- **AI never decides the verdict.** Humans do. Show the AI summary with an "AI-generated" label.
- Use a timeout of about 10 s, and never block form submission if AI fails.

## 11. Pages
- **Home / feed:** trending (by `submission_count` and recent activity) and latest. Filters: language, domain, district, status/verdict. Search.
- **Submit claim** (logged-in citizens): text, optional screenshot, optional link, language, district. Shows the live duplicate check.
- **Claim page:** status/verdict badge, confidence meter, evidence list, verdict breakdown table, verdict-change log, matching official notices, status timeline, follow button, report button, share buttons.
- **Public share page** `/c/<share_slug>/`: no login needed, with Open Graph tags. `og:title` like "FALSE: <summary>"; `og:image` is generated with Pillow at `/c/<share_slug>/og.png` (verdict badge, claim snippet, confidence). Add a WhatsApp button: `https://wa.me/?text=<encoded message + link>`. Use Noto Sans Kannada for Kannada text; if shaping looks broken, put English text on the image.
- **Verifier dashboard:** a queue of claims matching the verifier's languages/domains that they haven't voted on yet, urgent ones first. Vote form: verdict, note, 1–3 evidence URLs (at least 1 required for TRUE/FALSE).
- **Verifier onboarding:** apply, then work-email OTP, LinkedIn/profile URL, and proof upload. Shows status.
- **Public verifier profile:** badges, accuracy per domain/language, votes, history.
- **Leaderboard:** top verifiers by Bayesian score (minimum 3 scored votes).
- **Stats dashboard:** claims checked, % final, average time to verdict, verdict split, top domains.
- **"Listen to verdict" button:** uses the browser `speechSynthesis` with `kn-IN` (falls back to `en-IN`).
- Language switcher (en/kn) in the navbar.

## 12. Security and quality checklist
- Role decorators/mixins on every view. Citizens can't vote, verifiers can't vote until approved, and only admins can override.
- URL validation: `URLValidator(schemes=["http","https"])` plus an explicit block of `javascript:`/`data:` (case-insensitive, trimmed). Same for `source_url` and evidence.
- Sanitise user text with `nh3` (strip all tags). Never use `|safe` on user content.
- Uploads: validate the real image type with Pillow, max 5 MB, randomised filenames.
- `django-ratelimit` on: submit claim, vote, OTP request/verify, report, login.
- Structured errors: HTMX/JSON endpoints return `{"error": {"code": "...", "message": "...", "fields": {...}}}` with correct HTTP status. Provide custom 400/403/404/429/500 templates.
- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `GEMINI_API_KEY` and LinkedIn credentials come from `.env` via decouple. Add `.env` to `.gitignore`.
- CSRF stays on (HTMX: send the token via `hx-headers`).
- Use the logging module for errors; never show stack traces to users.

## 13. Seed data: `python manage.py seed_demo` (idempotent, `--reset` flag)
- 1 admin, 3 citizens, 8 verifiers with varied domains/languages/districts, varied track records and a mix of badges.
- About 25 claims, about 10 of them in Kannada, modelled on real local rumour patterns: fake school-holiday circular, "VTU exams postponed", miracle health cure, child-kidnapping alert, ration-card deadline, fake job notice. Include some duplicates, a mix of FINAL and UNDER_REVIEW, votes with evidence, and existing VerdictLog entries.
- Load the official notices fixture.
- Print all demo logins at the end. Use a single demo password from `.env` (`DEMO_PASSWORD`).

## 14. Design
- Dark theme with a red accent `#E0383E` (matches our presentation), clean cards, mobile-first. WhatsApp users are on phones.
- Verdict colours: FALSE red, TRUE green, UNVERIFIED amber, UNDER REVIEW grey.
- All UI strings wrapped for translation. Provide a Kannada `.po` file for the main UI.

## 15. Phases (stop after each, give me the run/test commands, update `PROGRESS.md`, and make a git commit)
1. **Scaffold:** project, apps, settings from `.env`, custom User, all models, migrations, admin registration, role decorators, base layout (Tailwind + HTMX + navbar + language switcher), `seed_demo`, auth (signup/login/logout).
2. **Claims:** submit form (validation + sanitising + upload), normalisation, live duplicate check, feed with filters, claim page.
3. **Verification + engine:** verifier dashboard, vote + evidence, `engine.py`, VerdictLog, VerifierStat track record, admin override, breakdown table, public verifier profile. **Unit tests** for the engine (including the worked example), one-vote constraint, URL blocking, RBAC.
4. **Sharing + i18n:** public share page, OG tags + Pillow OG image, WhatsApp button, Kannada translations.
5. **Background check:** onboarding flow, work-email OTP, badges + trust bonus in the engine, LinkedIn (optional), proof upload, admin actions.
6. **AI + local data:** Gemini OCR, embeddings, classify, with fallbacks; official notice matching; trusted-domain tagging.
7. **Extras:** trending, leaderboard, stats dashboard, report, follow, listen button, status timeline.
8. **Polish:** error pages, rate limits check, `README.md` (setup, `.env.example`, demo logins, demo script), and a full test run.

## 16. How to work
- First, show me a short plan and the file tree. Then start Phase 1.
- Keep code simple and readable. I need to explain it to judges.
- After each phase: run `python manage.py check`, `makemigrations`, `migrate` and `test`, fix any failures, then tell me exactly how to run it and what to click to test.
- If something is ambiguous, choose the simplest option that satisfies the constraints and note it in `PROGRESS.md`.
