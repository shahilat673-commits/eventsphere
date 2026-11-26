from io import BytesIO
import qrcode

PREFIX = 'EVS:'


def qr_png(code):
    img = qrcode.make(PREFIX + code, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def parse(raw):
    raw = (raw or '').strip()
    return raw[len(PREFIX):] if raw.startswith(PREFIX) else raw
