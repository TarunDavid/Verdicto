# PRD: AI-Assisted Crowd-Verified Fact-Check Board

**Team:** TENET · Avishkar'26 · Problem Statement 8
**Stack:** Django 5 · SQLite · Django templates + Tailwind + HTMX · **Groq API** for AI
**Status:** Draft v2 (pivot from a voting board to a chatbot with AI research and verifier review)

---

## 1. Summary
A chatbot where anyone pastes a forwarded message, a news item or a link they want checked. The AI immediately runs a **mini research pass** on live sources (news sites, local newspapers, research databases, fact-check databases, social posts) and returns:
- a **provisional verdict** (Likely True / Likely False / Misleading / Unclear),
- a **truth score** (0–100), and
- a cited summary of the evidence.

The result is clearly marked **"Pending: AI-provisional"** until human verifiers review it. The AI also **assigns the claim to the best-matched verifiers**, based on their domain, language and track record. Verifiers see the AI research, add their own evidence and vote. The **final verdict** comes from the weighted verifier votes. Every check is saved as a chat thread, and every verdict also gets a public page.

## 2. Problem
Rumours spread through WhatsApp in minutes, often in Kannada. Human fact-checking takes hours or days, and people have nowhere to check a claim *right now*. Pure AI answers are fast but can be wrong or hallucinated. Pure crowd checking is trustworthy but slow.

**Our answer:** AI gives a fast, cited, temporary answer, and accountable human verifiers give the final word.

## 3. Goals and non-goals
**Goals**
- G1. A provisional, cited AI answer within about 30 seconds of submission.
- G2. Every claim routed to suitable verifiers automatically.
- G3. Final verdicts decided by humans, weighted by verifier track record.
- G4. Public, ranked verdict feeds plus an archive of past verdicts.
- G5. Full transparency: sources, AI reasoning, verifier votes and a change log.

**Non-goals (for the hackathon)**
- AI never publishes a final verdict on its own.
- No scraping behind logins or paywalls, and no collecting personal data.
- No WhatsApp bot integration (roadmap).

## 4. Users and roles
| Role | Can do |
|---|---|
| **Citizen** (logged in or guest) | Chat with the bot, submit claims, see provisional and final verdicts, keep chat history (logged in only) |
| **Verifier** (admin-approved) | See assigned claims and the AI research, add evidence, vote TRUE / FALSE / MISLEADING / UNVERIFIED with a note |
| **Admin** | Approve verifiers, reassign claims, override a verdict with a reason, manage source lists |

Verifier profiles hold: domains (health, education, government, politics, science, technology, local news), languages (kn, en), district, and accuracy stats.

## 5. Core user flow
1. A citizen opens **Chat** and pastes text, a link or a screenshot (screenshot reading is optional).
2. The bot replies instantly: "Researching…", with live progress steps (searching news → reading sources → analysing).
3. **The AI research pipeline** runs (Section 7).
4. The bot posts its **research card**: provisional verdict, truth score, a short summary, 3–8 cited sources with stance (supports / refutes / neutral), and a status badge reading **PENDING VERIFICATION**.
5. The system **assigns verifiers** (Section 9) and notifies them on their dashboard.
6. Verifiers review the AI research, add evidence and vote.
7. Once the final-verdict rule is met (Section 10), the status changes to **VERIFIED** with the final verdict. The chat thread and the public page update, and the change is logged.
8. Anyone can share the public verdict link, which has a WhatsApp preview card.

## 6. Features (functional requirements)

### 6.1 Chatbot
- FR1. A chat interface with message history. Each claim check is a **thread** (like a chatbot conversation).
- FR2. Logged-in users see their past threads in a sidebar. Guests get a session-only thread.
- FR3. Users can ask follow-up questions in the same thread ("what does the health ministry say?"). The AI answers using the already-gathered sources and runs extra searches if needed.
- FR4. Input types: plain text, URL (the page is fetched and its text extracted), and optionally a screenshot (OCR via a vision-capable model if available; otherwise ask the user to paste the text).
- FR5. Language: accept Kannada and English input, and reply in the user's language.

