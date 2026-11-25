"""python manage.py seed_demo [--reset] — synthetic demo data. All content is fictional demo data."""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from faker import Faker

from accounts.models import User
from claims.duplicates import normalize
from claims.models import Claim
from verification.models import VerifierStat
from research.ai import fallback_understanding
from verification.assignment import assign_verifiers
from verification.services import admin_override, cast_community_vote, cast_vote, log_submission

# username: (languages, domains, district, work_email_org or None, approved, {(domain, lang): (correct, total)})
VERIFIERS = {
    "dr_meera": (["en", "kn"], ["health"], "Chikkamagaluru", None, True,
                 {("health", "en"): (8, 8), ("health", "kn"): (6, 7)}),
    "nurse_asha": (["en"], ["health"], "Hassan", None, True, {("health", "en"): (2, 2)}),
    "kiran_new": (["en", "kn"], ["health", "education", "technology", "local-news"], "Mysuru", None, True, {}),
    "prof_ravi": (["en", "kn"], ["education"], "Mysuru", "college.example.edu.in", True,
                  {("education", "en"): (12, 14), ("education", "kn"): (5, 5)}),
    "journo_divya": (["kn", "en"], ["local-news", "government"], "Bengaluru", None, True,
                     {("local-news", "kn"): (9, 11), ("government", "kn"): (4, 5)}),
    "tech_arjun": (["en"], ["technology", "science"], "Bengaluru", "it-company.example.com", True, {("technology", "en"): (6, 9)}),
    "clerk_suma": (["kn"], ["government"], "Udupi", "taluk-office.example.in", True,
                   {("government", "kn"): (3, 3)}),
    "pending_vikram": (["kn"], ["local-news"], "Shivamogga", None, False, {}),
}
CITIZENS = ["citizen_anil", "citizen_bhavya", "citizen_chetan"]
# Fictional demo numbers, in order: admin, citizens, verifiers.
DEMO_PHONES = [f"+9190000000{i:02d}" for i in range(1, 30)]
# Fixed, fictional names so the demo is easy to follow (Faker fills any others).
DEMO_NAMES = {"admin": ("Site", "Admin"), "citizen_anil": ("Anil", "Kumar"),
              "citizen_bhavya": ("Bhavya", "Murthy"), "citizen_chetan": ("Chetan", "Rao"),
              "dr_meera": ("Dr Meera", "Hegde"), "nurse_asha": ("Asha", "Kulkarni"),
              "kiran_new": ("Kiran", "Gowda"), "prof_ravi": ("Prof Ravi", "Shankar"),
              "journo_divya": ("Divya", "Bhat"), "tech_arjun": ("Arjun", "Nair"),
              "clerk_suma": ("Suma", "Patil"), "pending_vikram": ("Vikram", "Shetty")}
PROFESSIONS = {"dr_meera": "Doctor", "nurse_asha": "Nurse", "kiran_new": "Postgraduate student",
               "prof_ravi": "Professor", "journo_divya": "Journalist", "tech_arjun": "Software engineer",
               "clerk_suma": "Government clerk", "pending_vikram": "Teacher"}
COMMUNITY = {"metro": ["FALSE", "FALSE", "TRUE"], "kn_ration": ["TRUE", "UNVERIFIED"],
             "paracetamol": ["FALSE", "FALSE", "FALSE"]}

OFFICIAL = "https://pib.gov.in/factcheck"
VTU = "https://vtu.ac.in/en/examination-notices/"
HEALTH = "https://mohfw.gov.in/"
KA = "https://karnataka.gov.in/"
FC = "https://factly.in/"

