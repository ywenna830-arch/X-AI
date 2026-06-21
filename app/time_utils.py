from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context


DEFAULT_TIMEZONE = "Asia/Shanghai"


def app_timezone_name():
    if has_app_context():
        return current_app.config.get("APP_TIMEZONE", DEFAULT_TIMEZONE)
    return DEFAULT_TIMEZONE


def app_timezone(tz_name=None):
    name = tz_name or app_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == DEFAULT_TIMEZONE:
            return timezone(timedelta(hours=8), name)
        return timezone.utc


def app_now():
    return datetime.now(app_timezone()).replace(tzinfo=None)


def app_aware_now():
    return datetime.now(app_timezone())


def to_app_time(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(app_timezone()).replace(tzinfo=None)
