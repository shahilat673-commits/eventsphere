import csv
import json
import secrets
import time
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import F as models_F
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import ExtractHour, Lower, TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timesince import timesince
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .decorators import role_required
from .forms import (CouponForm, EventForm, FeedbackForm, LoginForm, ProfileForm, RegisterForm, TicketTypeForm)
from .models import (OTP, SEAT_HOLD_MINUTES, Announcement, Booking, Category, Coupon, Event, EventView, Feedback, NotificationLog,
                     SearchLog, SOSAlert, Ticket, TicketType, User)
from .services import ai, assistant, notify, payments, qr
from .services.certificate import build_certificate


# ====================== PUBLIC ======================
def home(request):
    events = Event.objects.filter(end__gte=timezone.now()).select_related('category', 'organiser')
    q, cat = request.GET.get('q', '').strip(), request.GET.get('category', '')
    if q:
        events = events.filter(Q(title__icontains=q) | Q(city__icontains=q) | Q(venue__icontains=q))
    if cat.isdigit():
        events = events.filter(category_id=cat)
    if (q or cat.isdigit()) and not (request.user.is_authenticated and request.user.role == 'ADMIN'):
        SearchLog.objects.create(user=request.user if request.user.is_authenticated else None, query=q[:120],
                                 category=Category.objects.filter(pk=cat).first() if cat.isdigit() else None,
                                 results=events.count())   # feeds the admin analytics dashboard
    stats = {'events': Event.objects.count(), 'attendees': User.objects.filter(role='ATTENDEE').count(),
             'tickets': Ticket.objects.filter(booking__status='PAID').count(), 'cities': Event.objects.values('city').distinct().count()}
    return render(request, 'home.html', {'events': events, 'categories': Category.objects.all(), 'q': q, 'cat': cat, 'stats': stats})


def _log_view(request, event):
    """Count one 'browse' per visitor per event per 30 minutes (admin and the event's own organiser are ignored)."""
    u = request.user
    if u.is_authenticated and (u.role == 'ADMIN' or event.organiser_id == u.id):
        return
    now = time.time()
    seen = {k: t for k, t in request.session.get('seen_events', {}).items() if now - t < 1800}
    if str(event.pk) in seen:
        return
    seen[str(event.pk)] = now
    request.session['seen_events'] = seen
    EventView.objects.create(event=event, user=u if u.is_authenticated else None)


def event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)
    _log_view(request, event)
    ctx = {'event': event, 'ticket_types': event.ticket_types.all(), 'feedbacks': event.feedbacks.select_related('attendee')[:10]}
    ctx['announcements'] = event.announcements.all()[:5]
    agg = event.feedbacks.aggregate(a=Avg('rating'), n=Count('id'))
    ctx['avg_rating'], ctx['rating_count'] = (round(agg['a'], 1) if agg['a'] else None), agg['n']
    if request.user.is_authenticated and request.user.is_attendee:
        ctx['has_ticket'] = Ticket.objects.filter(booking__event=event, booking__attendee=request.user, booking__status='PAID').exists()
        ctx['can_feedback'] = ctx['has_ticket']   # feedback is open to everyone who has booked
        ctx['my_feedback'] = Feedback.objects.filter(event=event, attendee=request.user).first()
        ctx['feedback_form'] = FeedbackForm(instance=ctx['my_feedback'])
    return render(request, 'event_detail.html', ctx)


# ====================== AUTH ======================
def _issue_otp(request, user):
    code = f'{secrets.randbelow(10 ** 6):06d}'
    OTP.objects.create(user=user, code=code)
    text = f'Your EventSphere AI verification OTP is {code}. It is valid for 10 minutes. Do not share it with anyone.'
    html = (f'<div style="font-family:Arial,sans-serif;max-width:420px;margin:auto;border:1px solid #EFE3DC;border-radius:14px;padding:24px">'
            f'<h2 style="color:#D97757;margin:0 0 8px">EventSphere AI</h2>'
            f'<p>Hi {user.first_name or user.username}, use this code to verify your account:</p>'
            f'<div style="font-size:34px;font-weight:700;letter-spacing:8px;background:#FBEDE5;border-radius:10px;padding:14px;text-align:center">{code}</div>'
            f'<p style="color:#777;font-size:13px">Valid for 10 minutes. If you did not sign up, ignore this email.</p></div>')
    sent = notify.send_email(user.email, 'EventSphere AI - your OTP code', text, html=html)
    notify.send_sms(user.phone, text)
    if settings.SHOW_OTP_ON_SCREEN:
        messages.info(request, f'DEMO MODE: your OTP is {code} (email is not configured, so it is shown here).')
    elif sent and settings.EMAIL_CONFIGURED:
        messages.success(request, 'OTP sent to your email. Please check your inbox (and the Spam folder).')
    else:
        reason = getattr(notify, 'last_error', '') or 'email is not configured in .env'
        if settings.DEBUG:
            messages.error(request, f'Could not send the OTP email ({reason}). Run "python manage.py test_email" in the terminal to find the cause.')
        else:
            messages.error(request, 'We could not send the OTP email. Please try again in a moment.')
        if settings.DEMO_MODE:      # so you are never blocked while testing
            messages.info(request, f'DEMO fallback: your OTP is {code}.')


@never_cache
@ensure_csrf_cookie
def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.is_verified = False
        user.is_approved = user.role != User.ORGANISER
        user.save()
        _issue_otp(request, user)
        request.session['pending_user'] = user.pk
        return redirect('verify_otp')
    return render(request, 'register.html', {'form': form})