# key, text, lang, domain, district, submission_count, votes [(verifier, verdict, note, [urls])]
# Order matters: the first claim reproduces the SPEC worked example (0.83 + 0.67 vs 0.50).
CLAIMS = [
    ("lemon", "Forwarded many times: Drinking hot lemon water every morning cures dengue within 3 days. Share with everyone!",
     "en", "health", "Hassan", 6, [
         ("dr_meera", "FALSE", "No evidence. Dengue needs medical care; see health ministry advisory.", [HEALTH]),
         ("nurse_asha", "FALSE", "Lemon water helps hydration only.", [FC]),
         ("kiran_new", "TRUE", "My relative felt better after it.", ["https://example.org/blog"]),
     ]),
    ("kn_holiday", "ನಾಳೆ ಭಾರಿ ಮಳೆ ಕಾರಣ ಚಿಕ್ಕಮಗಳೂರು ಜಿಲ್ಲೆಯ ಎಲ್ಲಾ ಶಾಲೆಗಳಿಗೆ ರಜೆ ಘೋಷಣೆ – ಜಿಲ್ಲಾಧಿಕಾರಿ ಆದೇಶ",
     "kn", "education", "Chikkamagaluru", 4, [
         ("prof_ravi", "FALSE", "ಜಿಲ್ಲಾಡಳಿತದ ವೆಬ್‌ಸೈಟ್‌ನಲ್ಲಿ ಇಂತಹ ಆದೇಶ ಇಲ್ಲ.", [KA]),
         ("journo_divya", "FALSE", "Circular uses an old 2023 letterhead.", [FC]),
         ("kiran_new", "FALSE", "", [KA]),
     ]),
    ("kn_kidnap", "ಮಕ್ಕಳ ಅಪಹರಣ ಗ್ಯಾಂಗ್ ಹಾಸನದಲ್ಲಿ ಓಡಾಡುತ್ತಿದೆ, ಮಕ್ಕಳನ್ನು ಹೊರಗೆ ಕಳುಹಿಸಬೇಡಿ! ಎಲ್ಲಾ ಗ್ರೂಪ್‌ಗಳಿಗೂ ಕಳುಹಿಸಿ",
     "kn", "local-news", "Hassan", 9, [
         ("journo_divya", "FALSE", "Hassan police: no such case. Video is from another state.", [FC]),
         ("clerk_suma", "FALSE", "", [KA]),
         ("kiran_new", "UNVERIFIED", "Could not confirm either way.", []),
     ]),
    ("kn_ration", "ಪಡಿತರ ಚೀಟಿ ಇ-ಕೆವೈಸಿ ಮಾಡದಿದ್ದರೆ ಡಿಸೆಂಬರ್ 1ರಿಂದ ರೇಷನ್ ನಿಲ್ಲಿಸಲಾಗುವುದು",
     "kn", "government", "Udupi", 3, [
         ("clerk_suma", "TRUE", "e-KYC is required, but the date is not confirmed.", [KA]),
         ("journo_divya", "TRUE", "", [KA]),
     ]),
    ("kn_turmeric", "ಬಿಸಿ ನೀರಿಗೆ ಅರಿಶಿನ ಹಾಕಿ ಕುಡಿದರೆ ಡೆಂಗ್ಯೂ ಗುಣವಾಗುತ್ತದೆ, ಆಸ್ಪತ್ರೆಗೆ ಹೋಗುವ ಅಗತ್ಯವಿಲ್ಲ",
     "kn", "health", "Mandya", 2, [
         ("dr_meera", "FALSE", "ಡೆಂಗ್ಯೂಗೆ ವೈದ್ಯಕೀಯ ಚಿಕಿತ್ಸೆ ಅಗತ್ಯ.", [HEALTH]),
         ("kiran_new", "FALSE", "", [HEALTH]),
         ("nurse_asha", "FALSE", "", [FC]),
     ]),
    ("kn_job", "ಕೆಎಸ್‌ಆರ್‌ಟಿಸಿಯಲ್ಲಿ 5000 ಕಂಡಕ್ಟರ್ ಹುದ್ದೆಗಳು! ₹500 ಶುಲ್ಕ ಕಟ್ಟಿ ಈ ಲಿಂಕ್‌ನಲ್ಲಿ ಅರ್ಜಿ ಸಲ್ಲಿಸಿ",
     "kn", "government", "Bengaluru", 5, [
         ("clerk_suma", "FALSE", "Official recruitment never asks for fees via WhatsApp links.", [KA]),
         ("journo_divya", "FALSE", "", [FC]),
         ("prof_ravi", "FALSE", "", [OFFICIAL]),
     ]),
    ("vtu_postponed", "URGENT: VTU has cancelled all 5th semester exams this year and will promote every student automatically. Official circular attached.",
     "en", "education", "Belagavi", 7, [
         ("prof_ravi", "FALSE", "No such circular on the VTU notices page.", [VTU]),
         ("kiran_new", "FALSE", "", [VTU]),
         ("tech_arjun", "FALSE", "PDF metadata shows it was edited last night.", [FC]),
     ]),
    ("vtu_timetable", "VTU 7th semester exam timetable for December 2026 is out on the VTU website.",
     "en", "education", "Belagavi", 2, [
         ("prof_ravi", "TRUE", "Timetable is published on the notices page.", [VTU]),
         ("kiran_new", "TRUE", "", [VTU]),
         ("journo_divya", "TRUE", "", [VTU]),
     ]),
    ("laptops", "Government is giving free laptops to all PU students. Register on this site before Friday!",
     "en", "government", "Tumakuru", 4, [
         ("journo_divya", "FALSE", "Phishing site collecting Aadhaar numbers.", [OFFICIAL]),
         ("clerk_suma", "FALSE", "", [KA]),
         ("tech_arjun", "FALSE", "Domain registered 3 days ago.", [FC]),
     ]),
    ("kfd", "Chikkamagaluru district has issued an advisory on monkey fever (KFD) for villages near Sringeri.",
     "en", "health", "Chikkamagaluru", 1, [
         ("dr_meera", "TRUE", "Advisory issued by the district health office.", [HEALTH]),
         ("nurse_asha", "TRUE", "", [HEALTH]),
         ("kiran_new", "TRUE", "", [KA]),
     ]),
    ("5g", "5G towers in Mysuru are spreading a new virus. Switch off your phones at night!",
     "en", "technology", "Mysuru", 3, [
         ("tech_arjun", "FALSE", "Radio waves cannot carry viruses.", [OFFICIAL]),
         ("dr_meera", "FALSE", "", [HEALTH]),
         ("kiran_new", "FALSE", "", [FC]),
     ]),
    ("metro", "Namma Metro Purple Line will be fully closed for three days next week for repairs.",
     "en", "local-news", "Bengaluru", 2, [
         ("journo_divya", "TRUE", "Partial closure announced for one stretch.", ["https://example.org/news"]),
         ("tech_arjun", "FALSE", "Only one section is closed, not the full line.", ["https://example.org/news2"]),
     ]),
    ("paracetamol", "Warning: P-500 paracetamol tablets contain a deadly virus. Do not buy them!",
     "en", "health", "Shivamogga", 1, []),
    ("mangaluru", "Mangaluru schools closed tomorrow due to red alert, says DC.",
     "en", "education", "Dakshina Kannada", 3, [
         ("kiran_new", "FALSE", "Could not find it on the website yet.", [KA]),
         ("tech_arjun", "FALSE", "", [FC]),
     ]),
]
DUPLICATE = ("Schools closed tomorrow in Chikkamagaluru because of heavy rain - DC order!!", "kn_holiday")


