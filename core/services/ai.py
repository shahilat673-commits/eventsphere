"""AI engine: Large Language Model API (Gemini / Claude / OpenAI) with an offline fallback so the demo
always works, even without an API key.

Priority: Gemini -> Claude -> OpenAI -> offline fallback."""
import logging
import re
import requests
from django.conf import settings

log = logging.getLogger(__name__)
SYSTEM = ('You are EventSphere AI, an assistant for an event management platform used by colleges, '
          'companies and clubs in India. Be concise, practical and friendly. Use Indian Rupees (INR).')

GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'


# ---------------- Gemini ----------------
def _gemini_models():
    """Preferred model first, then fallbacks (Google retires / restricts models from time to time)."""
    models = [settings.GEMINI_MODEL] + [m.strip() for m in settings.GEMINI_FALLBACK_MODELS.split(',') if m.strip()]
    seen, out = set(), []
    for m in models:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def gemini_chat(system, messages, max_tokens=2048, json_mode=False, temperature=0.4):
    """messages = [{'role': 'user'|'model', 'text': str}, ...]  (last one must be the user's question).
    Returns the reply text. Tries each configured Gemini model until one answers."""
    body = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': [{'role': m['role'], 'parts': [{'text': m['text']}]} for m in messages],
        'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': temperature},
    }
    if json_mode:
        body['generationConfig']['responseMimeType'] = 'application/json'
    last_exc = None
    for model in _gemini_models():
        try:
            r = requests.post(GEMINI_URL.format(model=model), json=body, timeout=40,
                              headers={'x-goog-api-key': settings.GEMINI_API_KEY, 'Content-Type': 'application/json'})
            if r.status_code in (404, 429, 500, 503):      # model missing / quota / busy -> try next model
                log.warning('Gemini model %s returned HTTP %s: %s', model, r.status_code, r.text[:200])
                last_exc = RuntimeError(f'{model}: HTTP {r.status_code}')
                continue
            r.raise_for_status()
            data = r.json()
            parts = (data.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
            text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
            if text.strip():
                return text.strip()
            last_exc = RuntimeError(f'{model}: empty reply')
        except requests.RequestException as exc:
            log.warning('Gemini call failed (%s): %s', model, exc)
            last_exc = exc
    raise last_exc or RuntimeError('Gemini not available')


def _call_llm(system, prompt, max_tokens=1200):
    if settings.GEMINI_API_KEY:
        return gemini_chat(system, [{'role': 'user', 'text': prompt}], max_tokens=max(max_tokens, 1500))
    if settings.ANTHROPIC_API_KEY:
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=45, headers={
            'x-api-key': settings.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01',
            'content-type': 'application/json'},
            json={'model': settings.ANTHROPIC_MODEL, 'max_tokens': max_tokens, 'system': system,
                  'messages': [{'role': 'user', 'content': prompt}]})
        r.raise_for_status()
        return r.json()['content'][0]['text']
    if settings.OPENAI_API_KEY:
        r = requests.post('https://api.openai.com/v1/chat/completions', timeout=45,
                          headers={'Authorization': f'Bearer {settings.OPENAI_API_KEY}'},
                          json={'model': settings.OPENAI_MODEL, 'max_tokens': max_tokens,
                                'messages': [{'role': 'system', 'content': system},
                                             {'role': 'user', 'content': prompt}]})
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content']
    return None


def generate(system, prompt, fallback, max_tokens=1200):
    try:
        out = _call_llm(system, prompt, max_tokens)
        if out:
            return out.strip()
    except Exception as exc:  # network / key problems -> fall back gracefully
        log.warning('LLM call failed, using offline fallback: %s', exc)
    return fallback()


def engine_name():
    if settings.GEMINI_API_KEY:
        return 'Gemini API'
    if settings.ANTHROPIC_API_KEY:
        return 'Claude API'
    if settings.OPENAI_API_KEY:
        return 'OpenAI API'
    return 'Offline demo AI'


