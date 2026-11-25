# PRD v3: TENET Fact-Check Assistant (Chat-first)

**Team:** TENET · Avishkar'26 · Problem Statement 8
**Stack:** Django 5 · SQLite · Django templates + Tailwind + HTMX · Groq API (AI)
**Changes from v2:** a chat-first home, role-based sign-up (Regular user / Verifier), community votes from regular users with low weight, two top-level sections ("Check a Claim" and "News & Verdicts"), and a UI based on the attached design.

---

## 1. Summary
Users land on a **login / sign-up page** and choose whether they are a **Regular user** or a **Verifier**. After logging in, they see a **chatbot interface** ("Check a Claim") where they paste a forwarded message, a news item or a link. The AI runs a quick live research pass and replies with a **provisional verdict and truth score**, marked **PENDING** until verifiers confirm it.

- **Regular users** check claims and give their own opinion. Their votes count only a little, because we don't know their background.
- **Verifiers** see the AI research as a reference and give expert verdicts. Their votes are weighted by their accuracy and past verdicts.

The feed of claims and verdicts lives in a separate **"News & Verdicts"** section. It is **not** shown on the chat home.

## 2. Goals
- G1. A chat-first experience: checking a claim feels like talking to an assistant.
- G2. Clear roles: anyone can ask, and experts decide.
- G3. Weighted, explainable final verdicts: verifiers count most, regular users count a little.
- G4. A clean separation: chat for checking, a feed for browsing verdicts.
- G5. A UI that follows the attached design (dark, glassy, sidebar + centred chat).

## 3. Roles and permissions

| Capability | Regular user | Verifier (approved) | Admin |
|---|---|---|---|
| Check a claim with the chatbot | ✅ | ✅ | ✅ |
| See the AI provisional verdict on their own checks | ✅ | ✅ | ✅ |
| Give their own verdict (community vote) | ✅ (low weight) | n/a, votes as verifier | ✅ |
| Verifier queue: claims assigned to them | ❌ | ✅ | ✅ |
| Full AI research panel (all sources, stances, reasoning) while voting | ❌ | ✅ | ✅ |
| Verifier vote with evidence (high, accuracy-based weight) | ❌ | ✅ | ✅ |
| Browse News & Verdicts | ✅ | ✅ | ✅ |
| Approve verifiers, reassign claims, override verdicts | ❌ | ❌ | ✅ |

**Pending verifier:** someone who signed up as a verifier but isn't approved yet acts as a regular user. The app shows a "Verification pending" banner.

## 4. Entry flow: login and sign-up

### 4.1 First screen
Anyone who isn't logged in sees the **Login / Sign-up page** first. There's no access to chat or the feed without an account. Public share links to a single verdict page still open without login, so WhatsApp sharing keeps working.

### 4.2 Sign-up
- **Step 1, choose a role:** two large cards, **"I want to check claims"** (Regular user) and **"I want to verify claims"** (Verifier).
- **Step 2, details (both roles):**
  - Name (required)
  - Phone number (required, Indian format +91, validated, unique)
  - Field(s) of interest (required, multi-select): Health, Education, Government & Policy, Politics, Science, Technology, Local news, Finance, Other
  - Preferred language: English / Kannada
  - Password (or phone OTP; see 4.4)
- **Step 3, verifiers only (extra details for trust):**
  - Profession / role (e.g. Doctor, Teacher, Software Engineer, Journalist)
  - Organisation (optional)
  - Work email (optional; verified by OTP, gives a "Work email verified" badge and a higher starting weight)
  - LinkedIn profile URL (optional; checked manually by an admin, never scraped)
  - Languages they can verify in
  - District (optional; helps with local claims)
- **Step 4, consent:** "Your name and verifier stats are public; your phone number is never shown."

### 4.3 After login
- **Regular user** → lands on **Check a Claim** (the chatbot home).
- **Verifier** → also lands on **Check a Claim**, with a third top tab, **"Verify Queue (n)"**, that shows a count of pending assignments.
- **Admin** → Check a Claim plus a link to the admin panel.

