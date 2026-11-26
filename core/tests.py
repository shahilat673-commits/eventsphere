from django.test import TestCase, Client
from django.core.management import call_command
from django.urls import reverse
from core.models import User, Event, Booking, Ticket, Feedback


class FlowTests(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)

    def login(self, u, p):
        c = Client(); self.assertTrue(c.login(username=u, password=p)); return c

    def test_roles_are_isolated(self):
        att = self.login('attendee1', 'Attendee@123')
        self.assertEqual(att.get(reverse('admin_dashboard')).status_code, 403)
        self.assertEqual(att.get(reverse('org_dashboard')).status_code, 403)
        org = self.login('organiser1', 'Organiser@123')
        self.assertEqual(org.get(reverse('my_tickets')).status_code, 403)
        self.assertEqual(org.get(reverse('admin_dashboard')).status_code, 403)
        adm = self.login('admin', 'Admin@12345')
        self.assertEqual(adm.get(reverse('admin_dashboard')).status_code, 200)
        self.assertEqual(Client().get(reverse('my_tickets')).status_code, 302)

    def test_full_flow(self):
        att = self.login('attendee1', 'Attendee@123'); org = self.login('organiser1', 'Organiser@123')
        e = Event.objects.get(title__startswith='AI & Django')
        vip = e.ticket_types.get(kind='VIP')
        r = att.post(reverse('book', args=[e.pk]), {'ticket_type': vip.pk, 'quantity': 2})
        b = Booking.objects.get(); self.assertEqual(b.status, 'PENDING'); self.assertEqual(float(b.amount), 1998)
        self.assertEqual(att.get(reverse('checkout', args=[b.pk])).status_code, 200)
        att.post(reverse('demo_pay', args=[b.pk])); b.refresh_from_db()
        self.assertEqual(b.status, 'PAID'); t = Ticket.objects.first(); self.assertEqual(Ticket.objects.count(), 2)
        self.assertEqual(att.get(reverse('ticket_qr', args=[t.code])).status_code, 200)
        self.assertEqual(att.get(reverse('my_tickets')).status_code, 200)
        import json
        ok = org.post(reverse('scan_api'), json.dumps({'code': 'EVS:' + t.code}), content_type='application/json').json()
        self.assertEqual(ok['status'], 'ok')
        dup = org.post(reverse('scan_api'), json.dumps({'code': t.code}), content_type='application/json').json()
        self.assertEqual(dup['status'], 'duplicate')
        bad = org.post(reverse('scan_api'), json.dumps({'code': 'fake'}), content_type='application/json').json()
        self.assertEqual(bad['status'], 'invalid')
        self.assertEqual(att.get(reverse('certificate', args=[t.code])).status_code, 200)
        att.post(reverse('feedback', args=[e.pk]), {'rating': 5, 'comment': 'Great workshop'})
        for k in ('plan', 'content', 'feedback'):
            self.assertEqual(org.post(reverse('event_ai', args=[e.pk, k])).status_code, 302)
        self.assertEqual(org.get(reverse('event_manage', args=[e.pk])).status_code, 200)
        self.assertEqual(org.get(reverse('attendee_export', args=[e.pk])).status_code, 200)
        r = att.post(reverse('chat_api'), json.dumps({'question': 'Where is parking?', 'event_id': e.pk}), content_type='application/json')
        self.assertIn('Parking', r.json()['answer'])
        for n in ('admin_dashboard', 'admin_organisers', 'admin_categories', 'admin_payments', 'admin_report_csv'):
            self.assertEqual(self.login('admin', 'Admin@12345').get(reverse(n)).status_code, 200)

    def test_register_otp_and_approval(self):
        c = Client()
        r = c.post(reverse('register'), {'username': 'neworg', 'email': 'n@x.com', 'phone': '9876543210', 'role': 'ORGANISER',
                                         'organisation': 'ABC Club', 'password1': 'Str0ng!Pass99', 'password2': 'Str0ng!Pass99'})
        self.assertRedirects(r, reverse('verify_otp'))
        from core.models import OTP
        code = OTP.objects.latest('id').code
        c.post(reverse('verify_otp'), {'otp': code})
        u = User.objects.get(username='neworg'); self.assertTrue(u.is_verified); self.assertFalse(u.is_approved)
        r = c.post(reverse('login'), {'username': 'neworg', 'password': 'Str0ng!Pass99'})
        self.assertNotIn('_auth_user_id', c.session)
        adm = self.login('admin', 'Admin@12345'); adm.post(reverse('admin_organiser_action', args=[u.pk, 'approve']))
        c.post(reverse('login'), {'username': 'neworg', 'password': 'Str0ng!Pass99'})
        self.assertIn('_auth_user_id', c.session)

    def test_organiser_cannot_touch_others_event(self):
        other = User.objects.create_user('org2', password='Org2@12345', role='ORGANISER', is_verified=True, is_approved=True)
        c = self.login('org2', 'Org2@12345'); e = Event.objects.first()
        self.assertEqual(c.get(reverse('event_manage', args=[e.pk])).status_code, 404)