@never_cache
@ensure_csrf_cookie
def verify_otp(request):
    uid = request.session.get('pending_user')
    user = User.objects.filter(pk=uid).first() if uid else None
    if not user:
        return redirect('login')
    if request.method == 'POST':
        key = f'otpfail:{user.pk}'
        if cache.get(key, 0) >= 5:
            messages.error(request, 'Too many wrong attempts. Please request a new OTP later.')
        else:
            otp = OTP.objects.filter(user=user, used=False).order_by('-created_at').first()
            if otp and otp.is_valid() and secrets.compare_digest(otp.code, request.POST.get('otp', '').strip()):
                otp.used = True; otp.save()
                user.is_verified = True; user.save()
                request.session.pop('pending_user', None)
                cache.delete(key)
                if user.role == User.ORGANISER and not user.is_approved:
                    messages.success(request, 'Verified! Your organiser account is now waiting for admin approval.')
                    return redirect('login')
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                messages.success(request, 'Account verified. Welcome to EventSphere AI!')
                return redirect('dashboard')
            cache.set(key, cache.get(key, 0) + 1, 600)
            messages.error(request, 'Invalid or expired OTP.')
    return render(request, 'verify_otp.html', {'user_obj': user})


def resend_otp(request):
    user = User.objects.filter(pk=request.session.get('pending_user')).first()
    if user:
        _issue_otp(request, user)      # shows the correct success/error message itself
        return redirect('verify_otp')
    return redirect('login')


