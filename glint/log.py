"""Persistent log for bug reports: ~/.cache/glint/glint.log.

Everything the running instance prints (Glint's own messages and Python
tracebacks) is copied there with timestamps, so users can attach it to an
issue without digging through the systemd journal. The file is capped at
~1 MB; the previous one is kept as glint.log.1.
"""

import datetime
import os
import platform
import re
import sys

from . import VERSION, paths

MAX_BYTES = 1_000_000
# Python colours tracebacks when started from a terminal; keep the file plain.
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def log_path():
    return os.path.join(paths.cache_dir(), "glint.log")


def _stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ")


class Tee:
    """File-like object writing to the original stream and the log file,
    prefixing each new log line with a timestamp."""

    def __init__(self, stream, logfile):
        self.stream = stream
        self.logfile = logfile
        self._line_start = True

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError):
                pass
        out = []
        for chunk in ANSI.sub("", text).splitlines(keepends=True):
            if self._line_start:
                out.append(_stamp())
            out.append(chunk)
            self._line_start = chunk.endswith("\n")
        try:
            self.logfile.write("".join(out))
            self.logfile.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        for f in (self.stream, self.logfile):
            try:
                if f is not None:
                    f.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.stream, name)


def rotate(path, max_bytes=MAX_BYTES):
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except OSError:
        pass


def start(path=None):
    """Begin logging this process's stdout/stderr. Returns the log path."""
    path = path or log_path()
    rotate(path)
    logfile = open(path, "a", encoding="utf-8", errors="replace")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    session = os.environ.get("XDG_SESSION_TYPE", "?")
    logfile.write(f"{_stamp()}--- Glint {VERSION} started · {desktop} {session} · "
                  f"Python {platform.python_version()} · pid {os.getpid()}\n")
    logfile.flush()
    sys.stdout = Tee(sys.stdout, logfile)
    sys.stderr = Tee(sys.stderr, logfile)
    return path