### 6.2 AI research (provisional)
- FR6. Run automatically on submit, in the background, with progress shown live.
- FR7. Output: provisional verdict, truth score (0–100), confidence level, a short reasoning summary, cited sources with stance and a credibility tier, and detected domain + urgency.
- FR8. Always labelled "AI-provisional, not final. Awaiting human verification."
- FR9. Store the full research record (queries run, sources fetched, extracted snippets, model output) for verifiers and the audit trail.

### 6.3 Duplicate detection
- FR10. Before running research, check whether a similar claim exists (fuzzy matching + AI similarity). If one does, show the existing verdict and attach the new submission to it (incrementing its report count) instead of re-researching.

### 6.4 Verifier assignment (AI + scoring)
- FR11. The AI classifies the claim's domain(s), language and urgency.
- FR12. The system ranks eligible verifiers by match and track record, and assigns the top N (default 3).
- FR13. If an assigned verifier doesn't respond within a set time (e.g. 2 hours), the claim is reassigned to the next-ranked verifier.
- FR14. Admins can reassign manually.

### 6.5 Verification
- FR15. The verifier dashboard shows assigned claims sorted by importance, with the full AI research visible.
- FR16. The verifier votes with a verdict, a note and at least one evidence link (required for TRUE / FALSE / MISLEADING). They can mark AI sources as "useful" or "wrong".
- FR17. One vote per verifier per claim, enforced in the database.

### 6.6 Public verdict pages and feeds
- FR18. **Verdicts feed** (home): all claims, both pending and verified, ranked by importance and published time (Section 11). Filters: status, domain, language, district.
- FR19. **Past verdicts page:** an archive of finalised verdicts only, newest first, with search.
- FR20. **Public claim page:** claim, AI research card, verifier votes, final verdict, change log and share buttons.
- FR21. A WhatsApp share card with the verdict badge and an OG image.

### 6.7 Transparency and audit
- FR22. Verdict-change log: who, old → new, reason, timestamp. Append-only.
- FR23. Public verifier profile: accuracy per domain, number of verdicts, badges.
- FR24. Where the AI disagreed with the final human verdict, show it (useful for judges and for improving the AI).

## 7. AI research pipeline
The pipeline runs as a background job per claim, and each step updates the progress shown in chat.

| Step | What happens | Tool |
|---|---|---|
| 1. Understand | Extract the core checkable claim(s), entities, dates, location, language; classify domain + urgency | Groq LLM |
| 2. Plan queries | Generate 3–6 search queries (English + Kannada where relevant) | Groq LLM |
| 3. Search live sources | Query each source connector in parallel (Section 8), with timeouts | Source connectors |
| 4. Fetch + extract | Download the top results; extract the main article text; respect robots.txt; cache by URL | HTTP fetch + article extractor |
| 5. Assess each source | For each source: stance (supports / refutes / neutral / irrelevant), a key quote/snippet, publication date | Groq LLM |
| 6. Score | Compute the truth score from source stances, credibility tiers and recency (Section 10.1); not just the model's opinion | Deterministic formula |
| 7. Summarise | Write a short, neutral explanation that cites only the sources found | Groq LLM |
| 8. Save + assign | Save the research record and trigger verifier assignment | Django |

**AI rules**
- The AI may only cite sources that were actually fetched. Answers without sources produce "Unclear" with a low score.
- Scraped page content is **untrusted data**. Instructions inside a page are ignored (prompt-injection guard) and never followed.
- Groq models are configurable in `.env` (a fast model for classification, a stronger model for the summary).
- **Fallbacks:** if Groq or a source fails or times out, continue with whatever is available and say so. Never block the chat.

## 8. Data sources (real-time, real data, compliant)
Prefer **official APIs and RSS feeds**, and scrape only public pages whose terms allow it, following robots.txt.