@never_cache
@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        key = 'loginfail:' + request.META.get('REMOTE_ADDR', '') + ':' + request.POST.get('username', '').lower()
        if cache.get(key, 0) >= 5:
            messages.error(request, 'Too many failed attempts. Try again in 5 minutes.')
            return render(request, 'login.html', {'form': LoginForm(request)})
        if form.is_valid():
            user = form.get_user()
            cache.delete(key)
            if not user.is_verified:
                request.session['pending_user'] = user.pk
                _issue_otp(request, user)
                return redirect('verify_otp')
            if user.role == User.ORGANISER and not user.is_approved:
                messages.warning(request, 'Your organiser account is still awaiting admin approval.')
                return redirect('login')
            login(request, user)
            nxt = request.GET.get('next', '')
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
                return redirect(nxt)
            return redirect('dashboard')
        cache.set(key, cache.get(key, 0) + 1, 300)
    return render(request, 'login.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def dashboard(request):
    """Every role lands on its own dashboard - and sees only its own tasks."""
    return redirect({'ADMIN': 'admin_dashboard', 'ORGANISER': 'org_dashboard'}.get(request.user.role, 'my_tickets'))


# ====================== ATTENDEE ======================
@role_required('ATTENDEE')
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile & interests saved. Your recommendations are updated!')
        return redirect('my_tickets')
    return render(request, 'profile.html', {'form': form})


@role_required('ATTENDEE')
def my_tickets(request):
    bookings = request.user.bookings.filter(status='PAID').select_related('event', 'ticket_type').prefetch_related('tickets')
    upcoming = Event.objects.filter(end__gte=timezone.now())
    recs = ai.recommend(request.user, upcoming.select_related('category').prefetch_related('ticket_types'))
    return render(request, 'my_tickets.html', {'bookings': bookings, 'recs': recs,
                                               'pending': request.user.bookings.filter(status='PENDING')})


@role_required('ATTENDEE')
def ticket_detail(request, code):
    ticket = get_object_or_404(Ticket, code=code, booking__attendee=request.user, booking__status='PAID')
    return render(request, 'ticket_detail.html', {'ticket': ticket, 'event': ticket.event})


@role_required('ATTENDEE')
def ticket_qr(request, code):
    ticket = get_object_or_404(Ticket, code=code, booking__attendee=request.user, booking__status='PAID')
    return HttpResponse(qr.qr_png(ticket.code), content_type='image/png')


@role_required('ATTENDEE')
def certificate(request, code):
    ticket = get_object_or_404(Ticket, code=code, booking__attendee=request.user, booking__status='PAID')
    if not ticket.checked_in:
        messages.error(request, 'Certificate is available only after you are checked in at the event.')
        return redirect('my_tickets')
    pdf = build_certificate(ticket, ticket.holder_name or request.user.display_name)
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="certificate_{ticket.code[:8]}.pdf"'
    return resp


def _parse_qty(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _seat_url(event, tt_id, qty, coupon=''):
    from urllib.parse import urlencode
    q = {'ticket_type': tt_id, 'quantity': qty}
    if coupon:
        q['coupon'] = coupon
    return reverse('seat_select', args=[event.pk]) + '?' + urlencode(q)


@role_required('ATTENDEE')
def seat_select(request, pk):
    """Seat map - only for events the organiser marked as 'reserved seating'."""
    event = get_object_or_404(Event, pk=pk)
    if not event.seating_enabled:
        messages.info(request, 'This event has open (common) entry - no seat selection needed.')
        return redirect('event_detail', pk=pk)
    if event.is_past:
        messages.error(request, 'This event has already ended.')
        return redirect('event_detail', pk=pk)
    tt = TicketType.objects.filter(pk=request.GET.get('ticket_type') or 0, event=event).first()
    qty = _parse_qty(request.GET.get('quantity', 1))
    if not tt:
        messages.error(request, 'Please choose a ticket type first.')
        return redirect('event_detail', pk=pk)
    if not 1 <= qty <= 5 or qty > tt.left:
        messages.error(request, f'Choose 1 to 5 tickets (only {tt.left} left for this ticket type).')
        return redirect('event_detail', pk=pk)
    taken = event.taken_seats()
    gap = max((event.seats_per_row or 10) // 2, 1)
    rows = [{'label': r, 'seats': [{'label': sl, 'taken': sl in taken, 'gap': i == gap} for i, sl in enumerate(seats)]}
            for r, seats in event.seat_rows()]
    return render(request, 'seat_select.html', {
        'event': event, 'tt': tt, 'qty': qty, 'coupon': request.GET.get('coupon', '').strip().upper()[:20],
        'rows': rows, 'price': tt.price, 'total': tt.price * qty, 'free': tt.price == 0, 'hold': SEAT_HOLD_MINUTES,
        'taken_count': len(taken), 'free_count': event.seat_limit - len(taken)})


@role_required('ATTENDEE')
@require_POST
def book(request, pk):
    event = get_object_or_404(Event, pk=pk)
    tt = get_object_or_404(TicketType, pk=request.POST.get('ticket_type'), event=event)
    qty = _parse_qty(request.POST.get('quantity', 1))
    raw_coupon = request.POST.get('coupon', '').strip().upper()
    if event.is_past:
        messages.error(request, 'This event has already ended.')
    elif not 1 <= qty <= 5:
        messages.error(request, 'You can book 1 to 5 tickets at a time.')
    elif qty > tt.left:
        messages.error(request, f'Only {tt.left} seats left for this ticket type.')
    else:
        seats = []
        if event.seating_enabled:
            order = {lbl: i for i, lbl in enumerate(event.all_seat_labels())}
            seats = [x.strip().upper() for x in request.POST.get('seats', '').split(',') if x.strip()]
            if not seats:
                messages.info(request, 'This show has reserved seating - please pick your seats.')
                return redirect(_seat_url(event, tt.pk, qty, raw_coupon))
            if len(seats) != qty or len(set(seats)) != qty or any(x not in order for x in seats):
                messages.error(request, f'Please select exactly {qty} valid seat(s).')
                return redirect(_seat_url(event, tt.pk, qty, raw_coupon))
            seats.sort(key=order.get)
        amount, discount, code = tt.price * qty, Decimal('0'), ''
        if raw_coupon:
            cp = Coupon.objects.filter(event=event, code=raw_coupon, active=True).first()
            if not cp or (cp.max_uses and cp.used >= cp.max_uses):
                messages.error(request, 'Invalid or expired coupon code.')
                return redirect(_seat_url(event, tt.pk, qty) if seats else reverse('event_detail', args=[pk]))
            discount = (amount * cp.percent / 100).quantize(Decimal('0.01'))
            amount, code = amount - discount, cp.code
            messages.info(request, f'Coupon {cp.code} applied: {cp.percent}% off (Rs.{discount} saved).')
        with transaction.atomic():
            Event.objects.select_for_update().get(pk=event.pk)   # serialise seat grabbing
            if seats and set(seats) & event.taken_seats():
                messages.error(request, 'Sorry - one or more of those seats were just taken. Please choose again.')
                return redirect(_seat_url(event, tt.pk, qty, raw_coupon))
            b = Booking.objects.create(attendee=request.user, event=event, ticket_type=tt, quantity=qty, amount=amount,
                                       discount=discount, coupon_code=code, seats=','.join(seats))  # price always from server
        if b.amount == 0:
            _finalize(b, 'free_' + secrets.token_hex(4))
            if b.status == Booking.PAID:
                messages.success(request, 'Registered! Your free e-ticket is ready.')
                return redirect('my_tickets')
            messages.error(request, 'Sorry, those tickets/seats were just taken. Please try again.')
            return redirect('event_detail', pk=pk)
        b.order_id = payments.create_order(b); b.save()
        return redirect('checkout', pk=b.pk)
    return redirect('event_detail', pk=pk)


@transaction.atomic
def _finalize(booking, payment_id):
    if booking.status == Booking.PAID:
        return
    seats = booking.seat_list
    if booking.quantity > booking.ticket_type.left or (
            seats and Ticket.objects.filter(booking__event=booking.event, booking__status=Booking.PAID,
                                            seat_label__in=seats).exists()):
        booking.status = Booking.FAILED; booking.save()
        return
    booking.status, booking.payment_id, booking.paid_at = Booking.PAID, payment_id, timezone.now()
    booking.save()
    if booking.coupon_code:
        Coupon.objects.filter(event=booking.event, code=booking.coupon_code).update(used=models_F('used') + 1)
    for i in range(booking.quantity):
        Ticket.objects.create(booking=booking, holder_name=booking.attendee.display_name,
                              seat_label=seats[i] if i < len(seats) else '')
    u, e = booking.attendee, booking.event
    seat_txt = f' Seats: {", ".join(seats)}.' if seats else ''
    notify.send_email(u.email, f'Your tickets for {e.title}',
                      f'Hi {u.display_name},\n\nYour booking #{booking.pk} is confirmed ({booking.quantity} x {booking.ticket_type.name}, '
                      f'Rs.{booking.amount}).{seat_txt}\n{e.title} - {e.start:%d %b %Y, %I:%M %p} at {e.venue}, {e.city}.\n'
                      'Open "My Tickets" to download your QR e-ticket.')
    notify.send_sms(u.phone, f'EventSphere: booking confirmed for {e.title} on {e.start:%d %b %I:%M %p}.{seat_txt} Show your QR at entry.')


@role_required('ATTENDEE')
def checkout(request, pk):
    b = get_object_or_404(Booking, pk=pk, attendee=request.user, status='PENDING')
    ctx = {'b': b, 'demo': payments.is_demo(), 'key_id': settings.RAZORPAY_KEY_ID, 'amount_paise': int(b.amount * 100),
           'hold': SEAT_HOLD_MINUTES}
    return render(request, 'checkout.html', ctx)


@role_required('ATTENDEE')
@require_POST
def demo_pay(request, pk):
    """DEMO gateway: behaves like Razorpay Checkout success callback (with signature verification)."""
    b = get_object_or_404(Booking, pk=pk, attendee=request.user, status='PENDING')
    if not payments.is_demo():
        return redirect('checkout', pk=pk)
    pid, sig = payments.demo_payment(b)
    if payments.verify(b.order_id, pid, sig):
        _finalize(b, pid)
        if b.status != Booking.PAID:
            messages.error(request, 'Sorry - these tickets/seats were taken by someone else while you were paying. No money was charged (demo).')
            return redirect('event_detail', pk=b.event_id)
        messages.success(request, f'Payment successful (demo)! Payment ID: {pid}')
        return redirect('my_tickets')
    messages.error(request, 'Payment failed.')
    return redirect('checkout', pk=pk)


@role_required('ATTENDEE')
@require_POST
def pay_verify(request):
    """Real Razorpay callback."""
    oid, pid, sig = (request.POST.get(k, '') for k in ('razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature'))
    b = get_object_or_404(Booking, order_id=oid, attendee=request.user, status='PENDING')
    if payments.verify(oid, pid, sig):
        _finalize(b, pid)
        if b.status != Booking.PAID:
            messages.error(request, 'Payment received but the seats/tickets were just taken. Please contact the organiser for a refund.')
            return redirect('event_detail', pk=b.event_id)
        messages.success(request, 'Payment successful! Your tickets are ready.')
        return redirect('my_tickets')
    b.status = Booking.FAILED; b.save()
    messages.error(request, 'Payment verification failed.')
    return redirect('event_detail', pk=b.event_id)


def _has_paid_ticket(user, event):
    return Ticket.objects.filter(booking__event=event, booking__attendee=user, booking__status='PAID').exists()


@role_required('ATTENDEE')
def my_feedback(request):
    """Feedback page: every event the attendee has booked, with a star rating + comment box."""
    given = {f.event_id: f for f in Feedback.objects.filter(attendee=request.user)}
    booked = Event.objects.filter(bookings__attendee=request.user, bookings__status='PAID').distinct().order_by('-start')
    items = [{'event': e, 'fb': given.get(e.pk)} for e in booked]
    return render(request, 'my_feedback.html', {'items': items, 'pending_count': sum(1 for i in items if not i['fb']),
                                                'given_count': sum(1 for i in items if i['fb'])})


@role_required('ATTENDEE')
@require_POST
def feedback(request, pk):
    event = get_object_or_404(Event, pk=pk)
    nxt = request.POST.get('next', '')
    back = nxt if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else reverse('event_detail', args=[pk])
    if not _has_paid_ticket(request.user, event):
        messages.error(request, 'You can give feedback only for events you have booked.')
        return redirect(back)
    form = FeedbackForm(request.POST)
    if form.is_valid():
        Feedback.objects.update_or_create(event=event, attendee=request.user, defaults=form.cleaned_data)
        messages.success(request, 'Thanks for your feedback!')
    else:
        messages.error(request, 'Please give a rating between 1 and 5 stars.')
    return redirect(back)


@role_required('ATTENDEE')
@require_POST
def assistant_api(request):
    """Booking-help chatbot (floating widget on every attendee page)."""
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'bad request'}, status=400)
    message = str(data.get('message', ''))[:300].strip()
    if not message:
        return JsonResponse({'error': 'empty'}, status=400)
    event = Event.objects.filter(pk=data['event_id']).first() if str(data.get('event_id', '')).isdigit() else None
    return JsonResponse(assistant.reply(request.user, message, event))


@login_required
@require_POST
def chat_api(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'bad request'}, status=400)
    question = str(data.get('question', ''))[:300].strip()
    if not question:
        return JsonResponse({'error': 'empty'}, status=400)
    qs = Event.objects.select_related('category', 'organiser').prefetch_related('ticket_types')
    if data.get('event_id'):
        events = list(qs.filter(pk=data['event_id'])[:1])
    else:
        events = ai.retrieve(question, list(qs.filter(end__gte=timezone.now())))
    if not events:
        return JsonResponse({'answer': 'No events available right now.'})
    return JsonResponse({'answer': ai.chat(question, events), 'engine': ai.engine_name()})