### 4.4 Authentication (hackathon scope)
- Login with **phone number + password**.
- Phone OTP is optional and on the roadmap. In development, OTP codes print to the server console.
- Rate-limit login and sign-up. Phone numbers are stored privately and never shown on public pages.

## 5. Information architecture

**Top bar (all logged-in pages):** logo (our own) · **Check a Claim** · **News & Verdicts** · (**Verify Queue** for verifiers only) · language switch (EN / ಕನ್ನಡ) · profile avatar menu (Profile, Settings, Logout).

| Section | Contains |
|---|---|
| **Check a Claim** (default) | The chatbot: new check, chat threads, AI research cards, community vote |
| **News & Verdicts** | Sub-tabs: **Most important** · **Latest** · **Past verdicts** (finalised only). This is the claim feed from screenshot 2. |
| **Verify Queue** (verifiers) | Assigned claims, AI research panel, verifier vote form |
| Claim page | Full public view of one claim (opened from chat, the feed or a share link) |
| Profile | User info, field of interest; for verifiers, badges and accuracy stats |

**Rule:** the news feed must **not** appear on the Check a Claim screen. It is only reachable through the News & Verdicts tab.

## 6. Check a Claim: the chatbot UI
Layout follows **design 1** (dark theme, rounded glass panels, soft gradient glow). Use our own name, logo and colours; the design's brand and orb graphic are references only.