# ---------------- 1. AI Event Planner ----------------
def plan_event(event):
    prompt = (f'Event: {event.title}\nCategory: {event.category}\nDescription: {event.description}\n'
              f'Venue: {event.venue}, {event.city}\nStart: {event.start:%d %b %Y %I:%M %p}  End: {event.end:%I:%M %p}\n'
              f'Expected attendees: {event.seat_limit}\nSpeakers: {event.speakers or "TBD"}\n\n'
              'Create a complete plan. Reply in EXACTLY this format with these three headings:\n'
              'SCHEDULE:\n(one line per slot like "09:00 - 10:00 | Registration")\n'
              'CHECKLIST:\n(one task per line starting with "- ")\n'
              'BUDGET:\n(one line per item like "Venue | 20000" in INR, then a final line "TOTAL | amount")')
    raw = generate(SYSTEM, prompt, lambda: _fallback_plan(event), 1500)
    return _parse_plan(raw, event)


def _parse_plan(raw, event):
    parts = {'SCHEDULE': '', 'CHECKLIST': '', 'BUDGET': ''}
    cur = None
    for line in raw.splitlines():
        m = re.match(r'^\W*(SCHEDULE|CHECKLIST|BUDGET)\W*:?\s*$', line.strip(), re.I)
        if m:
            cur = m.group(1).upper()
            continue
        if cur:
            parts[cur] += line + '\n'
    if not any(parts.values()):
        return _parse_plan(_fallback_plan(event), event)
    return {'schedule': parts['SCHEDULE'].strip(), 'checklist': parts['CHECKLIST'].strip(),
            'budget': parts['BUDGET'].strip()}


def _fallback_plan(event):
    from datetime import timedelta
    t = event.start
    slots = [('Registration & QR check-in', 45), ('Inauguration & welcome', 30),
             (f'Keynote: {event.speakers.split(",")[0] if event.speakers else "Guest speaker"}', 60),
             ('Tea / networking break', 20), (f'{event.category} main session / activities', 90),
             ('Lunch break', 60), ('Hands-on / competition round', 90), ('Prize distribution & feedback', 30),
             ('Vote of thanks & closing', 15)]
    sched = []
    for name, mins in slots:
        sched.append(f'{t:%I:%M %p} - {(t + timedelta(minutes=mins)):%I:%M %p} | {name}')
        t += timedelta(minutes=mins)
    n = max(event.seat_limit, 20)
    items = [('Venue & hall rent', 150 * n), ('Sound, projector & lighting', 80 * n), ('Food & refreshments', 120 * n),
             ('Marketing & posters', 30 * n), ('Speaker honorarium & gifts', 50 * n), ('Certificates & printing', 15 * n),
             ('Volunteers & security', 25 * n)]
    total = sum(v for _, v in items)
    items.append(('Contingency (10%)', int(total * 0.1)))
    total = sum(v for _, v in items)
    checklist = ['Confirm venue booking and permissions', 'Finalise speakers and send invitations',
                 'Open online ticket sales and publish event page', 'Design banner and social media posts',
                 'Arrange sound system, projector, mics and Wi-Fi', 'Plan food, water and parking arrangements',
                 'Brief volunteers on QR check-in process', 'Test QR scanner on the day before',
                 'Prepare certificates and feedback form', 'Send reminders 24 hours before the event']
    return ('SCHEDULE:\n' + '\n'.join(sched) + '\nCHECKLIST:\n' + '\n'.join('- ' + c for c in checklist) +
            '\nBUDGET:\n' + '\n'.join(f'{k} | {v}' for k, v in items) + f'\nTOTAL | {total}')


# ---------------- 2. AI Content Writer ----------------
def write_content(event):
    prompt = (f'Write marketing content for this event.\nTitle: {event.title}\nCategory: {event.category}\n'
              f'About: {event.description}\nWhen: {event.start:%A, %d %B %Y, %I:%M %p}\nWhere: {event.venue}, {event.city}\n'
              f'Speakers: {event.speakers or "TBA"}\n\nProduce three sections titled: '
              '"EVENT DESCRIPTION", "SOCIAL MEDIA POST" (with emojis and hashtags), "EMAIL INVITATION" (subject + body).')
    return generate(SYSTEM, prompt, lambda: _fallback_content(event), 900)


