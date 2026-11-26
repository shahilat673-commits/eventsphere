"""Booking-help assistant for attendees.

Offline engine (rule_reply) + AI engine (reply, Gemini): the AI engine answers any question; the offline engine understands the attendee's question with keyword intents
(English + a few Malayalam / Hindi words), looks at the attendee's own bookings and at the live
event data, and answers with short steps plus quick links.

reply(user, message, event=None) -> {'answer': str, 'links': [{'label', 'url'}], 'chips': [str]}
"""
import json
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from core.models import SEAT_HOLD_MINUTES, Booking, Event, Ticket
from core.services import ai

log = logging.getLogger(__name__)

CHIPS = ['How do I book a ticket?', 'How does seat selection work?', 'Show my bookings',
         'Find events', 'Payment help', 'Feedback & rating']

# intent -> keywords (matched as substrings of the lower-cased message)
INTENTS = {
    'greet': ['hello', 'hi ', 'hey ', 'good morning', 'good evening', 'namaste', 'നമസ്കാരം', 'हैलो', 'नमस्ते'],
    'thanks': ['thank', 'thanks', 'nandri', 'നന്ദി', 'धन्यवाद'],
    'how_book': ['how to book', 'how do i book', 'how can i book', 'book a ticket', 'book ticket', 'booking process',
                 'steps to book', 'register for', 'buy ticket', 'buy a ticket', 'ബുക്ക്', 'बुक'],
    'seats': ['seat', 'seating', 'choose seat', 'select seat', 'pick seat', 'row', 'സീറ്റ്', 'सीट'],
    'payment': ['pay', 'payment', 'razorpay', 'card', 'upi', 'checkout', 'transaction', 'money deducted',
                'പേയ്മെന്റ്', 'പണം', 'भुगतान', 'पेमेंट'],
    'pending': ['pending', 'unpaid', 'not paid', 'incomplete', 'expired', 'hold'],
    'my_bookings': ['my booking', 'my ticket', 'my seat', 'booking status', 'booked', 'show my', 'where is my ticket',
                    'my qr', 'qr code', 'e-ticket', 'eticket', 'download ticket', 'ടിക്കറ്റ് എവിടെ'],
    'coupon': ['coupon', 'discount', 'promo', 'offer code', 'voucher'],
    'cancel': ['cancel', 'refund', 'money back', 'change booking', 'modify', 'transfer ticket', 'റദ്ദ്', 'रद्द'],
    'price': ['price', 'cost', 'how much', 'fee', 'charges', 'ticket type', 'vip', 'early bird', 'regular', 'free',
              'വില', 'कीमत'],
    'find': ['find event', 'show event', 'suggest', 'recommend', 'upcoming', 'what events', 'any event', 'events in',
             'event near', 'search'],
    'when_where': ['when', 'what time', 'date', 'timing', 'where', 'venue', 'location', 'parking', 'address',
                   'എപ്പോൾ', 'എവിടെ'],
    'feedback': ['feedback', 'rate', 'rating', 'review', 'experience', 'stars'],
    'certificate': ['certificate'],
    'sos': ['sos', 'emergency', 'help me now', 'danger', 'medical'],
    'limit': ['how many ticket', 'maximum', 'limit', 'quantity', 'group booking'],
    'contact': ['contact organiser', 'contact organizer', 'organiser', 'organizer', 'speak to', 'human', 'support'],
}


def _link(label, name, *args):
    return {'label': label, 'url': reverse(name, args=list(args))}


def _has(m, w):
    """Word-start match for English keywords ('seat' matches 'seats' but 'rate' not 'separate');
    a keyword ending with a space must match a whole word. Non-English keywords match as substrings."""
    if not w.isascii():
        return w in m
    pat = r'\b' + re.escape(w.strip()) + (r'\b' if w.endswith(' ') else r'\w*')
    return re.search(pat, m) is not None


def _intents(msg):
    m = msg.lower()
    hits = []
    for name, words in INTENTS.items():
        score = sum(1 for w in words if _has(m, w))
        if score:
            hits.append((score, name))
    hits.sort(reverse=True)
    return [n for _, n in hits]


def _named_event(msg, events):
    """The upcoming event whose title / city / category words appear in the message (best match)."""
    words = set(re.findall(r'\w{3,}', msg.lower())) - {'the', 'and', 'for', 'event', 'events', 'ticket', 'tickets',
                                                       'book', 'seat', 'seats', 'how', 'what', 'when', 'where', 'price'}
    best, best_score = None, 0
    for e in events:
        blob = f'{e.title} {e.city} {e.category}'.lower()
        sc = sum(1 for w in words if w in blob)
        if sc > best_score:
            best, best_score = e, sc
    return best


