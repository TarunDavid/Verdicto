# Verdicto: AI-assisted, crowd-verified fact checking

Implements [PRD.md](PRD.md) on top of the original [SPEC.md](SPEC.md) build.

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
1. **Check a claim** (`/chat/`): paste a rumour, Kannada or English. The card shows live progress,
   then the **AI provisional assessment** (label, truth score, cited sources with stances, and what's
   established, assumed or conflicting). The status stays **PENDING VERIFICATION**.
2. The claim page lists the **auto-assigned verifiers** and why each was picked.
3. Log in as an assigned verifier → **Verify** (`/verify/`) → open the claim → vote with evidence.
4. After 3 weighted votes at 70% or more agreement, the claim is **Verified** (True / False / Partially true /
   Misleading / Unverified). The chat card, feed, archive (`/verdicts/`) and share page all update.

Demo logins are printed by `seed_demo` (one password, `DEMO_PASSWORD`).

## Where things live
| Area | Files |
|---|---|
| Chat + history | `chat/` (threads per user, or per browser session for guests) |
| AI research pipeline | `research/pipeline.py`, `research/ai.py` (Groq), `research/connectors.py` |
| Live sources | Groq Compound web search, Google News RSS, Google Fact Check API (optional key), Wikipedia, OpenAlex, Crossref. GDELT is implemented but off by default (it kept timing out). |
| Source credibility + truth score | `research/credibility.py`, `research/scoring.py` (a formula; the LLM never sets the score) |
| Safe fetching | `research/fetch.py` (http/https only, blocks private/internal IPs on every redirect, size + time limits) |
| Verifier assignment | `verification/assignment.py` (domain × language × reliability × workload × locality); `manage.py reassign_stale` for timeouts (run from cron) |
| Human verdict engine | `verification/engine.py` (weighted Beta-prior votes, unchanged from SPEC §6) |
| Feed ranking | `claims/ranking.py` (urgency × reach × domain risk × 24 h decay) |
| Moderation | AI holds claims about private individuals' personal lives; admins can hide/unhide in `/admin/` |

## Deliberate limits
- **Google Scholar and Web of Science are not used.** Scholar has no API and forbids scraping; Web of
  Science needs an institutional key. OpenAlex and Crossref cover scholarly literature legally.
- **Social media:** we don't scrape it (against platform terms). Social posts come in only when web
  search surfaces them, and they get the lowest credibility tier.
- The AI's quotes must appear verbatim in the source text, or they are dropped and the stance falls
  back to neutral. Page text is treated as untrusted data (prompt-injection guard).
- Research runs in an in-process background thread (SQLite, single server). For production, use a task
  queue as described in PRD §14.