# ====================== ORGANISER ======================
def _own_event(request, pk):
    return get_object_or_404(Event, pk=pk, organiser=request.user)  # organisers can only touch their own events


@role_required('ORGANISER')
def org_dashboard(request):
    events = request.user.events.all()
    paid = Booking.objects.filter(event__organiser=request.user, status='PAID')
    stats = {'events': events.count(), 'tickets': Ticket.objects.filter(booking__in=paid).count(),
             'revenue': paid.aggregate(s=Sum('amount'))['s'] or 0}
    fee = Decimal(settings.PLATFORM_FEE_PERCENT) / 100
    stats['payout'] = (Decimal(stats['revenue']) * (1 - fee)).quantize(Decimal('0.01'))
    return render(request, 'org_dashboard.html', {'events': events, 'stats': stats})


@role_required('ORGANISER')
def event_create(request):
    form = EventForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        e = form.save(commit=False); e.organiser = request.user; e.save()
        messages.success(request, 'Event created. Now add ticket types and try the AI planner!')
        return redirect('event_manage', pk=e.pk)
    return render(request, 'event_form.html', {'form': form, 'title': 'Create Event'})


@role_required('ORGANISER')
def event_edit(request, pk):
    e = _own_event(request, pk)
    form = EventForm(request.POST or None, request.FILES or None, instance=e)
    if request.method == 'POST' and form.is_valid():
        form.save(); messages.success(request, 'Event updated.')
        return redirect('event_manage', pk=pk)
    return render(request, 'event_form.html', {'form': form, 'title': f'Edit: {e.title}'})


