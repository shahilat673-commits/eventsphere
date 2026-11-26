from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Event, User
from core.services import notify


class Command(BaseCommand):
    help = 'Send email/SMS reminders for events starting in the next 24 hours (run daily via cron)'

    def handle(self, *a, **kw):
        now = timezone.now()
        for e in Event.objects.filter(start__range=(now, now + timedelta(hours=24))):
            for u in User.objects.filter(bookings__event=e, bookings__status='PAID').distinct():
                m = f'Reminder: {e.title} starts {e.start:%d %b, %I:%M %p} at {e.venue}.'
                notify.send_email(u.email, f'Reminder: {e.title}', m); notify.send_sms(u.phone, m)
        self.stdout.write('Reminders sent.')
