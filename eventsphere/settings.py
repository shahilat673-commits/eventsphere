"""EventSphere AI - Django settings (pure Django, full-stack)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# tiny .env loader (no extra package needed)
_envf = BASE_DIR / '.env'
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith('#') and '=' in _l:
            _k, _v = _l.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def env(key, default=''):
    return os.environ.get(key, default)


SECRET_KEY = env('SECRET_KEY', 'dev-only-insecure-key-change-me-in-production-0123456789')
DEBUG = env('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = [h.strip() for h in env('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')]
DEMO_MODE = env('DEMO_MODE', 'True') == 'True'   # shows OTP on screen + demo payment page

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'core',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'eventsphere.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug',
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'core.context.sos',
    ]},
}]
WSGI_APPLICATION = 'eventsphere.wsgi.application'

# ---------- Database: SQLite (default) / MySQL / PostgreSQL ----------
_engine = env('DB_ENGINE', 'sqlite').lower()
if _engine == 'mysql':
    DATABASES = {'default': {'ENGINE': 'django.db.backends.mysql', 'NAME': env('DB_NAME', 'eventsphere'),
                 'USER': env('DB_USER', 'root'), 'PASSWORD': env('DB_PASSWORD'),
                 'HOST': env('DB_HOST', '127.0.0.1'), 'PORT': env('DB_PORT', '3306')}}
elif _engine in ('postgres', 'postgresql'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': env('DB_NAME', 'eventsphere'),
                 'USER': env('DB_USER', 'postgres'), 'PASSWORD': env('DB_PASSWORD'),
                 'HOST': env('DB_HOST', '127.0.0.1'), 'PORT': env('DB_PORT', '5432')}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}

# ---------- Authentication & security ----------
AUTH_USER_MODEL = 'core.User'
LOGIN_URL = 'login'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
# Own cookie names so other Django projects on localhost (same host, any port) can't clash
CSRF_COOKIE_NAME = 'eventsphere_csrftoken'
SESSION_COOKIE_NAME = 'eventsphere_sessionid'
CSRF_FAILURE_VIEW = 'core.views.csrf_failure'
CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:8000', 'http://localhost:8000']
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 6
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------- AI engine ----------
GEMINI_API_KEY = env('GEMINI_API_KEY')
GEMINI_MODEL = env('GEMINI_MODEL', 'gemini-3.5-flash')
GEMINI_FALLBACK_MODELS = env('GEMINI_FALLBACK_MODELS', 'gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash')
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')
ANTHROPIC_MODEL = env('ANTHROPIC_MODEL', 'claude-sonnet-4-5')
OPENAI_API_KEY = env('OPENAI_API_KEY')
OPENAI_MODEL = env('OPENAI_MODEL', 'gpt-4o-mini')

# ---------- Razorpay ----------
RAZORPAY_KEY_ID = env('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = env('RAZORPAY_KEY_SECRET')
PLATFORM_FEE_PERCENT = 10   # commission kept by platform; rest is organiser payout

# ---------- Notifications / Email (OTP mails) ----------
EMAIL_HOST = env('EMAIL_HOST')
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD').replace(' ', '')      # Gmail app passwords are shown with spaces
EMAIL_CONFIGURED = bool(EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD)
if EMAIL_CONFIGURED:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(env('EMAIL_PORT', '587'))
    EMAIL_USE_SSL = env('EMAIL_USE_SSL', 'False') == 'True'            # only for port 465
    EMAIL_USE_TLS = not EMAIL_USE_SSL
    EMAIL_TIMEOUT = 20
    DEFAULT_FROM_EMAIL = f'EventSphere AI <{EMAIL_HOST_USER}>'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'   # prints emails in the terminal
    DEFAULT_FROM_EMAIL = 'EventSphere AI <noreply@eventsphere.local>'
# the OTP is shown on screen ONLY while real email is not set up
SHOW_OTP_ON_SCREEN = DEMO_MODE and not EMAIL_CONFIGURED
TWILIO_ACCOUNT_SID = env('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = env('TWILIO_AUTH_TOKEN')
TWILIO_FROM = env('TWILIO_FROM')


# ---------- Brevo web API (OTP email) ----------
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '').strip()
EMAIL_FROM = (os.environ.get('EMAIL_FROM', '') or os.environ.get('EMAIL_HOST_USER', '')).strip()
EMAIL_CONFIGURED = bool(globals().get('EMAIL_CONFIGURED')) or bool(BREVO_API_KEY and EMAIL_FROM)
SHOW_OTP_ON_SCREEN = DEMO_MODE and not EMAIL_CONFIGURED