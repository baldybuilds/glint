#!/bin/sh
# Install Glint for the current user (no root needed).
#
#   ./install.sh              install to ~/.local
#   ./install.sh --uninstall  remove it again
#   PREFIX=/opt/glint ./install.sh
set -eu

APP_ID=io.github.baldybuilds.Glint
LEGACY_APP_ID=app.glint.Glint   # before 0.3.0
PREFIX="${PREFIX:-$HOME/.local}"
SRC=$(dirname "$(readlink -f "$0")")
DEST="$PREFIX/share/glint"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable"

remove_legacy() {
    rm -f "$APPS/$LEGACY_APP_ID.desktop" "$PREFIX/share/kglobalaccel/$LEGACY_APP_ID.desktop" \
          "$ICONS/apps/$LEGACY_APP_ID.svg" "$ICONS/apps/$LEGACY_APP_ID-tray-symbolic.svg"
}

refresh() {
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
    # Lets Plasma pick up (or drop) our global shortcuts without a re-login.
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" || true
}

if [ "${1:-}" = "--uninstall" ]; then
    # Give Print Screen back to Spectacle before Glint disappears.
    "$PREFIX/bin/glint" unset-default >/dev/null 2>&1 || true
    "$PREFIX/bin/glint" quit >/dev/null 2>&1 || true
    rm -rf "$DEST"
    rm -f "$PREFIX/bin/glint" "$APPS/$APP_ID.desktop" "$PREFIX/share/kglobalaccel/$APP_ID.desktop" \
          "$ICONS/apps/$APP_ID.svg" "$ICONS/apps/$APP_ID-tray-symbolic.svg" \
          "$HOME/.config/autostart/$APP_ID.desktop"
    remove_legacy
    refresh
    echo "Glint removed. Settings remain in ~/.config/glint."
    exit 0
fi

# Required runtime pieces (all from Fedora repositories).
missing=""
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Gst", "1.0"); from gi.repository import Gtk, Adw, Gst' 2>/dev/null \
    || missing="$missing python3-gobject gtk4 libadwaita gstreamer1"
gst-inspect-1.0 pipewiresrc >/dev/null 2>&1 || missing="$missing pipewire-gstreamer"
gst-inspect-1.0 x264enc >/dev/null 2>&1 || gst-inspect-1.0 openh264enc >/dev/null 2>&1 \
    || missing="$missing gstreamer1-plugins-ugly"
if [ -n "$missing" ]; then
    echo "Missing required packages:$missing"
    echo "Install with: sudo dnf install$missing"
    exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST" "$PREFIX/bin" "$APPS" "$ICONS/apps"
cp -r "$SRC/glint" "$SRC/data" "$SRC/bin" "$DEST/"
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod +x "$DEST/bin/glint"
ln -sf "$DEST/bin/glint" "$PREFIX/bin/glint"

remove_legacy
install -m644 "$SRC/data/$APP_ID.desktop" "$APPS/$APP_ID.desktop"
mkdir -p "$PREFIX/share/kglobalaccel"
ln -sf "$APPS/$APP_ID.desktop" "$PREFIX/share/kglobalaccel/$APP_ID.desktop"
install -m644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ICONS/apps/"
install -m644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID-tray-symbolic.svg" "$ICONS/apps/"
refresh
command -v xdg-mime >/dev/null && xdg-mime default "$APP_ID.desktop" x-scheme-handler/glint 2>/dev/null || true

echo "Glint installed to $DEST"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: add $PREFIX/bin to your PATH." ;; esac

optional=""
command -v spectacle >/dev/null || command -v grim >/dev/null || optional="$optional spectacle"
command -v tesseract >/dev/null || optional="$optional tesseract"
command -v ffmpeg >/dev/null || optional="$optional ffmpeg-free"
command -v gifsicle >/dev/null || optional="$optional gifsicle"
command -v wl-copy >/dev/null || optional="$optional wl-clipboard"
if [ -n "$optional" ]; then
    echo "Optional extras (OCR, GIFs, reliable clipboard): sudo dnf install$optional"
fi
echo "Start it with: glint --background   (or enable 'Launch at login' in Preferences)"