@role_required('ORGANISER')
def event_manage(request, pk):
    e = _own_event(request, pk)
    tickets = Ticket.objects.filter(booking__event=e, booking__status='PAID').select_related('booking__attendee', 'booking__ticket_type')
    types = list(e.ticket_types.all())
    chart = {'labels': [t.name for t in types], 'sold': [t.sold for t in types],
             'checked': e.checked_in_count, 'pending': max(e.seats_sold - e.checked_in_count, 0), 'left': e.seats_left}
    return render(request, 'event_manage.html', {
        'chart': chart, 'coupon_form': CouponForm(), 'coupons': e.coupons.all(), 'announcements': e.announcements.all()[:5],
        'sos_alerts': e.sos_alerts.exclude(status='RESOLVED'),
        'event': e, 'tickets': tickets, 'tt_form': TicketTypeForm(), 'engine': ai.engine_name(),
        'plan_lines': [l.split('|') for l in e.schedule.splitlines() if l.strip()],
        'budget_lines': [l.split('|') for l in e.budget.splitlines() if l.strip()]})


@role_required('ORGANISER')
@require_POST
def tickettype_add(request, pk):
    e = _own_event(request, pk)
    form = TicketTypeForm(request.POST)
    if form.is_valid():
        t = form.save(commit=False); t.event = e
        if t.kind == 'FREE':
            t.price = 0
        t.save(); messages.success(request, 'Ticket type added.')
    else:
        messages.error(request, 'Invalid ticket type: ' + '; '.join(sum(form.errors.values(), [])))
    return redirect('event_manage', pk=pk)


@role_required('ORGANISER')
@require_POST
def tickettype_delete(request, pk):
    t = get_object_or_404(TicketType, pk=pk, event__organiser=request.user)
    if t.sold:
        messages.error(request, 'Tickets already sold for this type; cannot delete.')
    else:
        t.delete(); messages.success(request, 'Ticket type removed.')
    return redirect('event_manage', pk=t.event_id)


@role_required('ORGANISER')
@require_POST
def event_ai(request, pk, kind):
    e = _own_event(request, pk)
    if kind == 'plan':
        r = ai.plan_event(e); e.schedule, e.checklist, e.budget = r['schedule'], r['checklist'], r['budget']
    elif kind == 'content':
        e.promo_content = ai.write_content(e)
    elif kind == 'feedback':
        e.feedback_summary = ai.summarise_feedback(e)
    elif kind == 'demand':
        e.demand_forecast = ai.predict_demand(e)
    else:
        return redirect('event_manage', pk=pk)
    e.save()
    messages.success(request, f'AI ({ai.engine_name()}) has generated the {kind}.')
    return redirect('event_manage', pk=pk)


@role_required('ORGANISER')
def attendee_export(request, pk):
    e = _own_event(request, pk)
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="attendees_{e.pk}.csv"'
    w = csv.writer(resp)
    w.writerow(['Ticket code', 'Name', 'Email', 'Phone', 'Ticket type', 'Paid (Rs)', 'Checked in', 'Check-in time', 'Seat'])
    for t in Ticket.objects.filter(booking__event=e, booking__status='PAID').select_related('booking__attendee', 'booking__ticket_type'):
        u = t.booking.attendee
        w.writerow([t.code, t.holder_name, u.email, u.phone, t.booking.ticket_type.name,
                    t.booking.ticket_type.price, 'Yes' if t.checked_in else 'No', t.checked_in_at or '', t.seat_label])
    return resp


@role_required('ORGANISER')
@require_POST
def send_reminders(request, pk):
    e = _own_event(request, pk)
    users = User.objects.filter(bookings__event=e, bookings__status='PAID').distinct()
    for u in users:
        msg = f'Reminder: {e.title} starts {e.start:%d %b %Y, %I:%M %p} at {e.venue}, {e.city}. Carry your QR e-ticket.'
        notify.send_email(u.email, f'Reminder: {e.title}', msg); notify.send_sms(u.phone, msg)
    messages.success(request, f'Reminders sent to {users.count()} attendee(s).')
    return redirect('event_manage', pk=pk)


@role_required('ORGANISER')
def scanner(request):
    return render(request, 'scanner.html', {'events': request.user.events.all()})


@role_required('ORGANISER')
@require_POST
def scan_api(request):
    try:
        code = qr.parse(json.loads(request.body).get('code', ''))
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Bad request'}, status=400)
    t = Ticket.objects.filter(code=code).select_related('booking__event', 'booking__attendee', 'booking__ticket_type').first()
    if not t or t.booking.event.organiser_id != request.user.id:
        return JsonResponse({'status': 'invalid', 'message': 'INVALID TICKET - not found for your events (possible fake)'})
    b, e = t.booking, t.booking.event
    info = {'name': t.holder_name, 'event': e.title, 'type': b.ticket_type.name, 'seat': t.seat_label,
            'payment': b.status, 'amount': str(b.amount), 'payment_id': b.payment_id}
    if b.status != Booking.PAID:
        return JsonResponse({'status': 'unpaid', 'message': 'PAYMENT NOT COMPLETED', **info})
    with transaction.atomic():
        t = Ticket.objects.select_for_update().get(pk=t.pk)
        if t.checked_in:
            return JsonResponse({'status': 'duplicate', 'message': f'ALREADY USED at {timezone.localtime(t.checked_in_at):%I:%M %p}', **info})
        t.checked_in, t.checked_in_at = True, timezone.now(); t.save()
    return JsonResponse({'status': 'ok', 'message': 'ENTRY ALLOWED - PAYMENT VERIFIED', 'checked_in': e.checked_in_count,
                         'total': e.seats_sold, **info})