class CsrfLoginTest(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)

    def test_login_with_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        r = c.get(reverse('login')); self.assertEqual(r.status_code, 200)
        tok = c.cookies['eventsphere_csrftoken'].value
        r = c.post(reverse('login'), {'username': 'attendee1', 'password': 'Attendee@123', 'csrfmiddlewaretoken': tok})
        self.assertEqual(r.status_code, 302)
        r = Client(enforce_csrf_checks=True).post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertEqual(r.status_code, 403); self.assertContains(r, 'session expired', status_code=403)


class SOSAndExtrasTests(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)
        self.att = Client(); self.att.login(username='attendee1', password='Attendee@123')
        self.org = Client(); self.org.login(username='organiser1', password='Organiser@123')
        self.adm = Client(); self.adm.login(username='admin', password='Admin@12345')
        self.e = Event.objects.get(title__startswith='AI & Django')

    def _buy(self):
        from core.models import Coupon
        Coupon.objects.create(event=self.e, code='HALF', percent=50)
        self.att.post(reverse('book', args=[self.e.pk]), {'ticket_type': self.e.ticket_types.get(kind='VIP').pk, 'quantity': 1, 'coupon': 'half'})
        b = Booking.objects.get(); self.att.post(reverse('demo_pay', args=[b.pk])); b.refresh_from_db(); return b

    def test_coupon_discount(self):
        b = self._buy(); self.assertEqual(float(b.amount), 499.5); self.assertEqual(b.status, 'PAID')
        from core.models import Coupon; self.assertEqual(Coupon.objects.get().used, 1)

    def test_sos_flow(self):
        import json
        send = lambda c, d: c.post(reverse('sos_send'), json.dumps(d), content_type='application/json')
        # no ticket -> refused
        self.assertEqual(send(self.att, {'event_id': self.e.pk, 'category': 'MEDICAL'}).status_code, 403)
        self._buy()
        r = send(self.att, {'event_id': self.e.pk, 'category': 'MEDICAL', 'message': 'Fainted', 'location_text': 'Gate 2', 'lat': 10.78, 'lng': 76.65}).json()
        self.assertTrue(r['ok']); aid = r['alert']['id']
        self.assertTrue(send(self.att, {'event_id': self.e.pk, 'category': 'FIRE'}).json()['duplicate'])
        self.assertEqual(len(self.org.get(reverse('sos_feed')).json()['alerts']), 1)
        self.assertEqual(len(self.adm.get(reverse('sos_feed')).json()['alerts']), 1)
        self.assertEqual(self.att.get(reverse('sos_feed')).status_code, 403)
        self.assertEqual(self.org.get(reverse('sos_control')).status_code, 200)
        other = User.objects.create_user('org2', password='Org2@12345', role='ORGANISER', is_verified=True, is_approved=True)
        o2 = Client(); o2.login(username='org2', password='Org2@12345')
        self.assertEqual(len(o2.get(reverse('sos_feed')).json()['alerts']), 0)
        self.assertEqual(o2.post(reverse('sos_action', args=[aid, 'ack']), '{}', content_type='application/json').status_code, 404)
        act = lambda c, a: c.post(reverse('sos_action', args=[aid, a]), json.dumps({'note': 'ok'}), content_type='application/json').json()
        self.assertEqual(act(self.org, 'ack')['alert']['status'], 'ACK')
        self.assertTrue(act(self.org, 'escalate')['alert']['escalated'])
        self.assertEqual(self.att.get(reverse('sos_mine')).json()['alerts'][0]['status'], 'ACK')
        self.assertEqual(act(self.adm, 'resolve')['alert']['status'], 'RESOLVED')
        self.assertEqual(len(self.org.get(reverse('sos_feed')).json()['alerts']), 0)

    def test_pages_render(self):
        self._buy(); t = Ticket.objects.first()
        for c, names in ((self.att, [('home', []), ('sos_page', []), ('my_tickets', []), ('ticket_detail', [t.code]), ('ticket_ics', [t.code]), ('event_detail', [self.e.pk])]),
                         (self.org, [('event_manage', [self.e.pk]), ('org_dashboard', [])]), (self.adm, [('admin_dashboard', [])])):
            for n, a in names:
                self.assertEqual(c.get(reverse(n, args=a)).status_code, 200, n)
        self.assertEqual(self.org.post(reverse('event_ai', args=[self.e.pk, 'demand'])).status_code, 302)
        self.assertEqual(self.org.post(reverse('announce', args=[self.e.pk]), {'message': 'Hall changed'}).status_code, 302)
        self.assertContains(self.att.get(reverse('event_detail', args=[self.e.pk])), 'Hall changed')