def _event_line(e):
    price = e.min_price
    p = 'tickets soon' if price is None else ('Free' if price == 0 else f'from ₹{price:.0f}')
    seating = ' · 💺 choose your seat' if e.seating_enabled else ''
    return f'• {e.title} — {e.start:%d %b}, {e.city} ({p}, {e.seats_left} seats left){seating}'


def rule_reply(user, message, event=None):
    msg = (message or '').strip()
    now = timezone.now()
    upcoming = list(Event.objects.filter(end__gte=now).select_related('category').prefetch_related('ticket_types'))
    named = _named_event(msg, upcoming) or event
    intents = _intents(msg)
    first = intents[0] if intents else ''
    links = []

    def out(answer, links=None, chips=None):
        return {'answer': answer, 'links': links or [], 'chips': chips or CHIPS[:4]}

    # ---------------- greetings / thanks ----------------
    if first == 'greet' and len(msg) < 25:
        return out(f'Hi {user.first_name or user.username}! 👋 I\'m your booking assistant. I can help you find events, '
                   'pick seats, pay, and find your tickets. What do you need?')
    if first == 'thanks':
        return out('You\'re welcome! 😊 Anything else about booking?', chips=['Show my bookings', 'Find events'])

    # ---------------- my bookings / tickets ----------------
    if first == 'my_bookings' or re.search(r'\bmy\b.*\bbookings?\b', msg.lower()):
        paid = (Booking.objects.filter(attendee=user, status='PAID').select_related('event', 'ticket_type')
                .prefetch_related('tickets').order_by('-created_at')[:4])
        pend = Booking.objects.filter(attendee=user, status='PENDING').select_related('event')
        lines = []
        for b in paid:
            seats = ', '.join(t.seat_label for t in b.tickets.all() if t.seat_label)
            lines.append(f'• {b.event.title} — {b.quantity} × {b.ticket_type.name}' + (f' · seats {seats}' if seats else '')
                         + f' ({b.event.start:%d %b})')
        if pend:
            lines.append(f'⚠️ You have {pend.count()} unpaid booking(s). Finish payment from My Tickets.')
        links = [_link('🎟️ Open My Tickets', 'my_tickets')]
        if lines:
            return out('Here are your latest bookings:\n' + '\n'.join(lines) +
                       '\n\nOpen My Tickets to see the QR code of each ticket, add it to your calendar or download a certificate.',
                       links=links)
        return out('You have no paid bookings yet. Pick an event from Discover and I\'ll guide you through booking.',
                   links=[_link('🔍 Discover events', 'home')], chips=['How do I book a ticket?', 'Find events'])

    # ---------------- seat selection ----------------
    if first == 'seats':
        if named and named.seating_enabled:
            left = named.seats_left
            return out(f'"{named.title}" has **reserved seating** — {left} seats left.\n'
                       '1) Open the event and pick a ticket type + quantity\n'
                       '2) Press "Choose seats" and tap the seats you like on the seat map\n'
                       '3) Confirm and pay. Your seats are held for '
                       f'{SEAT_HOLD_MINUTES} minutes while you pay.',
                       links=[_link(f'💺 Choose seats – {named.title}', 'event_detail', named.pk)])
        if named:
            return out(f'"{named.title}" uses **common (open) entry** — no seat is assigned, you just book the number of '
                       'tickets you need and sit anywhere.', links=[_link('Open event', 'event_detail', named.pk)])
        seated = [e for e in upcoming if e.seating_enabled]
        txt = ('Some shows have a fixed seat plan and some are open entry.\n'
               '• Reserved-seating shows: after choosing the ticket type and quantity, tap "Choose seats" and pick your seats on the map.\n'
               '• Common-entry shows: no seat is assigned — just choose the quantity.')
        if seated:
            txt += '\n\nShows with seat selection right now:\n' + '\n'.join(_event_line(e) for e in seated[:5])
            links = [_link(e.title, 'event_detail', e.pk) for e in seated[:3]]
        return out(txt, links=links)

    # ---------------- how to book ----------------
    if first == 'how_book':
        txt = ('Booking takes under a minute:\n'
               '1) Open an event from Discover\n'
               '2) Choose a ticket type and quantity (1–5 per booking) and add a coupon if you have one\n'
               '3) For reserved-seating shows, press "Choose seats" and tap your seats\n'
               '4) Pay on the checkout page (free tickets are confirmed instantly)\n'
               '5) Your QR e-ticket appears in My Tickets — show it at the gate.')
        if named:
            links = [_link(f'Book {named.title}', 'event_detail', named.pk)]
        else:
            links = [_link('🔍 Discover events', 'home')]
        return out(txt, links=links, chips=['How does seat selection work?', 'Payment help', 'Coupon codes'])

    # ---------------- payment / pending ----------------
    if first in ('payment', 'pending'):
        pend = list(Booking.objects.filter(attendee=user, status='PENDING').select_related('event')[:3])
        txt = ('Payments go through the Razorpay checkout page. This project runs in demo mode, so no real money is charged '
               '— just press Pay.\n'
               f'If you leave before paying, your booking stays "pending" and any chosen seats are held for {SEAT_HOLD_MINUTES} minutes; '
               'afterwards the seats are released.\n'
               'If money was deducted but you got no ticket, check My Tickets first, then contact the organiser.')
        if pend:
            txt += '\n\nYou have unpaid booking(s) — tap to finish:'
            links = [{'label': f'Pay for {b.event.title}', 'url': reverse('checkout', args=[b.pk])} for b in pend]
        return out(txt, links=links, chips=['Show my bookings', 'How do I book a ticket?'])

    # ---------------- coupon ----------------
    if first == 'coupon':
        return out('Type your coupon code in the "Coupon" box on the event page before pressing Book. The discount is shown '
                   'on the checkout page. Codes belong to one event, work for a limited number of uses and are not case-sensitive.',
                   chips=['How do I book a ticket?', 'Find events'])

    # ---------------- cancel / refund ----------------
    if first == 'cancel':
        return out('Self-service cancellation and refunds are not available in this version. If you must cancel, contact the '
                   'event organiser (their contact is on the event page) — refunds are handled by them.',
                   links=[_link('🎟️ My Tickets', 'my_tickets')], chips=['Contact organiser', 'Show my bookings'])

    # ---------------- limits ----------------
    if first == 'limit':
        return out('You can book 1 to 5 tickets in one booking. For more people, make another booking. '
                   'For seated shows, you pick exactly as many seats as tickets.', chips=['How does seat selection work?'])

    # ---------------- price / ticket types ----------------
    if first == 'price':
        if named:
            tts = ', '.join(f'{t.name} {"Free" if t.price == 0 else "₹%.0f" % t.price} ({t.left} left)'
                            for t in named.ticket_types.all()) or 'not announced yet'
            return out(f'{named.title}: {tts}.', links=[_link('Open event', 'event_detail', named.pk)])
        txt = 'Ticket types are Free, Regular, VIP and Early Bird — each event sets its own prices.\n' + \
              '\n'.join(_event_line(e) for e in upcoming[:5])
        return out(txt, links=[_link('🔍 Discover events', 'home')])

    # ---------------- find events ----------------
    if first == 'find':
        pool = [e for e in upcoming if named and e.pk == named.pk] or upcoming
        words = set(re.findall(r'\w{3,}', msg.lower()))
        matched = [e for e in upcoming if any(w in f'{e.city} {e.category} {e.title}'.lower() for w in words
                                              if w not in ('find', 'event', 'events', 'show', 'upcoming', 'any', 'what', 'the', 'suggest'))]
        pool = matched or pool
        if not pool:
            return out('No upcoming events right now. Please check again soon!')
        return out('Here are some events you can book:\n' + '\n'.join(_event_line(e) for e in pool[:5]),
                   links=[_link(e.title, 'event_detail', e.pk) for e in pool[:3]])

    # ---------------- when / where ----------------
    if first == 'when_where' and named:
        return out(f'{named.title}: {named.start:%A, %d %b %Y, %I:%M %p} – {named.end:%I:%M %p} at {named.venue}, {named.city}.'
                   + (f' Parking: {named.parking_info}' if named.parking_info else ''),
                   links=[_link('Open event', 'event_detail', named.pk)])

    # ---------------- feedback / certificate / sos / contact ----------------
    if first == 'feedback':
        return out('After you book, open the Feedback page, tap 1–5 stars for each event and add a comment. You can edit it later.',
                   links=[_link('⭐ Give feedback', 'my_feedback')])
    if first == 'certificate':
        return out('Certificates are available in My Tickets after you are checked in at the event gate.',
                   links=[_link('🎟️ My Tickets', 'my_tickets')])
    if first == 'sos':
        return out('If this is an emergency, use the red SOS button (bottom-right) — organisers and admin are alerted instantly. '
                   'You need a valid ticket for a running event.', links=[_link('🚨 Open SOS', 'sos_page')])
    if first == 'contact':
        if named:
            o = named.organiser
            return out(f'{named.title} is organised by {o.organisation or o.display_name}.'
                       + (f' Emergency / helpline: {named.emergency_contact}.' if named.emergency_contact else '')
                       + ' Ask more on the event page chatbot or via the organiser.',
                       links=[_link('Open event', 'event_detail', named.pk)])
        return out('Open the event page to see the organiser and contact details, or ask me about a specific event.')

    # ---------------- named event but no clear intent ----------------
    if named:
        return out(f'{named.title} — {named.start:%d %b %Y, %I:%M %p} at {named.venue}, {named.city}. '
                   f'{named.seats_left} seats left' + (' · reserved seating' if named.seating_enabled else ' · open entry') +
                   '. What would you like to know — tickets, seats or timing?',
                   links=[_link('Open event', 'event_detail', named.pk)], chips=['Ticket prices?', 'How do I book a ticket?'])

    return out('I can help with booking: finding events, ticket types, choosing seats, payment, coupons, your tickets and '
               'feedback. Try one of the quick questions below 👇')