### 6.1 Left sidebar
- Logo + search icon (search your chats)
- **"+ New check"** button (like "New Chat")
- **Navigation:** Check a Claim · News & Verdicts · (Verify Queue) · My votes
- **"Your checks":** a list of the user's past chat threads, titled from the claim (e.g. "VTU exams postponed?"), with a small status dot (grey = pending, red = false, green = true, amber = misleading)
- **Bottom card:** for regular users, "Become a verifier" (a call to action, in place of the design's "Upgrade to Pro"); for pending verifiers, "Verification pending"

### 6.2 Main area: empty state (new check)
- A centred glowing emblem (our own graphic)
- Small caps "WELCOME BACK, <NAME>"
- Headline: **"What do you want to check today?"**
- **Input box** with the placeholder "Paste a forwarded message, news or link…"
  - **+** button: attach a screenshot or image
  - **"Sources" dropdown** (in place of "Tools"): News, Official, Fact-checkers, Research, All (default)
  - **"Deep Research" toggle** (in place of "Deep Think"): more queries and sources, slower
  - **Voice** button (optional; browser speech-to-text, en-IN / kn-IN)
  - **Send** button
- **Three suggestion cards below** (in place of Image / Video / Dev):
  1. **Check a forward:** "Paste a WhatsApp message"
  2. **Check a link:** "Verify a news article URL"
  3. **Check a screenshot:** "Upload an image of a message"

### 6.3 Main area: conversation
- The user's message appears as a bubble.
- The bot shows **live progress steps**: Understanding claim → Searching sources → Reading articles → Analysing.
- **The bot's research card:**
  - Provisional verdict chip (Likely True / Likely False / Misleading / Unclear) + **truth score 0–100** (a meter)
  - Status badge: **PENDING VERIFICATION** (or VERIFIED once final)
  - A 2–4 line neutral summary
  - **Sources** (3–8): publisher, title, date, stance tag (supports / refutes / neutral), link
  - A label: "AI-provisional. The final verdict comes from verified experts."
  - Buttons: **Give your verdict** · **View claim page** · **Share**
- **Community vote widget (regular users):** True / False / Misleading / Not sure, plus an optional one-line reason. One vote per user per claim, which they can change until the claim is finalised.
- **Follow-up questions** in the same thread ("what did PIB say?") get answers grounded in the gathered sources.
- **Live updates:** when verifiers finalise the claim, the thread shows a system message: "✅ Verified: FALSE (82% confidence) by 3 verifiers."

### 6.4 Duplicates
If the claim already exists, the bot replies: "This has been checked before", shows the existing verdict card, and adds the user's report to it (report count +1). It does not re-run research unless the user asks to "re-check with latest sources".

## 7. News & Verdicts (feed)
Styled like **screenshot 2**: dark cards with a verdict chip, urgency, language, domain, district, report count and time, plus the claim text.
- **Most important** (default): sorted by importance score (Section 10.3), then time.
- **Latest:** sorted by published time.
- **Past verdicts:** only finalised verdicts, newest first, with search.
- **Filters:** verdict/status, domain, language, district.
- **Card chips:** `PENDING` (grey, with "AI: Likely False · 18/100" in small text), `FALSE` (red), `VERIFIED TRUE` (green), `MISLEADING` (amber), and `URGENT` (red text).
- Clicking a card opens the **claim page**.

## 8. Verify Queue (verifiers)
- A list of claims **assigned to this verifier** (AI routing, Section 10.2), sorted by importance, with a time-left indicator.
- **The review screen has two panes:**
  - **Left:** claim, attachments, report count, community vote summary (e.g. "Community: 62% False, 41 votes")
  - **Right:** **AI research panel**: provisional verdict, truth score, every source with its stance, snippet and credibility tier, and the AI's reasoning. Each source has "useful / wrong" buttons.
- **Vote form:** True / False / Misleading / Unverified, a note (required), and evidence links (≥ 1 required for True / False / Misleading).
- One vote per verifier per claim, enforced in the database.
- Verifiers can also open any claim from News & Verdicts that matches their fields, even if it wasn't assigned to them.

## 9. Claim page (public)
- The claim, its status and final verdict, confidence, the AI research card, verifier votes (name, badge, weight, verdict, note, evidence), the community vote summary, and the verdict-change log.
- Share buttons: WhatsApp (with a preview card) and copy link.
- Works without login, so share links work for anyone.

## 10. Logic

### 10.1 AI research (provisional)
Unchanged from v2:
- Groq LLM extracts the claim and plans queries.
- Live source connectors: a search API, news RSS (national + Kannada outlets), official sources (PIB, state and district portals, VTU), the Google Fact Check Tools API, OpenAlex / Semantic Scholar / Crossref / PubMed, Wikipedia, and the Reddit API.
- Pages are fetched and their text extracted. Each source's stance is labelled.
- **Truth score is computed by a formula:** 100 × support weight ÷ (support + refute weight), with source-credibility and recency weights.
- The AI never sets the final verdict.
- Scraped text is treated as untrusted data (prompt-injection guard).
- No scraping of Google Scholar or social media platforms; only official APIs.
- Every source and AI step has a timeout and a fallback.

### 10.2 AI verifier assignment
- The AI classifies the claim's domain, language, urgency and district.
- **Verifier match score** = domain match × language match × reliability × availability (+ a locality bonus).
- **Reliability** = (correct + 2) ÷ (total + 4), per domain. It starts at 0.5, or 0.6 with a verified work email.
- Assign the top 3. Reassign if there's no response within 2 hours. Each assignment stores its reason (e.g. "Health · Kannada · 0.83").

### 10.3 Importance (feed ranking)
**importance = urgency (3 / 2 / 1) × log(1 + reports) × domain risk × recency decay** (halves every ~24 h)

### 10.4 Final verdict: weighted votes
- **Verifier weight** = their reliability for the claim's domain (0.5–1.0).
- **Regular user weight** = **0.05 each**, and **all community votes together are capped at 20% of the total weight**. This lets many users show a public signal but stops brigading from overruling experts.
- **The verdict becomes final when:**
  - there are **≥ 3 verifier votes**, **and**
  - the top option has **≥ 70%** of the total weighted votes.
- Until then, the status stays **PENDING**, and the claim shows the AI provisional verdict plus the current leaning.
- Admins can override with a mandatory reason. Every change goes into the append-only **VerdictLog**.
- When a claim is finalised, update the verifiers' accuracy stats. Regular users' "community accuracy" is tracked too, as a future path to becoming a verifier, but it doesn't change their weight in this version.

### 10.5 Statuses
`RESEARCHING → PENDING (AI provisional) → UNDER REVIEW (verifier votes in) → VERIFIED (final)`, plus `DUPLICATE` and `REJECTED`.

## 11. Data model (entities)
- **User:** name, phone (unique, private), role (regular / verifier / admin), fields of interest, preferred language, password
- **VerifierProfile:** profession, organisation, work email + verified flag, LinkedIn URL, languages, district, status (pending / approved / rejected), approved by / at
- **ChatThread:** user, title, created; **ChatMessage:** thread, sender (user / bot / system), content, linked claim, created
- **Claim:** text, normalised text, URL, attachment, language, domains, district, urgency, importance, status, final verdict, confidence, report count, duplicate-of, share slug, timestamps
- **ResearchRun:** claim, provisional verdict, truth score, confidence, summary, queries, model, duration, errors; **Source:** research run, URL, publisher, tier, date, stance, snippet
- **Assignment:** claim, verifier, match score, reason, status, due time
- **VerifierVote:** claim, verifier, verdict, note, weight at vote, scored-as, with **unique (claim, verifier)**; **Evidence:** vote, URL, is-official
- **CommunityVote:** claim, user, verdict, reason, with **unique (claim, user)**
- **VerdictLog** (append-only), **VerifierStat** (verifier, domain, language, correct, total)

## 12. Non-functional requirements
- **Design:** dark theme, glass panels, rounded corners (≈ 16 px), soft teal/blue glow consistent with design 1; verdict colours red / green / amber / grey; mobile-friendly (the sidebar collapses into a menu).
- **Languages:** the UI is in English and Kannada (Django i18n); the bot replies in the user's language.
- **Security:**
  - role checks on every view; CSRF protection
  - rate limits on sign-up, login, chat submissions and votes
  - sanitised input; http/https-only URLs that block `javascript:` and `data:`
  - an SSRF guard on page fetching
- **Secrets:** `GROQ_API_KEY`, search API key and `GOOGLE_FACTCHECK_API_KEY` in `.env`.
- **Privacy:** phone numbers are never public; no scraping of personal data.
- **Performance:** first AI answer within ~30 s with progress shown; research runs in the background with HTMX polling.
- **Reliability:** if the AI or a source fails, show a partial result, still route the claim to verifiers, and never block the chat.

## 13. Acceptance criteria
1. A logged-out visitor sees only the login / sign-up page (public claim share links excepted).
2. Sign-up asks for the role, then name, phone and field(s) of interest, plus extra details for verifiers.
3. A regular user lands on the chatbot after login, and **no news feed** is visible there.
4. The top bar shows **Check a Claim** and **News & Verdicts**; verifiers also see **Verify Queue**.
5. Submitting a claim shows progress, then an AI research card with a truth score and **PENDING** status.
6. Regular users can cast one community vote per claim. Their influence is capped (5% each, 20% combined).
7. Verifiers see the full AI research and vote with evidence. A claim becomes final only with ≥ 3 verifier votes and ≥ 70% weighted agreement.
8. Finalisation updates the chat thread, the feed and the claim page, and writes a VerdictLog entry.
9. News & Verdicts offers Most important, Latest and Past verdicts, styled like screenshot 2.

## 14. Milestones
1. Auth: role selection, sign-up (both roles), phone + password login, redirect rules
2. App shell: top bar tabs, sidebar, chat UI matching design 1
3. Chat threads + claim creation + duplicate check
4. Groq research pipeline + sources + truth score + research card
5. Community votes + verifier assignment + Verify Queue + weighted final verdict
6. News & Verdicts tabs + claim page + share card
7. Kannada UI, seed data, tests (weights and caps, one-vote constraints, role access, URL safety)

## 15. Demo script
1. Sign up as a regular user (name, phone, field: Education). You land on the chat.
2. Paste "ನಾಳೆ ಚಿಕ್ಕಮಗಳೂರು ಶಾಲೆಗಳಿಗೆ ರಜೆ" ("schools in Chikkamagaluru closed tomorrow"). You see the research steps, then the card "Likely False · 18/100 · PENDING".
3. Cast a community vote (False).
4. Log in as a verifier. Open the Verify Queue and the assigned claim, review the AI sources, and vote with evidence.
5. After the third verifier vote, the status becomes VERIFIED · FALSE, and the user's chat gets a ✅ system message.
6. Open News & Verdicts → Past verdicts to see the result, then share it on WhatsApp.