# ====================== ADMIN PANEL ======================
@role_required('ADMIN')
def admin_dashboard(request):
    paid = Booking.objects.filter(status='PAID')
    revenue = paid.aggregate(s=Sum('amount'))['s'] or 0
    fee = Decimal(settings.PLATFORM_FEE_PERCENT) / 100
    stats = {'events': Event.objects.count(), 'tickets': Ticket.objects.filter(booking__in=paid).count(),
             'revenue': revenue, 'commission': (Decimal(revenue) * fee).quantize(Decimal('0.01')),
             'pending': User.objects.filter(role='ORGANISER', is_approved=False, is_verified=True).count(),
             'attendees': User.objects.filter(role='ATTENDEE').count()}
    payouts = []
    for o in User.objects.filter(role='ORGANISER', is_approved=True):
        gross = Booking.objects.filter(event__organiser=o, status='PAID').aggregate(s=Sum('amount'))['s'] or 0
        payouts.append({'org': o, 'gross': gross, 'net': (Decimal(gross) * (1 - fee)).quantize(Decimal('0.01'))})
    top = Event.objects.annotate(n=Count('bookings', filter=Q(bookings__status='PAID'))).order_by('-n')[:5]
    ev = Event.objects.annotate(r=Sum('bookings__amount', filter=Q(bookings__status='PAID'))).order_by('-r')[:6]
    chart = {'labels': [e.title[:22] for e in ev], 'values': [float(e.r or 0) for e in ev]}
    stats['sos'] = SOSAlert.objects.exclude(status='RESOLVED').count()
    return render(request, 'admin_panel/dashboard.html', {'stats': stats, 'payouts': payouts, 'top': top, 'chart': chart,
                                                          'logs': NotificationLog.objects.all()[:8]})


@role_required('ADMIN')
def admin_analytics(request):
    """Searching & browsing analytics (bar / pie / doughnut / line charts). Sales charts already live on the main dashboard."""
    days = int(request.GET['days']) if request.GET.get('days') in ('7', '14', '30', '90') else 14
    today = timezone.localdate()
    start_day = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start_day, datetime.min.time()))
    views = EventView.objects.filter(created_at__gte=since)
    searches = SearchLog.objects.filter(created_at__gte=since)

    day_list = [start_day + timedelta(days=i) for i in range(days)]
    vday = {r['d']: r['n'] for r in views.annotate(d=TruncDate('created_at')).values('d').annotate(n=Count('id'))}
    sday = {r['d']: r['n'] for r in searches.annotate(d=TruncDate('created_at')).values('d').annotate(n=Count('id'))}
    top_events = list(views.values('event__title').annotate(n=Count('id')).order_by('-n')[:8])
    by_cat = list(views.values('event__category__name').annotate(n=Count('id')).order_by('-n'))
    kw = list(searches.exclude(query='').annotate(q=Lower('query')).values('q').annotate(n=Count('id')).order_by('-n')[:8])
    s_cat = list(searches.exclude(category=None).values('category__name').annotate(n=Count('id')).order_by('-n'))
    hours_v = {r['h']: r['n'] for r in views.annotate(h=ExtractHour('created_at')).values('h').annotate(n=Count('id'))}
    hours_s = {r['h']: r['n'] for r in searches.annotate(h=ExtractHour('created_at')).values('h').annotate(n=Count('id'))}
    found = searches.filter(results__gt=0).count()
    total_s, total_v = searches.count(), views.count()
    chart = {
        'days': [d.strftime('%d %b') for d in day_list],
        'views_daily': [vday.get(d, 0) for d in day_list], 'searches_daily': [sday.get(d, 0) for d in day_list],
        'top_events': {'labels': [r['event__title'][:24] for r in top_events], 'values': [r['n'] for r in top_events]},
        'by_cat': {'labels': [r['event__category__name'] for r in by_cat], 'values': [r['n'] for r in by_cat]},
        'keywords': {'labels': [r['q'] for r in kw], 'values': [r['n'] for r in kw]},
        'search_cat': {'labels': [r['category__name'] for r in s_cat], 'values': [r['n'] for r in s_cat]},
        'found': {'labels': ['Results found', 'No results'], 'values': [found, total_s - found]},
        'hours': {'labels': [f'{h:02d}:00' for h in range(24)], 'views': [hours_v.get(h, 0) for h in range(24)],
                  'searches': [hours_s.get(h, 0) for h in range(24)]},
    }
    kpi = {'views': total_v, 'searches': total_s,
           'viewers': views.exclude(user=None).values('user').distinct().count(),
           'guest_views': views.filter(user=None).count(),
           'avg_views': round(total_v / days, 1), 'no_result': total_s - found,
           'top_event': top_events[0]['event__title'] if top_events else '-',
           'top_keyword': kw[0]['q'] if kw else '-'}
    return render(request, 'admin_panel/analytics.html', {
        'chart': chart, 'kpi': kpi, 'days': days, 'ranges': (7, 14, 30, 90),
        'recent_searches': SearchLog.objects.select_related('user', 'category')[:10]})


