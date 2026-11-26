"""Usage:  python manage.py test_email            (sends a test mail to yourself)
          python manage.py test_email friend@gmail.com
Tells you exactly why an email is not going out."""
import smtplib
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Send a test email and explain any error in plain words.'

    def add_arguments(self, parser):
        parser.add_argument('to', nargs='?', default=None)

    def handle(self, *args, **opts):
        to = opts['to'] or settings.EMAIL_HOST_USER or settings.EMAIL_FROM
        self.stdout.write(f'EMAIL_CONFIGURED : {settings.EMAIL_CONFIGURED}')
        self.stdout.write(f'Backend          : {settings.EMAIL_BACKEND}')
        if not settings.EMAIL_CONFIGURED:
            self.stdout.write(self.style.ERROR(
                'EMAIL_HOST, EMAIL_HOST_USER or EMAIL_HOST_PASSWORD is empty in .env (or .env is not next to manage.py).'))
            return
        if settings.BREVO_API_KEY:
            from core.services import notify
            to = opts['to'] or settings.EMAIL_FROM
            self.stdout.write(f'Mode             : Brevo web API (HTTPS)  from {settings.EMAIL_FROM}')
            if notify.send_email(to, 'EventSphere AI - test email', 'If you can read this, OTP emails will work.'):
                self.stdout.write(self.style.SUCCESS(f'SENT OK -> check the inbox (and Spam) of {to}'))
            else:
                self.stdout.write(self.style.ERROR(f'FAILED: {notify.last_error}\n'
                    '  -> 401 = wrong API key | 400 with "sender" = EMAIL_FROM is not a verified sender in Brevo (Senders & IP -> Senders)'))
            return
        user = settings.EMAIL_HOST_USER
        self.stdout.write(f'Host / port      : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}')
        self.stdout.write(f'Login user       : {user}')
        self.stdout.write(f'Password length  : {len(settings.EMAIL_HOST_PASSWORD)}  (Gmail app password must be 16)')
        if settings.EMAIL_HOST == 'smtp.gmail.com' and not user.lower().endswith('@gmail.com'):
            self.stdout.write(self.style.ERROR('Login user must end with @gmail.com - check for a typo (e.g. gmai.com).'))
        if len(settings.EMAIL_HOST_PASSWORD) != 16 and settings.EMAIL_HOST == 'smtp.gmail.com':
            self.stdout.write(self.style.WARNING('Password is not 16 characters - use a Gmail App Password, not your normal password.'))
        try:
            send_mail('EventSphere AI - test email', 'If you can read this, OTP emails will work.',
                      settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
        except smtplib.SMTPAuthenticationError as e:
            self.stdout.write(self.style.ERROR(
                f'LOGIN REJECTED by Gmail ({e.smtp_code}).\n'
                '  -> The app password does not belong to this Gmail address, or it was revoked.\n'
                '  -> Turn on 2-Step Verification, create a NEW app password at https://myaccount.google.com/apppasswords\n'
                '     while logged in as EXACTLY the address in EMAIL_HOST_USER, paste it in .env, restart.'))
        except smtplib.SMTPServerDisconnected:
            self.stdout.write(self.style.ERROR(
                'CONNECTION CLOSED by the server.\n'
                '  -> Usually a wrong/mistyped Gmail address, or a firewall/antivirus blocking port 587.\n'
                '  -> Try a mobile hotspot, and re-check EMAIL_HOST_USER spelling.'))
        except (OSError, smtplib.SMTPConnectError) as e:
            self.stdout.write(self.style.ERROR(
                f'CANNOT REACH {settings.EMAIL_HOST}:{settings.EMAIL_PORT} ({e}).\n'
                '  -> No internet, or the network (college/office Wi-Fi) blocks SMTP. Try a mobile hotspot.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'{type(e).__name__}: {e}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'SENT OK -> check the inbox (and Spam) of {to}'))