def _fallback_content(e):
    tag = ''.join(w.capitalize() for w in re.findall(r'\w+', e.title))
    return (f'EVENT DESCRIPTION\n{e.title} is an exciting {e.category.name.lower()} event happening on '
            f'{e.start:%d %B %Y} at {e.venue}, {e.city}. {e.description[:200]} Join us to learn, connect and have fun!\n\n'
            f'SOCIAL MEDIA POST\n🎉 {e.title} is coming! 📅 {e.start:%d %b %Y} ⏰ {e.start:%I:%M %p} 📍 {e.venue}, {e.city}\n'
            f'Limited seats - book your QR e-ticket now on EventSphere AI! 🎟️\n#{tag} #EventSphereAI #{e.category.name.replace(" ", "")}\n\n'
            f'EMAIL INVITATION\nSubject: You are invited to {e.title}!\n\nHello,\n\nWe are delighted to invite you to {e.title} on '
            f'{e.start:%A, %d %B %Y} at {e.venue}, {e.city}. {"Featuring " + e.speakers + ". " if e.speakers else ""}'
            f'Book your ticket online and show the QR code at the entry gate.\n\nSee you there!\nTeam {e.organiser.organisation or e.organiser.display_name}')


# ---------------- 3. Smart Recommendations ----------------
def recommend(user, events, limit=6):
    """Score events using interests, past bookings, location and price preference."""
    from core.models import Booking
    past_cats = set(Booking.objects.filter(attendee=user, status='PAID').values_list('event__category_id', flat=True))
    interests = set(user.interests.values_list('id', flat=True))
    booked = set(Booking.objects.filter(attendee=user, status='PAID').values_list('event_id', flat=True))
    scored = []
    for e in events:
        if e.id in booked:
            continue
        score, why = 0, []
        if e.category_id in interests:
            score += 3; why.append(f'matches your interest in {e.category}')
        if e.category_id in past_cats:
            score += 2; why.append('similar to events you booked')
        if user.location and user.location.lower() in e.city.lower():
            score += 1; why.append(f'happening in {e.city}')
        if user.preferred_type == 'FREE' and e.min_price == 0:
            score += 1; why.append('free to attend')
        scored.append((score, e, ' · '.join(why) or 'popular upcoming event'))
    scored.sort(key=lambda x: (-x[0], x[1].start))
    return [{'event': e, 'why': w} for _, e, w in scored[:limit]]


# ---------------- 4. Event Chatbot (RAG) ----------------
def event_context(e):
    types = '; '.join(f'{t.name} Rs.{t.price} ({t.left} left)' for t in e.ticket_types.all()) or 'not announced'
    return (f'EVENT: {e.title} | Category: {e.category} | Date: {e.start:%d %b %Y}, {e.start:%I:%M %p} to {e.end:%I:%M %p} | '
            f'Venue: {e.venue}, {e.city} | Speakers: {e.speakers or "TBA"} | Tickets: {types} | Seats left: {e.seats_left} | '
            f'Parking: {e.parking_info or "not specified"} | Organiser: {e.organiser.organisation or e.organiser.display_name} | '
            f'About: {e.description[:300]} | FAQ: {e.faq} | Schedule: {e.schedule[:500]}')


def retrieve(question, events, k=3):
    words = set(re.findall(r'\w{3,}', question.lower()))
    scored = []
    for e in events:
        blob = f'{e.title} {e.category} {e.city} {e.venue} {e.description}'.lower()
        scored.append((sum(1 for w in words if w in blob), e))
    scored.sort(key=lambda x: -x[0])
    return [e for s, e in scored[:k] if s > 0] or [e for _, e in scored[:k]]


def chat(question, events):
    ctx = '\n'.join(event_context(e) for e in events)
    prompt = ('Answer the attendee question using ONLY the event data below. If the answer is not in the data, say you do not '
              'know and suggest contacting the organiser. Reply in the SAME LANGUAGE as the question '
              '(Malayalam, English or Hindi). Keep it short.\n\nEVENT DATA:\n' + ctx + f'\n\nQUESTION: {question}')
    return generate(SYSTEM, prompt, lambda: _fallback_chat(question, events), 400)


def _fallback_chat(q, events):
    e = events[0]
    ql = q.lower()
    if any(w in ql for w in ['park', 'പാർക്ക്']):
        return f'Parking: {e.parking_info or "Parking details are not given yet. Please contact the organiser."}'
    if any(w in ql for w in ['when', 'time', 'date', 'start', 'സമയം', 'എപ്പോൾ', 'തീയതി']):
        return f'{e.title} is on {e.start:%A, %d %B %Y} from {e.start:%I:%M %p} to {e.end:%I:%M %p}.'
    if any(w in ql for w in ['where', 'venue', 'location', 'place', 'എവിടെ', 'വേദി']):
        return f'The venue is {e.venue}, {e.city}.'
    if any(w in ql for w in ['ticket', 'price', 'cost', 'fee', 'book', 'ടിക്കറ്റ്', 'വില']):
        types = ', '.join(f'{t.name}: Rs.{t.price}' for t in e.ticket_types.all()) or 'not announced yet'
        return f'Tickets for {e.title} - {types}. Seats left: {e.seats_left}.'
    if any(w in ql for w in ['speaker', 'guest', 'who']):
        return f'Speakers: {e.speakers or "to be announced"}.'
    return (f'{e.title} ({e.category}) - {e.start:%d %b %Y, %I:%M %p} at {e.venue}, {e.city}. '
            'Ask me about timing, venue, tickets or parking.')