# =====================================================================================================
#  AI (Gemini) assistant  - answers ANY question using live platform data; falls back to rule_reply()
# =====================================================================================================
HISTORY_TURNS = 6          # remembered question/answer pairs per user
PLATFORM_GUIDE = f"""
HOW EVENTSPHERE AI WORKS (facts you must follow):
- Attendees find events on the Discover (home) page, open an event, choose ticket type (Free / Regular / VIP / Early Bird)
  and quantity (1-5 per booking), optionally enter a coupon code (belongs to one event, limited uses, not case-sensitive).
- Reserved-seating events: after choosing type + quantity press "Choose seats" and tap seats on the seat map; seats are held
  for {SEAT_HOLD_MINUTES} minutes while paying. Open-entry events have no assigned seats.
- Payment: Razorpay checkout page. This project runs in DEMO mode - no real money is charged, just press Pay. Free tickets are
  confirmed instantly. Unpaid bookings stay "pending" and held seats are released after {SEAT_HOLD_MINUTES} minutes.
- After payment the QR e-ticket appears in "My Tickets" (show it at the gate, add to calendar). Certificates are available in
  My Tickets after check-in at the event gate. Feedback (1-5 stars + comment) is on the Feedback page.
- Self-service cancellation / refund is NOT available; the attendee must contact the event organiser (contact is on the event page).
- Emergency: the red SOS button (bottom-right) alerts organisers and admin; a valid ticket for a running event is needed.
- Roles: attendees book tickets; organisers create events, scan QR at the gate and see analytics; admin manages the platform.
"""

