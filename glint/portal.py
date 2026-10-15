"""Minimal xdg-desktop-portal client built on Gio.

All portal traffic goes over a private session-bus connection so we can
register our application ID with org.freedesktop.host.portal.Registry before
any other portal call is made on it (GTK may already have talked to the
Settings portal over the shared connection).
"""

import itertools
import os

from gi.repository import Gio, GLib

from . import APP_ID

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"

_counter = itertools.count(1)
_connection = None


def connection():
    global _connection
    if _connection is None:
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        _connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        try:
            _connection.call_sync(BUS_NAME, OBJECT_PATH, "org.freedesktop.host.portal.Registry", "Register",
                                  GLib.Variant("(sa{sv})", (APP_ID, {})), None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as exc:
            # Older portals lack the registry; sandboxed/systemd-scoped launches
            # already have an identity. Neither is fatal.
            if os.environ.get("GLINT_DEBUG"):
                print(f"glint: portal registry: {exc.message}")
    return _connection


def new_token(prefix="glint"):
    return f"{prefix}{os.getpid()}_{next(_counter)}"


def _request_path(conn, token):
    sender = conn.get_unique_name().lstrip(":").replace(".", "_")
    return f"{OBJECT_PATH}/request/{sender}/{token}"


def get_version(interface):
    try:
        res = connection().call_sync(BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                                     GLib.Variant("(ss)", (interface, "version")), None,
                                     Gio.DBusCallFlags.NONE, 3000, None)
        return res.unpack()[0]
    except GLib.Error:
        return 0


def request(interface, method, args_format, args, options, callback, fd_list=None):
    """Call a portal method that returns a Request handle.

    `args` excludes the trailing options dict; `options` is a dict of
    GLib.Variant values. callback(response_code, results_dict).
    response: 0 success, 1 cancelled by user, 2 other error.
    """
    conn = connection()
    token = new_token()
    options = dict(options)
    options["handle_token"] = GLib.Variant("s", token)
    path = _request_path(conn, token)
    state = {"sub": 0, "done": False}

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        if state["done"]:
            return
        state["done"] = True
        conn.signal_unsubscribe(state["sub"])
        code, results = params.unpack()
        callback(code, results)

    # Subscribe before calling so a fast Response can't be missed. Sender is
    # left unset: the signal comes from the portal's unique name.
    state["sub"] = conn.signal_subscribe(None, REQUEST_IFACE, "Response", path, None,
                                         Gio.DBusSignalFlags.NONE, on_response)

    params = GLib.Variant(f"({args_format}a{{sv}})", tuple(args) + (options,))

    def on_called(c, res):
        try:
            if fd_list is not None:
                c.call_with_unix_fd_list_finish(res)
            else:
                c.call_finish(res)
        except GLib.Error as exc:
            if not state["done"]:
                state["done"] = True
                conn.signal_unsubscribe(state["sub"])
                print(f"glint: portal {interface}.{method} failed: {exc.message}")
                callback(2, {"error": exc.message})

    if fd_list is not None:
        conn.call_with_unix_fd_list(BUS_NAME, OBJECT_PATH, interface, method, params, None,
                                    Gio.DBusCallFlags.NONE, -1, fd_list, None, on_called)
    else:
        conn.call(BUS_NAME, OBJECT_PATH, interface, method, params, None,
                  Gio.DBusCallFlags.NONE, -1, None, on_called)


def screenshot(callback, interactive=False):
    """callback(path or None)."""
    def done(code, results):
        if code != 0 or "uri" not in results:
            callback(None)
            return
        callback(Gio.File.new_for_uri(results["uri"]).get_path())

    request("org.freedesktop.portal.Screenshot", "Screenshot", "s", ("",),
            {"interactive": GLib.Variant("b", interactive), "modal": GLib.Variant("b", False)}, done)
