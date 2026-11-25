# Verdicto — crowd-verified fact-check board (MVP)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then set SECRET_KEY
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo      # --reset to reseed; prints logins
.venv/bin/python manage.py test
.venv/bin/python manage.py runserver
```

Demo flow: log in as `citizen_anil` → *Check a claim* (type "VTU exams postponed" to see the duplicate check) →
log out, log in as `prof_ravi`, `kiran_new`, `tech_arjun` and vote → verdict breakdown + change log →
*Public result page* / *Share on WhatsApp*. The "hot lemon water cures dengue" claim is the SPEC worked
example (FALSE = 0.83 + 0.67 = 1.50; TRUE = 0.50; 75%, FINAL).

Key files: `verification/engine.py` (pure verdict maths), `verification/services.py` (votes, logging,
track-record scoring), `claims/validators.py` (URL blocking + nh3), `accounts/decorators.py` (roles),
`factcheck/errors.py` + `middleware.py` (structured JSON errors).

MVP simplifications: verifier profile fields live on `User`; work-email verification is a flag set by the
seed/admin (no OTP); no AI, LinkedIn, OG image or i18n files yet. Admin override locks the claim against
further automatic recomputation.
# Verdicto