# ---------------- 5. Feedback Analyzer ----------------
def summarise_feedback(event):
    fbs = list(event.feedbacks.all())
    if not fbs:
        return 'No feedback received yet.'
    text = '\n'.join(f'{f.rating}/5: {f.comment}' for f in fbs)
    prompt = ('Summarise this attendee feedback as: AVERAGE RATING, HIGHLIGHTS (3 bullets), IMPROVEMENTS (3 bullets).\n' + text)
    return generate(SYSTEM, prompt, lambda: _fallback_feedback(fbs), 600)


def _fallback_feedback(fbs):
    avg = sum(f.rating for f in fbs) / len(fbs)
    good = [f.comment for f in fbs if f.rating >= 4 and f.comment]
    bad = [f.comment for f in fbs if f.rating <= 3 and f.comment]
    out = f'AVERAGE RATING: {avg:.1f}/5 from {len(fbs)} responses\n\nHIGHLIGHTS\n'
    out += '\n'.join('- ' + g for g in good[:3]) or '- Attendees were generally positive.'
    out += '\n\nIMPROVEMENTS\n' + ('\n'.join('- ' + b for b in bad[:3]) or '- No major complaints reported.')
    return out


# ---------------- 6. Demand Predictor & Smart Pricing ----------------
def predict_demand(event):
    from datetime import timedelta
    from core.models import Event
    past = Event.objects.filter(category=event.category).exclude(pk=event.pk)
    ratios = [e.seats_sold / e.seat_limit for e in past if e.seat_limit and e.seats_sold]
    base = sum(ratios) / len(ratios) if ratios else 0.55
    price = float(event.min_price or 0)
    factor = base * (1.15 if event.start.weekday() >= 5 else 1.0) * (1.25 if price == 0 else (0.85 if price > 500 else 1.0))
    factor = max(0.25, min(factor, 0.98))
    predicted = int(event.seat_limit * factor)
    level = 'HIGH' if factor >= 0.75 else 'MEDIUM' if factor >= 0.45 else 'LOW'
    launch = event.start - timedelta(days=30)
    facts = (f'Predicted attendance: {predicted} of {event.seat_limit} seats ({factor:.0%}); demand level {level}. '
             f'Weekend event: {"yes" if event.start.weekday() >= 5 else "no"}; lowest ticket price: Rs.{price}; '
             f'past similar events used for comparison: {len(ratios)}.')

    def fallback():
        out = [f'📈 PREDICTED ATTENDANCE: {predicted} / {event.seat_limit} ({factor:.0%}) - demand {level}', '']
        if price == 0:
            out.append('💰 PRICING: Free events fill fast but no-shows are common (20-30%). Allow ~15% over-registration.')
        else:
            out.append(f'💰 PRICING: Keep Regular at Rs.{int(price)}; Early Bird at Rs.{int(price * 0.8)} (20% off) for the first 25% of seats; '
                       f'raise VIP by 10% once 70% seats are sold.')
        out.append(f'🚀 LAUNCH PLAN: Open early-bird sales on {launch:%d %b %Y} (30 days before). Send reminders 7 days and 1 day before the event.')
        out.append('📣 PROMOTION: ' + ('Demand looks strong - focus on last-week reminders.' if level == 'HIGH' else 'Run extra social promotion and a coupon code to lift registrations.'))
        return '\n'.join(out)

    prompt = ('Act as an event revenue analyst. Using these facts give: PREDICTED ATTENDANCE, PRICING ADVICE, LAUNCH TIMING, PROMOTION TIPS '
              '(short, with emojis).\n' + facts + f'\nEvent: {event.title} ({event.category}), city {event.city}.')
    return generate(SYSTEM, prompt, fallback, 600)