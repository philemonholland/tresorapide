from __future__ import annotations

from accounts.access import user_has_minimum_role
from accounts.models import User


PREFER_DESKTOP_SITE_SESSION_KEY = "prefer_desktop_site"

_HANDHELD_USER_AGENT_MARKERS = (
    "android",
    "iphone",
    "ipad",
    "ipod",
    "mobile",
    "windows phone",
    "blackberry",
    "opera mini",
)


def is_handheld_user_agent(user_agent: str | None) -> bool:
    normalized = str(user_agent or "").lower()
    return any(marker in normalized for marker in _HANDHELD_USER_AGENT_MARKERS)


def apply_site_mode_preference(request) -> None:
    site_mode = str(request.GET.get("site_mode") or "").strip().lower()
    if site_mode == "desktop":
        request.session[PREFER_DESKTOP_SITE_SESSION_KEY] = True
    elif site_mode == "mobile":
        request.session.pop(PREFER_DESKTOP_SITE_SESSION_KEY, None)


def handheld_capture_enabled_for_user(user) -> bool:
    return user_has_minimum_role(user, User.Role.TREASURER) and bool(
        getattr(user, "house_id", None)
    )


def handheld_capture_enabled_for_request(request) -> bool:
    if request.session.get(PREFER_DESKTOP_SITE_SESSION_KEY):
        return False
    if not handheld_capture_enabled_for_user(request.user):
        return False
    return is_handheld_user_agent(request.META.get("HTTP_USER_AGENT"))
