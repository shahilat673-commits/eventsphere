import uuid
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone


SEAT_HOLD_MINUTES = 10   # seats of an unpaid booking are held for this long


def new_code():
    return uuid.uuid4().hex


class Category(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    ADMIN, ORGANISER, ATTENDEE = 'ADMIN', 'ORGANISER', 'ATTENDEE'
    ROLE_CHOICES = [(ADMIN, 'Admin'), (ORGANISER, 'Event Organiser'), (ATTENDEE, 'Attendee')]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ATTENDEE)
    phone = models.CharField(max_length=15, blank=True)
    organisation = models.CharField('College / Company / Club', max_length=120, blank=True)
    is_approved = models.BooleanField(default=True)   # organisers need admin approval
    is_verified = models.BooleanField(default=False)  # OTP verified
    location = models.CharField(max_length=80, blank=True)
    interests = models.ManyToManyField(Category, blank=True)
    preferred_type = models.CharField(max_length=10, blank=True,
                                      choices=[('', 'Any'), ('FREE', 'Free events'), ('PAID', 'Paid events')])

    @property
    def is_admin_role(self):
        return self.role == self.ADMIN

    @property
    def is_organiser(self):
        return self.role == self.ORGANISER

    @property
    def is_attendee(self):
        return self.role == self.ATTENDEE

    @property
    def display_name(self):
        return self.get_full_name() or self.username


class OTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def is_valid(self):
        return not self.used and (timezone.now() - self.created_at).total_seconds() < 600


class Event(models.Model):
    organiser = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    description = models.TextField()
    banner = models.ImageField(upload_to='banners/', blank=True, null=True)
    venue = models.CharField(max_length=200)
    city = models.CharField(max_length=80)
    start = models.DateTimeField()
    end = models.DateTimeField()
    speakers = models.CharField(max_length=300, blank=True)
    seat_limit = models.PositiveIntegerField(default=100)
    parking_info = models.CharField(max_length=300, blank=True)
    faq = models.TextField(blank=True, help_text='Extra Q&A the chatbot can use')
    # AI generated fields
    schedule = models.TextField(blank=True)
    checklist = models.TextField(blank=True)
    budget = models.TextField(blank=True)
    promo_content = models.TextField(blank=True)
    feedback_summary = models.TextField(blank=True)
    demand_forecast = models.TextField(blank=True, default='')
    emergency_contact = models.CharField('Emergency / security contact number', max_length=15, blank=True, default='')
    seating_enabled = models.BooleanField('Reserved seating (attendees pick their seats)', default=False,
                                          help_text='Tick for shows with a fixed seat plan. Leave unticked for open / common entry.')
    seats_per_row = models.PositiveSmallIntegerField('Seats per row', default=10,
                                                     help_text='Only used when reserved seating is on (4 - 20).')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start']

    def __str__(self):
        return self.title

    @property
    def seats_sold(self):
        return Ticket.objects.filter(booking__event=self, booking__status=Booking.PAID).count()

    @property
    def seats_left(self):
        return max(self.seat_limit - self.seats_sold, 0)

    @property
    def checked_in_count(self):
        return Ticket.objects.filter(booking__event=self, booking__status=Booking.PAID, checked_in=True).count()

    @property
    def revenue(self):
        return self.bookings.filter(status=Booking.PAID).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    @property
    def is_past(self):
        return self.end < timezone.now()

    @property
    def min_price(self):
        p = self.ticket_types.order_by('price').first()
        return p.price if p else None

    # ---------- reserved seating ----------
    @staticmethod
    def _row_name(i):
        """0 -> A, 25 -> Z, 26 -> AA ..."""
        name = ''
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            name = chr(65 + r) + name
        return name

    def seat_rows(self):
        """Seat plan as [(row_label, [seat_label, ...]), ...] covering exactly seat_limit seats."""
        per = max(self.seats_per_row or 10, 1)
        rows, n, i = [], self.seat_limit, 0
        while n > 0:
            take = min(per, n)
            r = self._row_name(i)
            rows.append((r, [f'{r}{c}' for c in range(1, take + 1)]))
            n -= take
            i += 1
        return rows

    def all_seat_labels(self):
        return [s for _, seats in self.seat_rows() for s in seats]

    def taken_seats(self, exclude_booking=None):
        """Seats that are paid for, or held by a recent unpaid booking (held for SEAT_HOLD_MINUTES)."""
        taken = set(Ticket.objects.filter(booking__event=self, booking__status=Booking.PAID)
                    .exclude(seat_label='').values_list('seat_label', flat=True))
        cutoff = timezone.now() - timedelta(minutes=SEAT_HOLD_MINUTES)
        holds = self.bookings.filter(status=Booking.PENDING, created_at__gte=cutoff).exclude(seats='')
        if exclude_booking is not None:
            holds = holds.exclude(pk=exclude_booking.pk)
        for csv_ in holds.values_list('seats', flat=True):
            taken.update(x for x in csv_.split(',') if x)
        return taken


