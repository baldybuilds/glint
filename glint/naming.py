"""Filename templates for saved captures.

Tokens: {date} {time} {year} {month} {day} {hour} {minute} {second}
        {type} {width} {height} {counter}
"""

import datetime
import os
import re

TOKENS = ("date", "time", "year", "month", "day", "hour", "minute", "second",
          "type", "width", "height", "counter")

_INVALID = re.compile(r'[/\\\x00-\x1f]')
_TOKEN = re.compile(r"\{(\w+)\}")


def render(template, now=None, kind="Screenshot", size=None, counter=1):
    now = now or datetime.datetime.now()
    values = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H.%M.%S"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "hour": now.strftime("%H"),
        "minute": now.strftime("%M"),
        "second": now.strftime("%S"),
        "type": kind,
        "width": str(size[0]) if size else "",
        "height": str(size[1]) if size else "",
        "counter": str(counter),
    }

    def sub(match):
        key = match.group(1)
        return values.get(key, match.group(0))

    name = _TOKEN.sub(sub, template or "").strip()
    name = _INVALID.sub("-", name).strip(" .")
    return name or now.strftime("Glint %Y-%m-%d at %H.%M.%S")


def unique_path(directory, base, ext):
    """`directory/base.ext`, or `base (2).ext` etc. if taken."""
    ext = ext.lstrip(".")
    candidate = os.path.join(directory, f"{base}.{ext}")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({n}).{ext}")
        n += 1
    return candidate