@role_required('ADMIN')
def admin_organisers(request):
    return render(request, 'admin_panel/organisers.html', {'organisers': User.objects.filter(role='ORGANISER', is_verified=True).order_by('is_approved', '-date_joined')})


@role_required('ADMIN')
@require_POST
def admin_organiser_action(request, pk, action):
    u = get_object_or_404(User, pk=pk, role='ORGANISER')
    if action == 'approve':
        u.is_approved = True; u.save()
        notify.send_email(u.email, 'EventSphere AI - organiser approved', 'Your organiser account is approved. You can log in and create events.')
        messages.success(request, f'{u.username} approved.')
    elif action == 'reject':
        u.is_approved = False; u.save(); messages.warning(request, f'{u.username} access revoked.')
    return redirect('admin_organisers')


@role_required('ADMIN')
def admin_categories(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:60]
        if name:
            Category.objects.get_or_create(name=name); messages.success(request, 'Category saved.')
        return redirect('admin_categories')
    return render(request, 'admin_panel/categories.html', {'categories': Category.objects.annotate(n=Count('event'))})


@role_required('ADMIN')
@require_POST
def admin_category_delete(request, pk):
    c = get_object_or_404(Category, pk=pk)
    if c.event_set.exists():
        messages.error(request, 'Category is used by events and cannot be deleted.')
    else:
        c.delete(); messages.success(request, 'Category deleted.')
    return redirect('admin_categories')


@role_required('ADMIN')
def admin_payments(request):
    return render(request, 'admin_panel/payments.html', {'bookings': Booking.objects.select_related('attendee', 'event')[:200]})


@role_required('ADMIN')
def admin_report_csv(request):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="eventsphere_report.csv"'
    w = csv.writer(resp)
    w.writerow(['Booking', 'Event', 'Organiser', 'Attendee', 'Qty', 'Amount', 'Status', 'Payment ID', 'Date'])
    for b in Booking.objects.select_related('attendee', 'event__organiser'):
        w.writerow([b.pk, b.event.title, b.event.organiser.username, b.attendee.username, b.quantity, b.amount, b.status, b.payment_id, b.created_at])
    return resp


def csrf_failure(request, reason=''):
    """Friendly page instead of Django's raw 403 CSRF error."""
    return render(request, 'csrf_failed.html', status=403)


# ====================== EXTRAS: coupons, announcements, calendar ======================
@role_required('ORGANISER')
@require_POST
def coupon_add(request, pk):
    e = _own_event(request, pk)
    form = CouponForm(request.POST)
    if form.is_valid():
        if Coupon.objects.filter(event=e, code=form.cleaned_data['code']).exists():
            messages.error(request, 'Coupon code already exists.')
        else:
            c = form.save(commit=False); c.event = e; c.save(); messages.success(request, 'Coupon created.')
    else:
        messages.error(request, 'Invalid coupon.')
    return redirect('event_manage', pk=pk)


@role_required('ORGANISER')
@require_POST
def coupon_delete(request, pk):
    c = get_object_or_404(Coupon, pk=pk, event__organiser=request.user)
    c.delete(); messages.success(request, 'Coupon removed.')
    return redirect('event_manage', pk=c.event_id)


@role_required('ORGANISER')
@require_POST
def announce(request, pk):
    """Broadcast a live announcement to everyone with a ticket (email + SMS + shown on event page)."""
    e = _own_event(request, pk)
    msg = request.POST.get('message', '').strip()[:300]
    if msg:
        Announcement.objects.create(event=e, message=msg)
        users = User.objects.filter(bookings__event=e, bookings__status='PAID').distinct()
        for u in users:
            notify.send_email(u.email, f'Update: {e.title}', msg); notify.send_sms(u.phone, f'{e.title}: {msg}')
        messages.success(request, f'Announcement sent to {users.count()} attendee(s).')
    return redirect('event_manage', pk=pk)


@role_required('ATTENDEE')
def ticket_ics(request, code):
    t = get_object_or_404(Ticket, code=code, booking__attendee=request.user, booking__status='PAID')
    e = t.event
    from datetime import timezone as dtz
    f = lambda d: d.astimezone(dtz.utc).strftime('%Y%m%dT%H%M%SZ')
    ics = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//EventSphere AI//EN\r\nBEGIN:VEVENT\r\n'
           f'UID:{t.code}@eventsphere\r\nDTSTAMP:{f(timezone.now())}\r\nDTSTART:{f(e.start)}\r\nDTEND:{f(e.end)}\r\n'
           f'SUMMARY:{e.title}\r\nLOCATION:{e.venue}, {e.city}\r\nDESCRIPTION:Show your QR e-ticket at the entry gate.\r\n'
           'END:VEVENT\r\nEND:VCALENDAR\r\n')
    resp = HttpResponse(ics, content_type='text/calendar')
    resp['Content-Disposition'] = f'attachment; filename="{e.pk}_event.ics"'
    return resp


# ====================== SOS EMERGENCY SYSTEM ======================
def _sos_json(a):
    return {'id': a.pk, 'event': a.event.title, 'event_id': a.event_id, 'attendee': a.attendee.display_name,
            'phone': a.attendee.phone, 'category': a.category, 'category_label': a.get_category_display(),
            'message': a.message, 'location_text': a.location_text, 'maps': a.maps_url, 'status': a.status,
            'escalated': a.escalated, 'ago': timesince(a.created_at) + ' ago', 'note': a.response_note,
            'responder': a.responder.display_name if a.responder else '',
            'venue': a.event.venue, 'emergency_contact': a.event.emergency_contact}


