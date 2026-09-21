"""Kotlin / native Android primary-stack gate for the Android resume profile.

Keeps Android/Kotlin/KMP roles. Rejects jobs whose *primary* stack is
Flutter, React Native, React, iOS/Swift, Xamarin, MAUI, Ionic, or Cordova.

Secondary mentions ("React knowledge is a plus") on an Android/Kotlin job
do NOT disqualify.
"""

from __future__ import annotations

import re
from typing import Optional

# Title-primary roles we never treat as Android-profile matches.
_EXCLUDED_PRIMARY_TITLE = re.compile(
    r"(?i)\b(?:"
    r"flutter|"
    r"react\s*native|"
    r"react\.?js|"
    r"frontend\s+react|"
    r"react|"
    r"ios|"
    r"swiftui|"
    r"swift|"
    r"objective[\s-]?c|"
    r"xamarin|"
    r"(?:\.?\s*net\s*)?maui|"
    r"ionic|"
    r"cordova|"
    r"dart"
    r")\b"
    r"[\w\s/&+.-]{0,40}?"
    r"\b(?:developer|engineer|programmer|architect)\b"
)

# Explicit primary-title patterns (even without trailing "Developer").
_EXCLUDED_TITLE_ONLY = re.compile(
    r"(?i)^\s*(?:senior|junior|lead|staff|principal|mid[\s-]?level)?\s*"
    r"(?:"
    r"flutter|react\s*native|react\.?js|ios|swift(?:ui)?|"
    r"xamarin|(?:\.?\s*net\s*)?maui|ionic|cordova|dart"
    r")\b"
)

# Native Android / Kotlin / KMP signals (title preferred).
_ANDROID_TITLE_SIGNALS = re.compile(
    r"(?i)\b(?:"
    r"android|"
    r"kotlin|"
    r"jetpack|"
    r"compose|"
    r"kmp|"
    r"kmm|"
    r"kotlin\s+multiplatform|"
    r"android\s+sdk"
    r")\b"
)

_KMP_SIGNALS = re.compile(
    r"(?i)\b(?:"
    r"kmp|kmm|kotlin\s+multiplatform|multiplatform\s+mobile"
    r")\b"
)

_ANDROID_BODY_SIGNALS = re.compile(
    r"(?i)\b(?:"
    r"android\s+sdk|jetpack\s+compose|jetpack|"
    r"kotlin\s+coroutines|kotlin\s+flow|\broom\b|\bretrofit\b|"
    r"\bhilt\b|\bdagger\b|mvvm|mvi|clean\s+architecture|"
    r"android\s+studio|native\s+android"
    r")\b"
)

_EXCLUDED_BODY_PRIMARY = re.compile(
    r"(?i)\b(?:"
    r"flutter|react\s*native|dart\s+developer|swiftui|"
    r"xamarin|ionic\s+framework|cordova"
    r")\b"
)


def _norm(title: str, description: str = "") -> tuple[str, str]:
    t = (title or "").strip()
    d = (description or "")[:4000]
    return t, d


def has_android_kotlin_title_signal(title: str) -> bool:
    return bool(_ANDROID_TITLE_SIGNALS.search(title or ""))


# "Backend Developer (Kotlin)" is JVM/server — not the Android resume profile.
_BACKEND_PRIMARY_TITLE = re.compile(
    r"\bbackend\b[\w\s/&+.\-]{0,40}\b(?:developer|engineer|programmer|architect)\b|"
    r"\b(?:developer|engineer|programmer|architect)\b[\w\s/&+.\-]{0,40}\bbackend\b|"
    r"\b(?:back[\s\-]?end)\b[\w\s/&+.\-]{0,40}\b(?:developer|engineer)\b",
    re.I,
)

_EXPLICIT_ANDROID_TITLE = re.compile(
    r"\b(?:android|jetpack|compose|kmp|kmm|kotlin\s+multiplatform)\b",
    re.I,
)


def is_backend_primary_title(title: str) -> bool:
    """True when the title is a backend/server role (Kotlin/JVM OK) without Android UI stack."""
    t = title or ""
    if not _BACKEND_PRIMARY_TITLE.search(t):
        return False
    # "Android Backend" / "Kotlin Android Backend" stays Android-adjacent.
    if _EXPLICIT_ANDROID_TITLE.search(t):
        return False
    return True


def is_excluded_cross_platform_primary(title: str, description: str = "") -> bool:
    """True when the job's *primary* role is a non-Android mobile/web stack."""
    title, desc = _norm(title, description)
    title_l = title.lower()

    # Android/Kotlin/KMP in the title wins — even if Flutter/RN is "a plus" in JD.
    if has_android_kotlin_title_signal(title):
        return False

    if _EXCLUDED_PRIMARY_TITLE.search(title) or _EXCLUDED_TITLE_ONLY.search(title):
        return True

    # Generic "Mobile Developer" whose body is clearly Flutter/RN-primary.
    if re.search(r"(?i)\bmobile\b", title_l) and not has_android_kotlin_title_signal(title):
        body = f"{title} {desc}".lower()
        excluded_hits = len(_EXCLUDED_BODY_PRIMARY.findall(body))
        android_hits = len(_ANDROID_TITLE_SIGNALS.findall(body)) + len(
            _ANDROID_BODY_SIGNALS.findall(body)
        )
        if excluded_hits >= 2 and android_hits == 0:
            return True
        if excluded_hits >= 1 and android_hits == 0 and re.search(
            r"(?i)\b(?:flutter|react\s*native)\s+(?:developer|engineer|experience)\b",
            body,
        ):
            return True

    return False


def is_kotlin_android_role(title: str, description: str = "") -> bool:
    """True if this job is a valid match for the Android (Kotlin/native/KMP) profile."""
    title, desc = _norm(title, description)
    if not title:
        return False

    if is_excluded_cross_platform_primary(title, desc):
        return False

    # Backend/server Kotlin (no Android/Jetpack/KMP in title) → Backend profile, not Android.
    if is_backend_primary_title(title):
        return False

    blob = f"{title} {desc}"

    # Title-level Android/Kotlin/KMP is enough.
    if has_android_kotlin_title_signal(title):
        return True

    # KMP/KMM anywhere with Kotlin/Android relevance.
    if _KMP_SIGNALS.search(blob) and (
        re.search(r"(?i)\bkotlin\b", blob) or re.search(r"(?i)\bandroid\b", blob)
    ):
        return True

    # Description-heavy native Android (no excluded primary title).
    if _ANDROID_BODY_SIGNALS.search(desc) and re.search(r"(?i)\b(?:android|kotlin)\b", blob):
        return True

    # Bare Kotlin developer title (often Android-track in India hiring).
    if re.search(r"(?i)\bkotlin\b", title) and re.search(
        r"(?i)\b(?:developer|engineer|programmer)\b", title
    ):
        return True

    return False


def android_mismatch_reason(title: str, description: str = "") -> Optional[str]:
    """Human-readable reject reason, or None if allowed."""
    if is_kotlin_android_role(title, description):
        return None
    if is_excluded_cross_platform_primary(title, description):
        return "primary stack is Flutter/RN/React/iOS/other non-Android"
    return "no Kotlin/Android/KMP primary-stack signal"
