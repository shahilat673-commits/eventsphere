"""Razorpay integration. If real keys are not set, a DEMO gateway (same flow, same HMAC signature
check) is used so the project can be demonstrated without a bank account."""
import hashlib
import hmac
import secrets
from django.conf import settings

DEMO_SECRET = 'demo_secret_key'


def is_demo():
    return not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def create_order(booking):
    amount_paise = int(booking.amount * 100)
    if is_demo():
        return 'order_demo_' + secrets.token_hex(6)
    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    order = client.order.create({'amount': amount_paise, 'currency': 'INR', 'receipt': f'booking_{booking.id}'})
    return order['id']


def _sign(order_id, payment_id, secret):
    return hmac.new(secret.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest()


def demo_payment(booking):
    """Simulates what Razorpay Checkout would return after a successful card payment."""
    pid = 'pay_demo_' + secrets.token_hex(6)
    return pid, _sign(booking.order_id, pid, DEMO_SECRET)


def verify(order_id, payment_id, signature):
    if is_demo():
        return hmac.compare_digest(_sign(order_id, payment_id, DEMO_SECRET), signature)
    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    try:
        client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id,
                                                 'razorpay_signature': signature})
        return True
    except Exception:
        return False