| Category | Sources | Access method |
|---|---|---|
| Web search | A search API (e.g. Tavily, Brave Search, SerpAPI); Groq's built-in web-search models if available | API key in `.env` |
| National + local news | The Hindu, Deccan Herald, Indian Express, Prajavani, Vijay Karnataka, Udayavani, local Chikkamagaluru outlets | RSS feeds + fetching public article pages |
| Official sources | PIB (incl. PIB Fact Check), Karnataka government portals, VTU circulars, district websites, WHO/MoHFW for health | RSS / public pages |
| Fact-checkers | Google Fact Check Tools API (ClaimReview), BOOM, Alt News, Factly, Newschecker | API + RSS |
| Research / academic | **OpenAlex, Crossref, Semantic Scholar, PubMed** | Free official APIs |
| Encyclopedic | Wikipedia | Official API |
| Social media | Reddit (official API); public posts surfaced via web search | Official APIs only |
| News aggregation | GDELT | Free API |

**Important constraints (for the PRD and for judges)**
- **Google Scholar** has no public API and its terms forbid scraping. Use OpenAlex, Semantic Scholar and Crossref instead, which cover similar literature legally.
- **Web of Science** needs an institutional Clarivate API key. Include it only if AIT has one; otherwise list it as an optional connector.
- **X/Twitter, Instagram, Facebook:** scraping is against their terms and their APIs are paid or restricted. Rely on posts that appear in search results, plus Reddit's API.
- Every connector is a pluggable module with an on/off switch, a timeout (~8 s), rate limiting and URL caching.

## 9. Verifier assignment algorithm
For each claim, score every approved, active verifier:

**match score = domain match × language match × reliability × availability**
- **Domain match:** 1.0 for the primary domain, 0.6 for a related domain, otherwise not eligible.
- **Language match:** 1.0 if the verifier knows the claim's language, otherwise 0.5.
- **Reliability:** the Bayesian accuracy in that domain, (correct + 2) ÷ (total + 4). New verifiers start at 0.5; verified work email gives 0.6.
- **Availability:** drops as current open assignments rise (e.g. 1 ÷ (1 + open tasks)).
- **Locality bonus:** +10% if the district matches, for local claims.

Assign the top 3 (configurable). The AI's role is to classify domain, language, urgency and location. The ranking itself is deterministic and explainable, and each assignment stores the reason ("Health · Kannada · 0.83 accuracy").

## 10. Verdict logic

### 10.1 AI truth score (provisional)
- Each source gets a credibility tier weight: official / fact-checker 1.0, established news 0.8, academic 0.9, Wikipedia 0.6, social / forum 0.3.
- Recency factor: recent sources count more for breaking claims.
- **Truth score = 100 × (support weight) ÷ (support weight + refute weight).** If there's too little evidence, it's marked "Unclear" with low confidence.
- The LLM explains the score but does not set it, which keeps it reproducible and explainable.

### 10.2 Final verdict (human)
- Verifier vote weight = their Bayesian reliability in that domain (Section 9).
- **Final when ≥ 3 votes and the top option has ≥ 70% of the weighted votes.** Until then, the status stays PENDING and shows the AI provisional verdict plus the current vote leaning.
- Admin override is allowed with a mandatory logged reason.
- When a claim is finalised, update each verifier's accuracy stats, with no double counting if the verdict changes later.

### 10.3 Statuses
`RESEARCHING → PENDING (AI provisional) → UNDER REVIEW (votes in) → VERIFIED (final)`, plus `DUPLICATE` and `REJECTED` (spam).

## 11. Ranking the verdicts feed
**importance = urgency × reach × domain risk × recency decay**
- **Urgency** (AI): high / medium / low → 3 / 2 / 1
- **Reach:** log(1 + number of times reported)
- **Domain risk:** health, safety, violence and exams rank higher than entertainment
- **Recency decay:** halves every ~24 hours

Default sort is by importance, then published time. Users can switch to "Latest". The past verdicts page is sorted by verified time.

## 12. Pages
1. **Chat** (home for citizens): conversation, past-threads sidebar, research cards
2. **Verdicts feed:** ranked list of all claims with status badges
3. **Past verdicts:** finalised verdicts archive with search
4. **Claim page:** public, shareable, with the full transparency view
5. **Verifier dashboard:** assigned queue, AI research panel, vote form
6. **Verifier profile:** public accuracy and history
7. **Admin:** Django Admin for verifier approval, reassignment and source settings
8. Login / sign-up, and the language switch (English / Kannada)

