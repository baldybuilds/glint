"""Glint — a screenshot, annotation and screen-recording tool for Linux."""

APP_ID = "app.glint.Glint"
APP_NAME = "Glint"
VERSION = "0.2.0"
URL_SCHEME = "glint"


def debug(*args):
    import os
    import sys
    if os.environ.get("GLINT_DEBUG"):
        print("glint:", *args, file=sys.stderr, flush=True)