ASSISTANT_SYSTEM = (
    'You are the EventSphere AI booking assistant chatting with an attendee inside a web app. '
    'Answer EVERY question helpfully: use the PLATFORM GUIDE and LIVE DATA below as the source of truth for anything about '
    'this platform, its events, prices, dates, seats or the user\'s own bookings (never invent events, prices, dates or policies). '
    'If the user asks something general (not about the platform) answer it briefly and correctly from your own knowledge. '
    'If platform data needed is missing, say so and suggest contacting the organiser. '
    'Reply in the SAME LANGUAGE the user wrote (English, Malayalam, Hindi, Manglish etc). Be friendly, short (max ~120 words), '
    'use short lines / numbered steps when helpful, amounts in INR (use the rupee sign). '
    'Return ONLY a JSON object: {"answer": "...", "links": [...], "chips": [...]} where '
    '"links" (0-3 items) uses ONLY these codes: "home", "my_tickets", "feedback", "sos", "event:<id>" (an event id from LIVE DATA), '
    '"pay:<booking_id>" (a PENDING booking id from LIVE DATA); and "chips" is 2-3 short follow-up questions the user may ask next.'
)


def _live_context(user, upcoming, named):
    lines = [f'USER: {user.first_name or user.username}']
    paid = (Booking.objects.filter(attendee=user, status='PAID').select_related('event', 'ticket_type')
            .prefetch_related('tickets').order_by('-created_at')[:6])
    pend = list(Booking.objects.filter(attendee=user, status='PENDING').select_related('event', 'ticket_type')[:5])
    lines.append('USER PAID BOOKINGS:')
    for b in paid:
        seats = ', '.join(t.seat_label for t in b.tickets.all() if t.seat_label)
        lines.append(f'- {b.event.title} | {b.quantity} x {b.ticket_type.name}' + (f' | seats {seats}' if seats else '')
                     + f' | {b.event.start:%d %b %Y}')
    if not paid:
        lines.append('- none')
    lines.append('USER PENDING (UNPAID) BOOKINGS:')
    for b in pend:
        lines.append(f'- booking_id={b.pk} | {b.event.title} | {b.quantity} x {b.ticket_type.name}')
    if not pend:
        lines.append('- none')
    lines.append('UPCOMING EVENTS (id | details):')
    for e in upcoming[:15]:
        lines.append(f'- id={e.pk} | ' + ai.event_context(e) + (' | Reserved seating' if e.seating_enabled else ' | Open entry'))
    if not upcoming:
        lines.append('- none right now')
    if named:
        lines.append(f'THE USER IS CURRENTLY VIEWING EVENT id={named.pk} ("{named.title}") - questions without an event name refer to it.')
    return '\n'.join(lines)


