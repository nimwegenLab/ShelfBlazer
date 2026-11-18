"""Small presentation helpers for templates and pages: colour slots, icons, initials, relative times. No state."""
import datetime, re, zlib

from markupsafe import Markup, escape


def hue_of(key):
    """Stable colour slot 1..8 for an option value, a person or a type without a colour setting."""
    return 1 + zlib.crc32(str(key).encode("utf-8")) % 8


def svg_icon(name, cls=""):
    """Flat SVG icon: a Font Awesome Free glyph from the local sprite (static/icons.svg)."""
    return Markup(f'<svg class="ico {escape(cls)}" aria-hidden="true" focusable="false"><use href="/static/icons.svg#{escape(name)}"></use></svg>')


def initials(name):
    words = re.findall(r"\w+", name or "", re.UNICODE)
    if not words:
        return "?"
    return (words[0][0] + words[-1][0]).upper() if len(words) > 1 else words[0][:2].upper()


def time_ago(iso):
    """'just now', '12 min ago', '1 h ago', 'yesterday', '3 days ago', then the date."""
    try:
        then = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    secs = (datetime.datetime.now(then.tzinfo) - then).total_seconds()
    if secs < 60: return "just now"
    if secs < 3600: return f"{int(secs // 60)} min ago"
    if secs < 86400: return f"{int(secs // 3600)} h ago"
    days = (datetime.datetime.now(then.tzinfo).date() - then.date()).days
    return "yesterday" if days == 1 else f"{days} days ago" if days < 7 else then.strftime("%d %b %Y")


def day_label(iso):
    """(day key, label) for an event time: "Today", else the date as the app writes it (11 Nov 2026)."""
    try:
        then = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10], iso[:10]
    today = datetime.datetime.now(then.tzinfo).date()
    return then.date().isoformat(), "Today" if then.date() == today else then.strftime("%d %b %Y")