class Command(BaseCommand):
    help = "Seed synthetic demo data (8 verifiers, 15 claims, votes) and print logins."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete demo data and reseed.")
        parser.add_argument("--research", action="store_true",
                            help="Also run live AI research on open claims (needs network; slow).")

    def handle(self, *args, **opts):
        usernames = ["admin", *CITIZENS, *VERIFIERS]
        if opts["reset"]:
            with transaction.atomic(), connection.cursor() as cur:
                # The only place the append-only log is bypassed: wiping the demo database.
                cur.execute("DELETE FROM verification_verdictlog")
                Claim.objects.all().delete()
                User.objects.filter(username__in=usernames).delete()
        if User.objects.filter(username="admin").exists():
            self.stdout.write(self.style.WARNING("Demo data already present (use --reset to reseed)."))
        else:
            self.seed()
            if opts["research"]:
                self.research()
        self.print_logins()

    def research(self):
        from research.pipeline import run_research
        from research.models import ResearchRun
        for claim in Claim.objects.filter(duplicate_of__isnull=True, status__in=["PENDING", "UNDER_REVIEW"]):
            self.stdout.write(f"Researching: {claim.text[:60]}…")
            run_research(ResearchRun.objects.create(claim=claim).pk)

    @transaction.atomic
    def seed(self):
        fake = Faker("en_IN")
        Faker.seed(26)
        pw = settings.DEMO_PASSWORD

        phones = iter(DEMO_PHONES)

        def make(username, password=None, **kw):
            first, last = DEMO_NAMES.get(username, (fake.first_name(), fake.last_name()))
            return User.objects.create_user(username, password=password or pw, first_name=first, last_name=last,
                                            phone=next(phones), **kw)

        admin = make("admin", password=settings.ADMIN_PASSWORD, role=User.Role.ADMIN, is_staff=True,
                     is_superuser=True)
        citizens = [make(name, domains=["health", "education", "local-news"]) for name in CITIZENS]
        verifiers = {}
        for name, (langs, domains, district, org, approved, stats) in VERIFIERS.items():
            verifiers[name] = make(name, role=User.Role.VERIFIER, languages=langs, domains=domains,
                                   district=district, verifier_approved=approved,
                                   work_email_verified=bool(org), org_domain=org or "",
                                   profession=PROFESSIONS.get(name, ""))
            for (domain, lang), (correct, total) in stats.items():
                VerifierStat.objects.create(verifier=verifiers[name], domain=domain, language=lang,
                                            correct=correct, total=total)

        created = {}
        for i, (key, text, lang, domain, district, count, votes) in enumerate(CLAIMS):
            claim = Claim.objects.create(text=text, normalized_text=normalize(text), language=lang,
                                         domain=domain, district=district, submission_count=count,
                                         urgency=fallback_understanding(text)["urgency"],
                                         submitted_by=citizens[i % len(citizens)])
            log_submission(claim, claim.submitted_by)
            for username, verdict, note, urls in votes:
                cast_vote(claim, verifiers[username], verdict, note, urls)
            created[key] = claim

        # Community opinions from regular users (low weight) on open claims.
        for key, opinions in COMMUNITY.items():
            for citizen, verdict in zip(citizens, opinions):
                cast_community_vote(created[key], citizen, verdict, "Seen this forwarded in my groups")

        admin_override(created["mangaluru"], admin, "TRUE",
                       "District administration published the red-alert holiday order (demo data)")
        for claim in Claim.objects.filter(status__in=["PENDING", "UNDER_REVIEW"]):
            assign_verifiers(claim)
        text, original = DUPLICATE
        Claim.objects.create(text=text, normalized_text=normalize(text), language="en",
                             domain="education", district="Chikkamagaluru",
                             submitted_by=citizens[0], duplicate_of=created[original])
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Claim.objects.count()} claims, {len(verifiers)} verifiers, {len(citizens)} citizens."))

    def print_logins(self):
        pw = settings.DEMO_PASSWORD
        self.stdout.write(f"\nDemo logins: log in with the PHONE number (password: {pw})")
        self.stdout.write(f"Admin: username 'admin' or its phone, password: {settings.ADMIN_PASSWORD}\n")
        for u in User.objects.filter(username__in=["admin", *CITIZENS, *VERIFIERS]).order_by("phone"):
            tag = u.get_role_display()
            if u.is_pending_verifier:
                tag += " (PENDING approval)"
            if u.work_email_verified:
                tag += f", work email verified · {u.org_domain}"
            self.stdout.write(f"  {u.phone}  {u.display_name:<20} {u.username:<16} {tag}")