class SeatingFeedbackAssistantAnalyticsTests(TestCase):
    """New in v3: reserved seating, booking-help chatbot, feedback page, search/browse analytics."""

    def setUp(self):
        call_command('seed_demo', verbosity=0)
        mk = lambda u, p: (lambda c: (c.login(username=u, password=p), c)[1])(Client())
        self.att, self.org, self.adm = mk('attendee1', 'Attendee@123'), mk('organiser1', 'Organiser@123'), mk('admin', 'Admin@12345')
        self.seated = Event.objects.get(title__startswith='Monsoon')        # reserved seating (500 seats)
        self.common = Event.objects.get(title__startswith='AI & Django')    # open entry
        self.reg = self.seated.ticket_types.get(kind='REGULAR')

    # ---------- seat selection ----------
    def test_seat_map_only_for_seated_events(self):
        self.assertTrue(self.seated.seating_enabled); self.assertFalse(self.common.seating_enabled)
        r = self.att.get(reverse('seat_select', args=[self.seated.pk]), {'ticket_type': self.reg.pk, 'quantity': 2})
        self.assertEqual(r.status_code, 200); self.assertContains(r, 'data-seat="A1"'); self.assertContains(r, 'STAGE')
        r = self.att.get(reverse('seat_select', args=[self.common.pk]), {'ticket_type': self.common.ticket_types.first().pk, 'quantity': 1})
        self.assertRedirects(r, reverse('event_detail', args=[self.common.pk]))
        self.assertContains(self.att.get(reverse('event_detail', args=[self.seated.pk])), 'Choose seats')
        self.assertContains(self.att.get(reverse('event_detail', args=[self.common.pk])), 'Book now')

    def test_pick_pay_and_seat_conflicts(self):
        book = lambda c, seats, qty=2: c.post(reverse('book', args=[self.seated.pk]),
                                              {'ticket_type': self.reg.pk, 'quantity': qty, 'seats': seats})
        # wrong number of seats / invalid seat / duplicate seat are refused
        for bad in ('A1', 'A1,A2,A3', 'Z99,A1', 'A1,A1'):
            self.assertEqual(book(self.att, bad).status_code, 302); self.assertEqual(Booking.objects.count(), 0, bad)
        r = book(self.att, 'A2,A1'); b = Booking.objects.get(); self.assertEqual(b.seats, 'A1,A2')
        self.assertRedirects(r, reverse('checkout', args=[b.pk]))
        # held seats can't be taken by someone else
        other = User.objects.create_user('att2', password='Att2@12345', role='ATTENDEE', is_verified=True, email='a2@x.com', phone='9999999999')
        c2 = Client(); c2.login(username='att2', password='Att2@12345')
        book(c2, 'A2,A3'); self.assertEqual(Booking.objects.count(), 1)
        self.assertContains(c2.get(reverse('seat_select', args=[self.seated.pk]), {'ticket_type': self.reg.pk, 'quantity': 1}), 'disabled title="A1 - booked"')
        # pay -> tickets carry the seat labels
        self.att.post(reverse('demo_pay', args=[b.pk])); b.refresh_from_db(); self.assertEqual(b.status, 'PAID')
        self.assertEqual(sorted(Ticket.objects.values_list('seat_label', flat=True)), ['A1', 'A2'])
        self.assertContains(self.att.get(reverse('my_tickets')), 'Seat A1')
        t = Ticket.objects.get(seat_label='A1'); self.assertContains(self.att.get(reverse('ticket_detail', args=[t.code])), 'Seat A1')
        # paid seats are gone for good; free seats can be booked
        book(c2, 'A1,A5'); self.assertEqual(Booking.objects.count(), 1)
        book(c2, 'A5,A6'); self.assertEqual(Booking.objects.count(), 2)
        # expired hold is released
        Booking.objects.filter(attendee=other).update(created_at=timezone_now_minus(11))
        self.assertNotIn('A5', self.seated.taken_seats())
        # scanner shows the seat, CSV has the seat column
        import json
        j = self.org.post(reverse('scan_api'), json.dumps({'code': t.code}), content_type='application/json').json()
        self.assertEqual(j['seat'], 'A1')
        self.assertIn('Seat', self.org.get(reverse('attendee_export', args=[self.seated.pk])).content.decode().splitlines()[0])

    def test_seated_event_requires_seats_and_common_event_unchanged(self):
        r = self.att.post(reverse('book', args=[self.seated.pk]), {'ticket_type': self.reg.pk, 'quantity': 1})
        self.assertIn(reverse('seat_select', args=[self.seated.pk]), r['Location']); self.assertEqual(Booking.objects.count(), 0)
        self.att.post(reverse('book', args=[self.common.pk]), {'ticket_type': self.common.ticket_types.get(kind='VIP').pk, 'quantity': 2})
        b = Booking.objects.get(); self.assertEqual(b.seats, '')
        self.att.post(reverse('demo_pay', args=[b.pk])); self.assertEqual(list(Ticket.objects.values_list('seat_label', flat=True)), ['', ''])

    def test_seat_plan_layout_and_organiser_form(self):
        e = Event(seat_limit=25, seats_per_row=10)
        self.assertEqual([(r, len(s)) for r, s in e.seat_rows()], [('A', 10), ('B', 10), ('C', 5)])
        self.assertEqual(Event._row_name(26), 'AA')
        data = {'title': 'Seated gig', 'category': self.seated.category_id, 'description': 'd', 'venue': 'Hall', 'city': 'Kochi',
                'start': '2030-01-01T10:00', 'end': '2030-01-01T12:00', 'seat_limit': 600, 'seating_enabled': 'on', 'seats_per_row': 10}
        r = self.org.post(reverse('event_create'), data); self.assertContains(r, 'up to 500 seats')
        data['seat_limit'] = 40; self.assertEqual(self.org.post(reverse('event_create'), data).status_code, 302)
        self.assertTrue(Event.objects.get(title='Seated gig').seating_enabled)

    # ---------- feedback ----------
    def test_feedback_after_booking(self):
        self.assertContains(self.att.get(reverse('my_feedback')), 'No bookings yet')
        self.att.post(reverse('book', args=[self.common.pk]), {'ticket_type': self.common.ticket_types.get(kind='VIP').pk, 'quantity': 1})
        b = Booking.objects.get(); self.att.post(reverse('demo_pay', args=[b.pk]))   # NOT checked in - still allowed
        self.assertContains(self.att.get(reverse('my_feedback')), 'AI &amp; Django Workshop')
        r = self.att.post(reverse('feedback', args=[self.common.pk]), {'rating': 4, 'comment': 'Smooth booking', 'next': reverse('my_feedback')})
        self.assertRedirects(r, reverse('my_feedback')); self.assertEqual(Feedback.objects.get().rating, 4)
        self.att.post(reverse('feedback', args=[self.common.pk]), {'rating': 5, 'comment': 'Even better'}); self.assertEqual(Feedback.objects.get().rating, 5)
        self.assertContains(self.att.get(reverse('event_detail', args=[self.common.pk])), 'Even better')
        self.att.post(reverse('feedback', args=[self.common.pk]), {'rating': 9}); self.assertEqual(Feedback.objects.get().rating, 5)
        self.att.post(reverse('feedback', args=[self.seated.pk]), {'rating': 3})   # never booked this one
        self.assertEqual(Feedback.objects.count(), 1)
        self.assertEqual(self.org.get(reverse('my_feedback')).status_code, 403)
        # open redirect is refused
        r = self.att.post(reverse('feedback', args=[self.common.pk]), {'rating': 5, 'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('event_detail', args=[self.common.pk]))

    # ---------- chatbot ----------
    def test_booking_assistant(self):
        import json
        ask = lambda m, **kw: self.att.post(reverse('assistant_api'), json.dumps({'message': m, **kw}), content_type='application/json')
        self.assertIn('Booking takes under a minute', ask('how do i book a ticket').json()['answer'])
        j = ask('How does seat selection work for Monsoon Melodies?').json()
        self.assertIn('reserved seating', j['answer']); self.assertTrue(j['links'])
        self.assertIn('common (open) entry', ask('can i choose a seat for the AI Django workshop').json()['answer'])
        self.assertIn('no paid bookings', ask('show my bookings').json()['answer'])
        self.att.post(reverse('book', args=[self.seated.pk]), {'ticket_type': self.reg.pk, 'quantity': 1, 'seats': 'C3'})
        a = ask('payment help').json(); self.assertTrue(any('checkout' in l['url'] for l in a['links']))
        b = Booking.objects.get(); self.att.post(reverse('demo_pay', args=[b.pk]))
        self.assertIn('seats C3', ask('show my bookings').json()['answer'])
        self.assertIn('₹199', ask('price of startup summit').json()['answer'])
        self.assertTrue(ask('separate').json()['chips'])                       # 'rate' must not match inside 'separate'
        self.assertNotIn('stars', ask('separate').json()['answer'])
        self.assertEqual(ask('').status_code, 400)
        self.assertEqual(self.org.post(reverse('assistant_api'), '{}', content_type='application/json').status_code, 403)
        self.assertEqual(Client().post(reverse('assistant_api'), '{}', content_type='application/json').status_code, 302)
        self.assertContains(self.att.get(reverse('home')), 'botPanel'); self.assertNotContains(self.org.get(reverse('org_dashboard')), 'botPanel')

    # ---------- analytics ----------
    def test_search_and_browse_analytics(self):
        self.att.get(reverse('home'), {'q': 'monsoon'}); self.att.get(reverse('home'), {'q': 'zzzz-nothing'})
        self.att.get(reverse('home'), {'category': self.seated.category_id}); Client().get(reverse('home'), {'q': 'Monsoon'})
        self.adm.get(reverse('home'), {'q': 'admin-search'})            # admin activity is not counted
        from core.models import SearchLog, EventView
        self.assertEqual(SearchLog.objects.count(), 4); self.assertEqual(SearchLog.objects.filter(results=0).count(), 1)
        self.att.get(reverse('event_detail', args=[self.seated.pk])); self.att.get(reverse('event_detail', args=[self.seated.pk]))  # deduped
        Client().get(reverse('event_detail', args=[self.common.pk])); self.org.get(reverse('event_detail', args=[self.seated.pk])); self.adm.get(reverse('event_detail', args=[self.seated.pk]))
        self.assertEqual(EventView.objects.count(), 2)
        r = self.adm.get(reverse('admin_analytics')); self.assertEqual(r.status_code, 200)
        k = r.context['kpi']; self.assertEqual((k['views'], k['searches'], k['no_result'], k['guest_views']), (2, 4, 1, 1))
        self.assertEqual(k['top_keyword'], 'monsoon'); self.assertEqual(r.context['chart']['by_cat']['labels'].count('Concert'), 1)
        for d in ('7', '30', '90', 'bad'):
            self.assertEqual(self.adm.get(reverse('admin_analytics'), {'days': d}).status_code, 200)
        self.assertEqual(self.att.get(reverse('admin_analytics')).status_code, 403)
        self.assertEqual(self.org.get(reverse('admin_analytics')).status_code, 403)
        self.assertContains(self.adm.get(reverse('admin_dashboard')), 'analytics')


def timezone_now_minus(minutes):
    from datetime import timedelta
    from django.utils import timezone
    return timezone.now() - timedelta(minutes=minutes)
