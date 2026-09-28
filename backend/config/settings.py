import os
import json
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_ROOT = Path(os.environ.get("MALL_MEDIA_ROOT", BASE_DIR.parent / "media")).resolve()
if os.environ.get("DJANGO_DEBUG", "1") != "1" and not os.environ.get("MALL_MEDIA_ROOT"):
    raise RuntimeError("MALL_MEDIA_ROOT must point to persistent storage outside local development")
PRODUCT_IMAGE_MAX_BYTES = int(os.environ.get("PRODUCT_IMAGE_MAX_BYTES", str(10 * 1024 * 1024)))
PRODUCT_VIDEO_MAX_BYTES = int(os.environ.get("PRODUCT_VIDEO_MAX_BYTES", str(50 * 1024 * 1024)))
STARTUP_GIF_MAX_BYTES = int(os.environ.get("STARTUP_GIF_MAX_BYTES", str(10 * 1024 * 1024)))
PRODUCT_MEDIA_FFMPEG_BIN = os.environ.get("PRODUCT_MEDIA_FFMPEG_BIN", "ffmpeg")
PRODUCT_MEDIA_ORPHAN_TTL = timedelta(hours=24)
PRODUCT_MEDIA_ORPHAN_USER_QUOTA_BYTES = 200 * 1024 * 1024
PRODUCT_MEDIA_ORPHAN_GLOBAL_QUOTA_BYTES = 1024 * 1024 * 1024
STORAGE_TEMPORARY_TTL = timedelta(hours=24)
# Reserved private storage boundaries; E0 does not build code or export reports.
STORAGE_CODE_MAX_BYTES = int(os.environ.get("STORAGE_CODE_MAX_BYTES", str(200 * 1024 * 1024)))
STORAGE_EXPORT_MAX_BYTES = int(os.environ.get("STORAGE_EXPORT_MAX_BYTES", str(100 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-only-change-before-deploy")
WECHAT_MINI_APP_ID = os.environ.get("WECHAT_MINI_APP_ID", "")
WECHAT_MINI_APP_SECRET = os.environ.get("WECHAT_MINI_APP_SECRET", "")
ORDER_PAYMENT_METHODS_ENABLED = {"WECHAT": False, "OFFLINE": False}
EXCHANGE_ORDER_ENABLED = os.environ.get("EXCHANGE_ORDER_ENABLED", "0") == "1"
# Secrets are deployment-owned files, never a PC-editable policy or tracked data.
WECHAT_PAY = {
    "APP_ID": WECHAT_MINI_APP_ID,
    "MERCHANT_ID": os.environ.get("WECHAT_PAY_MERCHANT_ID", ""),
    "MERCHANT_SERIAL": os.environ.get("WECHAT_PAY_MERCHANT_SERIAL", ""),
    "API_V3_KEY_FILE": os.environ.get("WECHAT_PAY_API_V3_KEY_FILE", ""),
    "PRIVATE_KEY_FILE": os.environ.get("WECHAT_PAY_PRIVATE_KEY_FILE", ""),
    "PLATFORM_KEYS": json.loads(os.environ.get("WECHAT_PAY_PLATFORM_KEYS", "{}")),
    "NOTIFY_URL": os.environ.get("WECHAT_PAY_NOTIFY_URL", ""),
    "TIMEOUT_SECONDS": 8, "REPLAY_WINDOW_SECONDS": 300,
}
# Refund transport is independently disabled until real merchant acceptance.
WECHAT_REFUND_ENABLED = os.environ.get("WECHAT_REFUND_ENABLED", "0") == "1"
WECHAT_REFUND_NOTIFY_URL = os.environ.get("WECHAT_REFUND_NOTIFY_URL", "")
KDNIAO = {"ENABLED": os.environ.get("KDNIAO_ENABLED", "0") == "1",
          "EBUSINESS_ID": os.environ.get("KDNIAO_EBUSINESS_ID", ""),
          "APP_KEY": os.environ.get("KDNIAO_APP_KEY", "")}
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
if not DEBUG and SECRET_KEY == "local-only-change-before-deploy":
    raise RuntimeError("DJANGO_SECRET_KEY must be configured outside local development")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
local_origins = "http://127.0.0.1:5173,http://localhost:5173" if DEBUG else ""
CSRF_TRUSTED_ORIGINS = [value for value in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", local_origins).split(",") if value]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "accounts",
    "catalog",
    "pages",
    "inventory",
    "customers",
    "checkout",
    "orders",
    "fulfillment",
    "payments",
    "aftersales",
    "shipping",
    "benefits",
    "points_exchange",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "catalog.upload_limit.AssetUploadLimitMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "accounts.AdminAccount"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "product_mall_dev"),
        "USER": os.environ.get("POSTGRES_USER", "product_mall_dev"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "15432"),
        "TEST": {"NAME": os.environ.get("POSTGRES_TEST_DB", "test_product_mall_dev")},
    }
}
TEST_RUNNER = "config.test_runner.SeededDiscoverRunner"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
USE_TZ = True
TIME_ZONE = "Asia/Shanghai"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_NAME = os.environ.get("DJANGO_SESSION_COOKIE_NAME", "sessionid")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1800
SESSION_SAVE_EVERY_REQUEST = False