class TicketType(models.Model):
    FREE, REGULAR, VIP, EARLY = 'FREE', 'REGULAR', 'VIP', 'EARLY'
    KINDS = [(FREE, 'Free'), (REGULAR, 'Regular'), (VIP, 'VIP'), (EARLY, 'Early Bird')]
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_types')
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=KINDS, default=REGULAR)
    price = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=50)

    def __str__(self):
        return f'{self.event.title} - {self.name}'

    @property
    def sold(self):
        return Ticket.objects.filter(booking__ticket_type=self, booking__status=Booking.PAID).count()

    @property
    def left(self):
        return max(min(self.quantity - self.sold, self.event.seats_left), 0)


class Booking(models.Model):
    PENDING, PAID, FAILED = 'PENDING', 'PAID', 'FAILED'
    attendee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bookings')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=8, default=PENDING)
    order_id = models.CharField(max_length=60, blank=True)
    payment_id = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    coupon_code = models.CharField(max_length=20, blank=True, default='')
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    seats = models.CharField(max_length=300, blank=True, default='', help_text='Chosen seats (comma separated) for reserved-seating events')

    class Meta:
        ordering = ['-created_at']

    @property
    def seat_list(self):
        return [x for x in self.seats.split(',') if x]


class Ticket(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='tickets')
    code = models.CharField(max_length=32, unique=True, default=new_code)
    holder_name = models.CharField(max_length=100, blank=True)
    seat_label = models.CharField(max_length=8, blank=True, default='')
    checked_in = models.BooleanField(default=False)
    checked_in_at = models.DateTimeField(null=True, blank=True)

    @property
    def event(self):
        return self.booking.event


class Feedback(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='feedbacks')
    attendee = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('event', 'attendee')


class NotificationLog(models.Model):
    channel = models.CharField(max_length=10)
    recipient = models.CharField(max_length=120)
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Coupon(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='coupons')
    code = models.CharField(max_length=20)
    percent = models.PositiveSmallIntegerField(default=10)
    max_uses = models.PositiveIntegerField(default=0, help_text='0 = unlimited')
    used = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('event', 'code')


class Announcement(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='announcements')
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SOSAlert(models.Model):
    """Emergency alert raised by an attendee; handled by the organiser / admin (authority)."""
    OPEN, ACK, RESOLVED = 'OPEN', 'ACK', 'RESOLVED'
    CATEGORIES = [('MEDICAL', '🚑 Medical emergency'), ('FIRE', '🔥 Fire / smoke'),
                  ('SECURITY', '🛡️ Harassment / security threat'), ('LOST', '🧒 Lost person / child'),
                  ('CROWD', '👥 Crowd crush risk'), ('OTHER', '⚠️ Other emergency')]
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='sos_alerts')
    attendee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sos_alerts')
    category = models.CharField(max_length=10, choices=CATEGORIES, default='OTHER')
    message = models.CharField(max_length=300, blank=True)
    location_text = models.CharField(max_length=120, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, default=OPEN)
    escalated = models.BooleanField(default=False)
    responder = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='handled_sos')
    response_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f'https://www.google.com/maps?q={self.latitude},{self.longitude}'
        return ''


class SearchLog(models.Model):
    """One row per search / category-filter on the Discover page (used by the admin analytics dashboard)."""
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='searches')
    query = models.CharField(max_length=120, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    results = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class EventView(models.Model):
    """One row per event page view (browsing) - used by the admin analytics dashboard."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='event_views')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
