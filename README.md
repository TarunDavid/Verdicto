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

Log in with a **phone number**. `seed_demo` prints all demo phones; there is one password,
`DEMO_PASSWORD`. Django admin also accepts the username `admin`.

## Where things live
| Area | Files |
|---|---|
| Sign-up / login | `accounts/forms.py` (role, name, phone, interests, verifier details), `accounts/backends.py` (phone login); site-wide `LoginRequiredMiddleware` (claim and share pages stay public) |
| Community votes | `verification/engine.py` (`community_vote_weight`: 0.05 each, max 20% combined), `CommunityVote` model |
| Chat + history | `chat/` (threads per user; system messages when a claim is finalised) |
| AI research pipeline | `research/pipeline.py`, `research/ai.py` (Groq), `research/connectors.py` |
| Live sources | Groq web search (gpt-oss `browser_search`), Google News RSS, Google Fact Check API (optional key), Wikipedia, OpenAlex. Crossref and GDELT are implemented but off by default (irrelevant results / timeouts). |
| Source credibility + truth score | `research/credibility.py`, `research/scoring.py` (a formula; the LLM never sets the score) |
| Safe fetching | `research/fetch.py` (http/https only, blocks private/internal IPs on every redirect, size + time limits) |
| Verifier assignment | `verification/assignment.py` (domain × language × reliability × workload × locality); `manage.py reassign_stale` for timeouts (run from cron) |
| Human verdict engine | `verification/engine.py` (weighted Beta-prior votes, unchanged from SPEC §6) |
| Feed ranking | `claims/ranking.py` (urgency × reach × domain risk × 24 h decay) |
| Moderation | AI holds claims about private individuals' personal lives; admins can hide/unhide in `/admin/` |

## Deliberate limits
- Not built yet from PRD2: screenshot upload/OCR (no Groq vision model on this account), phone OTP,
  work-email OTP, the "useful / wrong" source buttons, a profile page, Kannada UI translations, and
  community-accuracy tracking.
- **Google Scholar and Web of Science are not used.** Scholar has no API and forbids scraping; Web of
  Science needs an institutional key. OpenAlex and Crossref cover scholarly literature legally.
- **Social media:** we don't scrape it (against platform terms). Social posts come in only when web
  search surfaces them, and they get the lowest credibility tier.
- The AI's quotes must appear verbatim in the source text, or they are dropped and the stance falls
  back to neutral. Page text is treated as untrusted data (prompt-injection guard).
- Research runs in an in-process background thread (SQLite, single server). For production, use a task
  queue as described in PRD §14.
