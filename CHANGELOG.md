# Changelog

All notable changes to Glint are listed here. Versions follow
[Semantic Versioning](https://semver.org/); dates are YYYY-MM-DD.

## [0.2.0] - 2026-10-15

### Added
- **Use Glint for Print Screen:** take over Print, Shift+Print, Meta+Print,
  Meta+Shift+S and the other screenshot shortcuts from Spectacle (KDE) or the
  GNOME screenshot UI, and restore them exactly. Offered once on first start;
  toggle it in *Preferences → Shortcuts* or with `glint set-default` /
  `glint unset-default`.
- Native KDE Plasma global shortcuts through KGlobalAccel (listed under
  *System Settings → Shortcuts → Glint*), with no portal prompt or re-login.
- Video trimmer: filmstrip timeline, looped preview, frame-accurate MP4/GIF
  export, resize and remove audio (`glint trim-video`).
- Click highlighting for recordings using KWin's Mouse Click Animation
  effect, with your KWin settings restored afterwards.
- Near-instant command forwarding to the running instance, so shortcuts feel
  immediate.

### Changed
- The default record shortcut is now Meta+Shift+8, so it no longer clashes
  with Spectacle's Meta+Shift+R.

### Fixed
- Shortcuts that the portal had filed under the launching terminal
  (e.g. Konsole) are cleaned up automatically.

## [0.1.0] - 2026-10-15

### Added
- First release: area, window, fullscreen and previous-area capture with a
  magnifier; Quick Access Overlay; annotation editor; pinned screenshots;
  scrolling capture; self-timer; OCR; MP4/GIF recording; tray menu; CLI and
  `glint://` URLs; RPM packaging.

[0.2.0]: https://github.com/baldybuilds/glint/releases/tag/v0.2.0
[0.1.0]: https://github.com/baldybuilds/glint/commit/5bcc6cd
