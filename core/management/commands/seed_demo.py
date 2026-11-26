from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Category, Event, TicketType, User, Booking, Ticket


class Command(BaseCommand):
    help = 'Create demo users, categories and events'

    def handle(self, *a, **kw):
        for n in ['Workshop', 'Tech Fest', 'Concert', 'Sports', 'Conference']:
            Category.objects.get_or_create(name=n)
        def mk(username, pw, role, **extra):
            u, created = User.objects.get_or_create(username=username, defaults=dict(role=role, is_verified=True, is_approved=True, **extra))
            if created:
                u.set_password(pw); u.save()
            return u
        admin = mk('admin', 'Admin@12345', 'ADMIN', email='admin@eventsphere.local', phone='9000000001', is_staff=True, is_superuser=True)
        org = mk('organiser1', 'Organiser@123', 'ORGANISER', email='organiser@eventsphere.local', phone='9000000002', organisation='NSS College of Engineering', first_name='Anil')
        att = mk('attendee1', 'Attendee@123', 'ATTENDEE', email='attendee@eventsphere.local', phone='9000000003', first_name='Meera', location='Palakkad')
        att.interests.set(Category.objects.filter(name__in=['Tech Fest', 'Workshop']))
        if not Event.objects.exists():
            now = timezone.now()
            data = [('TechNova 2026 - Annual Tech Fest', 'Tech Fest', 'Hackathon, robotics, coding contests and project expo for engineering students.', 'Main Auditorium, NSS College', 'Palakkad', 5, 'Dr. Rahul Nair, Ms. Sneha Menon', 300, 'Free parking at the north gate, 200 two-wheeler slots.'),
                    ('AI & Django Workshop', 'Workshop', 'Hands-on one-day workshop to build AI powered web apps using Django.', 'CS Seminar Hall', 'Kochi', 9, 'Mr. Arjun Das', 80, 'Parking available in the college ground.'),
                    ('Monsoon Melodies Live', 'Concert', 'An evening of live music with popular bands.', 'Open Air Stadium', 'Thrissur', 14, 'The Rhythm Band', 500, 'Paid parking near stadium gate 2.'),
                    ('Startup Summit Kerala', 'Conference', 'Founders, investors and students discuss the startup ecosystem.', 'Convention Centre', 'Kochi', 20, 'Industry panel', 200, 'Basement parking available.')]
            for title, cat, desc, venue, city, days, sp, seats, park in data:
                s = now + timedelta(days=days, hours=1)
                e = Event.objects.create(organiser=org, category=Category.objects.get(name=cat), title=title, description=desc, venue=venue, city=city,
                                         start=s, end=s + timedelta(hours=6), speakers=sp, seat_limit=seats, parking_info=park,
                                         faq='Carry your college ID and QR e-ticket. Outside food is not allowed.')
                if cat in ('Concert', 'Conference'):   # demo of reserved seating (organiser can toggle it per event)
                    e.seating_enabled, e.seats_per_row = True, 10
                    e.save()
                TicketType.objects.create(event=e, name='Free Entry' if 'Tech' in title else 'Regular', kind='FREE' if 'Tech' in title else 'REGULAR', price=0 if 'Tech' in title else 299, quantity=seats // 2)
                TicketType.objects.create(event=e, name='VIP', kind='VIP', price=999, quantity=seats // 10)
                TicketType.objects.create(event=e, name='Early Bird', kind='EARLY', price=199, quantity=seats // 5)
        self.stdout.write(self.style.SUCCESS('Demo data ready.\n  Admin:     admin / Admin@12345\n  Organiser: organiser1 / Organiser@123\n  Attendee:  attendee1 / Attendee@123'))