def _links_from_codes(codes, user, upcoming, pending_ids):
    ev = {e.pk: e for e in upcoming}
    out = []
    for c in (codes or [])[:3]:
        c = str(c).strip()
        if c == 'home':
            out.append(_link('\U0001F50D Discover events', 'home'))
        elif c == 'my_tickets':
            out.append(_link('\U0001F39F\uFE0F My Tickets', 'my_tickets'))
        elif c == 'feedback':
            out.append(_link('\u2B50 Give feedback', 'my_feedback'))
        elif c == 'sos':
            out.append(_link('\U0001F6A8 Open SOS', 'sos_page'))
        elif c.startswith('event:') and c[6:].isdigit() and int(c[6:]) in ev:
            e = ev[int(c[6:])]
            out.append(_link(f'Open {e.title}', 'event_detail', e.pk))
        elif c.startswith('pay:') and c[4:].isdigit() and int(c[4:]) in pending_ids:
            out.append(_link('Finish payment', 'checkout', int(c[4:])))
    return out


def reply(user, message, event=None):
    """AI answer (Gemini) with live data; falls back to the offline rule-based assistant on any problem."""
    if not (settings.GEMINI_API_KEY or settings.ANTHROPIC_API_KEY or settings.OPENAI_API_KEY):
        return rule_reply(user, message, event)
    try:
        now = timezone.now()
        upcoming = list(Event.objects.filter(end__gte=now).select_related('category', 'organiser')
                        .prefetch_related('ticket_types').order_by('start'))
        named = _named_event(message, upcoming) or event
        pending_ids = set(Booking.objects.filter(attendee=user, status='PENDING').values_list('pk', flat=True))
        hist_key = f'bot_hist:{user.pk}'
        hist = cache.get(hist_key, [])

        system = ASSISTANT_SYSTEM + '\n' + PLATFORM_GUIDE + '\nLIVE DATA (' + f'{now:%d %b %Y %H:%M}' + '):\n' + \
            _live_context(user, upcoming, named)
        if settings.GEMINI_API_KEY:
            msgs = [{'role': r, 'text': t} for r, t in hist] + [{'role': 'user', 'text': message}]
            raw = ai.gemini_chat(system, msgs, max_tokens=2048, json_mode=True)
        else:                                   # Claude / OpenAI keys: single-turn
            raw = ai._call_llm(system, message, 900)
        raw = re.sub(r'^```(?:json)?|```$', '', (raw or '').strip(), flags=re.M).strip()
        try:
            data = json.loads(raw)
            answer = str(data.get('answer', '')).strip()
            links = _links_from_codes(data.get('links'), user, upcoming, pending_ids)
            chips = [str(c)[:60] for c in (data.get('chips') or [])][:3]
        except ValueError:
            answer, links, chips = raw, [], []
        if not answer:
            return rule_reply(user, message, event)
        hist = (hist + [('user', message), ('model', answer)])[-HISTORY_TURNS * 2:]
        cache.set(hist_key, hist, 60 * 60)
        return {'answer': answer, 'links': links, 'chips': chips or CHIPS[:3]}
    except Exception as exc:
        log.warning('AI assistant failed, using rule-based fallback: %s', exc)
        return rule_reply(user, message, event)