## 13. Data model (entities)
- **User** (role, preferred language)
- **VerifierProfile** (domains, languages, district, approved, work-email verified)
- **ChatThread** (owner or session, title, created)
- **ChatMessage** (thread, sender: user / bot, content, linked claim, timestamp)
- **Claim** (text, normalised text, source URL, language, domains, district, urgency, importance score, status, final verdict, final confidence, report count, duplicate-of, share slug, timestamps)
- **ResearchRun** (claim, status, queries, provisional verdict, truth score, confidence, summary, model names, duration, errors)
- **Source** (research run, URL, title, publisher, type/tier, published date, stance, snippet, fetched at)
- **Assignment** (claim, verifier, match score, reason, status: assigned / done / expired, timestamps)
- **Vote** (claim, verifier, verdict, note, weight at vote, scored-as) with a **unique (claim, verifier)** constraint
- **Evidence** (vote, URL, is-official)
- **VerdictLog** (claim, old → new status/verdict, reason, changed by, timestamp), append-only
- **VerifierStat** (verifier, domain, language, correct, total)

## 14. Non-functional requirements
- **Speed:** provisional answer within ~30 s on average, with progress shown live. Each source connector times out at ~8 s.
- **Background jobs:** for the hackathon, a simple in-process background worker with HTMX polling; for production, a task queue (Celery/RQ + Redis).
- **Security:**
  - role-based access, CSRF protection, rate limits (especially on chat submissions, which cost API calls)
  - sanitised input; http/https-only URLs that block `javascript:` and `data:`
  - safe fetching: only http/https, block internal/private IPs (SSRF guard), size limits
- **Secrets:** `GROQ_API_KEY`, search API key and other keys only in `.env`.
- **Cost control:** cache research by normalised claim and URL; limit tokens per run; reuse results for duplicates.
- **Reliability:** if the AI or a source fails, show a partial result and send the claim to verifiers anyway.
- **Compliance:** follow robots.txt and site terms; no personal-data scraping; store only snippets plus links, never full copyrighted articles.
- **Explainability:** every score, assignment and verdict shows its inputs.

## 15. Success metrics
- Time to provisional answer (target < 30 s)
- Time to verified verdict (target: hours, not days)
- % of AI provisional verdicts that match the final human verdict
- % of verdicts with ≥ 2 credible sources
- Verifier response time and reassignment rate

## 16. Risks and mitigations
| Risk | Mitigation |
|---|---|
| AI hallucination | Cite fetched sources only; score from a formula; human final verdict |
| Prompt injection from scraped pages | Treat page text as data; strict system prompt; never execute instructions from content |
| Sites block scraping | Prefer APIs/RSS; caching; graceful skip |
| Legal/ToS issues | No Google Scholar/social scraping; official APIs; robots.txt |
| Groq rate limits | Caching, retries with backoff, smaller model for simple steps |
| Users trusting the AI score too much | Clear "provisional" label; pending status until verified |
| Verifier gaming | Admin approval, one vote per claim, Bayesian weights, public logs |

## 17. Milestones (hackathon order)
1. Chat UI + threads + claim creation + duplicate check
2. Groq integration: claim understanding + query planning
3. Source connectors: search API, news RSS, OpenAlex, Google Fact Check, Wikipedia
4. Source assessment + truth score + research card in chat
5. Verifier assignment + dashboard + voting + final verdict logic
6. Verdicts feed (ranked) + past verdicts page + public claim page + share card
7. Polish: Kannada UI, seed data, tests (scoring, assignment, one-vote constraint, URL safety)

## 18. Demo script
1. Paste a Kannada rumour ("schools closed tomorrow due to heavy rain") into the chat.
2. Show the live research steps, then the research card: "Likely False · 18/100 · 5 sources · PENDING".
3. Show the auto-assigned verifiers and the reason for each assignment.
4. Log in as a verifier, review the AI research, and vote with evidence.
5. After the third vote, the status flips to VERIFIED · FALSE, and the chat, feed and public page all update.
6. Open the share link on a phone.