@role_required('ATTENDEE')
def sos_page(request):
    now = timezone.now()
    events = Event.objects.filter(end__gte=now, bookings__attendee=request.user, bookings__status='PAID').distinct()
    return render(request, 'sos.html', {'events': events, 'categories': SOSAlert.CATEGORIES})


@role_required('ATTENDEE')
@require_POST
def sos_send(request):
    try:
        d = json.loads(request.body)
        event = Event.objects.get(pk=d.get('event_id'))
    except (ValueError, Event.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'Select a valid event.'}, status=400)
    if event.end < timezone.now() or not Ticket.objects.filter(booking__event=event, booking__attendee=request.user, booking__status='PAID').exists():
        return JsonResponse({'ok': False, 'error': 'You need a valid ticket for a running/upcoming event to raise an SOS.'}, status=403)
    cat = d.get('category') if d.get('category') in dict(SOSAlert.CATEGORIES) else 'OTHER'
    existing = SOSAlert.objects.filter(event=event, attendee=request.user).exclude(status='RESOLVED').first()
    if existing:  # no duplicates - return the active one
        return JsonResponse({'ok': True, 'duplicate': True, 'alert': _sos_json(existing)})

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    lat, lng = num(d.get('lat')), num(d.get('lng'))
    if lat is not None and not (-90 <= lat <= 90 and -180 <= (lng if lng is not None else 0) <= 180):
        lat = lng = None
    a = SOSAlert.objects.create(event=event, attendee=request.user, category=cat, message=str(d.get('message', ''))[:300],
                                location_text=str(d.get('location_text', ''))[:120], latitude=lat, longitude=lng)
    text = (f'🚨 SOS at {event.title}: {a.get_category_display()} - {request.user.display_name} ({request.user.phone}). '
            f'{a.location_text or "location not given"}. {a.message}')
    notify.send_email(event.organiser.email, f'🚨 SOS ALERT - {event.title}', text)
    notify.send_sms(event.organiser.phone, text)
    if event.emergency_contact:
        notify.send_sms(event.emergency_contact, text)
    return JsonResponse({'ok': True, 'alert': _sos_json(a)})


@role_required('ATTENDEE')
def sos_mine(request):
    alerts = SOSAlert.objects.filter(attendee=request.user).select_related('event', 'responder')[:10]
    return JsonResponse({'alerts': [_sos_json(a) for a in alerts]})


def _sos_qs(user):
    qs = SOSAlert.objects.select_related('event', 'attendee', 'responder')
    return qs if user.role == 'ADMIN' else qs.filter(event__organiser=user)


@role_required('ORGANISER', 'ADMIN')
def sos_control(request):
    return render(request, 'sos_control.html')


@role_required('ORGANISER', 'ADMIN')
def sos_feed(request):
    qs = _sos_qs(request.user)
    if request.GET.get('all') != '1':
        qs = qs.exclude(status='RESOLVED')
    items = list(qs[:50])
    return JsonResponse({'open': sum(1 for a in items if a.status == 'OPEN'), 'alerts': [_sos_json(a) for a in items]})


@role_required('ORGANISER', 'ADMIN')
@require_POST
def sos_action(request, pk, action):
    a = get_object_or_404(_sos_qs(request.user), pk=pk)
    try:
        note = str(json.loads(request.body or '{}').get('note', ''))[:300]
    except ValueError:
        note = ''
    who = request.user.display_name + (' (Admin/Authority)' if request.user.role == 'ADMIN' else ' (Organiser)')
    if action == 'ack' and a.status == 'OPEN':
        a.status, a.responder, a.acknowledged_at = 'ACK', request.user, timezone.now()
        a.response_note = note or 'Help is on the way.'
        msg = f'EventSphere SOS: {who} has acknowledged your alert. Help is on the way. Stay where you are.'
    elif action == 'resolve' and a.status != 'RESOLVED':
        a.status, a.resolved_at = 'RESOLVED', timezone.now()
        a.responder = a.responder or request.user
        a.response_note = note or 'Resolved.'
        msg = f'EventSphere SOS: your alert has been marked resolved by {who}. {a.response_note}'
    elif action == 'escalate' and request.user.role == 'ORGANISER':
        a.escalated = True
        msg = ''
        for adm in User.objects.filter(role='ADMIN'):
            t = f'🚨 ESCALATED SOS - {a.event.title}: {a.get_category_display()} - {a.attendee.display_name} ({a.attendee.phone}). Please take charge.'
            notify.send_email(adm.email, 'ESCALATED SOS', t); notify.send_sms(adm.phone, t)
    else:
        return JsonResponse({'ok': False, 'error': 'Action not allowed.'}, status=400)
    a.save()
    if msg:
        notify.send_sms(a.attendee.phone, msg); notify.send_email(a.attendee.email, 'SOS update', msg)
    return JsonResponse({'ok': True, 'alert': _sos_json(a)})


# ====================== SHOW REMINDER (attendee only) ======================
@role_required('ATTENDEE')
def reminders_api(request):
    """Gentle in-app reminder: events this attendee has booked that start within the next 60 minutes."""
    now = timezone.now()
    events = Event.objects.filter(start__gt=now, start__lte=now + timedelta(minutes=60),
                                  bookings__attendee=request.user, bookings__status='PAID').distinct()
    items = [{'id': e.pk, 'title': e.title, 'venue': e.venue, 'city': e.city,
              'minutes': max(int((e.start - now).total_seconds() // 60), 1)} for e in events]
    return JsonResponse({'reminders': items})