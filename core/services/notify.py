"""Email + SMS notifications. Every message is stored in NotificationLog (handy for demo/viva)."""
import logging
import requests
from django.conf import settings
from django.core.mail import send_mail
from core.models import NotificationLog

log = logging.getLogger(__name__)
last_error = ''          # reason of the most recent failed email (shown on the OTP page in DEBUG mode)


def _send_via_brevo(to, subject, body, html):
    """Brevo transactional email over HTTPS (port 443) - works even when SMTP ports are blocked."""
    r = requests.post('https://api.brevo.com/v3/smtp/email', timeout=20,
                      headers={'api-key': settings.BREVO_API_KEY, 'accept': 'application/json', 'content-type': 'application/json'},
                      json={'sender': {'name': 'EventSphere AI', 'email': settings.EMAIL_FROM}, 'to': [{'email': to}],
                            'subject': subject, 'textContent': body, 'htmlContent': html or f'<p>{body}</p>'})
    if r.status_code >= 300:
        raise RuntimeError(f'Brevo API {r.status_code}: {r.text[:200]}')


def send_email(to, subject, body, html=None):
    """Send an email. Returns True if the mail server accepted it, False otherwise."""
    global last_error
    last_error = ''
    if not to:
        last_error = 'no recipient email address'
        return False
    NotificationLog.objects.create(channel='EMAIL', recipient=to, subject=subject, message=body)
    try:
        if settings.BREVO_API_KEY:
            _send_via_brevo(to, subject, body, html)
        else:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False, html_message=html)
        return True
    except Exception as exc:
        last_error = f'{type(exc).__name__}: {exc}'
        log.warning('email failed: %s', last_error)
        print(f'[EMAIL ERROR -> {to}] {last_error}')      # shows the real reason in the VS Code terminal
        return False


def send_sms(phone, message):
    if not phone:
        return
    NotificationLog.objects.create(channel='SMS', recipient=phone, message=message)
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        try:
            requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json',
                          auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN), timeout=10,
                          data={'From': settings.TWILIO_FROM, 'To': phone, 'Body': message})
        except Exception as exc:
            log.warning('sms failed: %s', exc)
    else:
        print(f'[DEMO SMS to {phone}] {message}')