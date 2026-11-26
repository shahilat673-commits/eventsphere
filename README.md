# EventSphere AI – Smart Event Management & Ticketing Platform
Final-year project · Pure Django full-stack (Django Templates + HTML/CSS/JS) · "Plan it. Book it. Live it."

## Quick start (5 commands)
```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo       # creates demo users, categories, events
python manage.py runserver
```
Open http://127.0.0.1:8000

| Role | Username | Password | What they see |
|------|----------|----------|---------------|
| Admin | admin | Admin@12345 | Approvals, categories, payments, reports, payouts |
| Organiser | organiser1 | Organiser@123 | Only THEIR events, ticket types, AI tools, QR scanner |
| Attendee | attendee1 | Attendee@123 | Discover/book events, My Tickets, certificates |

(Django's built-in admin is also at /django-admin/ for the admin user.)

## Demo script for viva (2 browser windows)
1. Attendee: log in → open "AI & Django Workshop" → Book VIP → **Razorpay demo checkout** → Pay.
   Payment is verified with a real HMAC-SHA256 signature check (same logic as Razorpay), then QR e-ticket + email/SMS are generated.
2. Organiser (second window): QR Scanner → paste the ticket code (or scan the QR with webcam/phone) →
   shows **ENTRY ALLOWED – PAYMENT VERIFIED**. Scan again → **ALREADY USED** (fraud-free). Random code → **INVALID**.
3. Attendee: My Tickets → download **PDF certificate** (only after check-in) → give feedback.
4. Organiser: Manage event → **AI Event Planner / Content Writer / Feedback Analyzer**.
5. Attendee: chatbot on event page (type or 🎤 voice, English/Malayalam/Hindi).
6. Register a new Organiser → OTP (shown on screen in demo mode) → Admin approves → organiser can log in.

## Mapping to the project document
| Module | Where |
|---|---|
| 1. Admin & organiser management | `/panel/` – approvals, categories, dashboard (events, tickets, revenue, payouts), CSV report; RBAC in `core/decorators.py` |
| 2. Attendee registration & profile | OTP sign-up, interest profile, My Tickets (QR), reminders (`send_reminders` command + button), certificates |
| 3. Event creation, ticketing, check-in | Event CRUD, Free/Regular/VIP/Early Bird, Razorpay, QR scan, live count, attendee CSV export |
| 4. AI core | `core/services/ai.py` – Planner, Content Writer, Recommendations, RAG Chatbot, Feedback Analyzer |

## NEW in v2 – Advanced features
- **🚨 SOS Emergency Alert** – attendee (with a valid ticket) press-and-holds a big SOS button, picks emergency type (medical, fire, security, lost child, crowd, other), adds location text + live GPS.
  Organiser & admin (authority) get an instant live alert (toast + siren sound + red badge), can **Take charge → Resolve**, **Escalate to admin**, and the attendee sees live status (Sent → Help on the way → Resolved) plus SMS/email updates. Route: `/sos/` (attendee), `/control/sos/` (organiser/admin).
- Coupon codes, live announcements broadcast (email+SMS), AI Demand Predictor & Smart Pricing, sales/check-in charts (Chart.js), add-to-calendar (.ics), WhatsApp share, event emergency contact.
- UI: dark mode, particles hero, cursor glow, scroll progress bar, page loader, marquee, animated counters, 3D tilt cards, countdown timer.
- After upgrading from v1 run: `python manage.py migrate`

## NEW in v3 - white + orange theme, seat selection, booking chatbot, feedback, analytics
- **Theme**: clean white UI with Claude-style orange (`#D97757`). Colours live in the `:root` variables at the top of `static/css/style.css`.
- **Reserved seating (per event)**: organiser ticks *Reserved seating* (and seats per row) in Create / Edit event. Attendees then press **Choose seats** on the event page and pick seats on a seat map (booked/held seats are disabled; seats are held 10 min while paying). Events without the tick stay "common / open entry" - the attendee just picks the quantity. Seat numbers appear on tickets, the QR scanner, attendee list and CSV.
- **Booking assistant chatbot**: floating 💬 button on every attendee page (above the SOS button). Answers how to book, seats, payment/pending bookings, coupons, "show my bookings", find events, prices, feedback. 100 % offline - no paid API.
- **Feedback page** (`/my-feedback/`, "Feedback" in the attendee menu): after booking, attendees rate every event with 1-5 stars + comment and can edit it later. Average rating is shown on the event page.
- **Admin analytics** (`/panel/analytics/`, "Analytics" in the admin menu): searches and event-page views (browsing) as line, bar, pie and doughnut charts, with 7 / 14 / 30 / 90 day filter. Sales & revenue charts are unchanged on the main dashboard.
- After upgrading run: `python manage.py migrate` (adds migration `0003`). No new Python packages needed.

## Technology stack (as specified)
Django · Django Templates + HTML/CSS/JS · MySQL/PostgreSQL (SQLite for quick demo) · Claude/OpenAI API · Razorpay · Email + SMS (Twilio) · `qrcode` · `reportlab` · Django auth + role-based access.

## Going "real" (optional – copy `.env.example` to `.env`)
- **MySQL/PostgreSQL**: set `DB_ENGINE=mysql|postgres` + DB_* values (`pip install mysqlclient` or `psycopg2-binary`).
- **AI**: set `ANTHROPIC_API_KEY` (Claude) or `OPENAI_API_KEY`. Without a key an offline fallback AI is used so the demo never breaks.
- **Razorpay**: set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (test keys) → real Razorpay Checkout is used automatically.
- **Email**: set EMAIL_HOST etc. (otherwise emails print in terminal). **SMS**: set TWILIO_* (otherwise saved in Notification Log + terminal).
- Production: `DEBUG=False`, strong `SECRET_KEY`, set `ALLOWED_HOSTS`.

## Security features
Password hashing + validators · CSRF on all forms · login rate-limit (5 tries/5 min) · OTP verification (10-min expiry, attempt limit) ·
role-based access (403 for wrong role) · object-level ownership checks (organisers only touch own events, attendees only own tickets) ·
ticket price computed server-side · Razorpay signature verification · unguessable ticket codes · atomic check-in (no double entry) ·
open-redirect protection · secure cookies/HSTS when DEBUG=False.

## Run tests
`python manage.py test`

## Project structure
```
eventsphere/   settings, urls
core/          models, views, forms, urls, decorators, tests
core/services/ ai.py  payments.py  qr.py  certificate.py  notify.py
templates/     all HTML pages (admin_panel/ for admin)
static/        css
```
