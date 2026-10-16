# Changelog

All notable changes to Glint are listed here. Versions follow
[Semantic Versioning](https://semver.org/); dates are YYYY-MM-DD.

## [0.4.0] - 2026-10-16

### Added
- **Capture History** (tray → Capture History…, `glint history`, or bind it
  in System Settings): recent screenshots and recordings grouped by day,
  filterable, with copy, annotate/trim, pin, open, show in folder, drag out
  and delete. Captures you saved are only referenced, never deleted;
  unsaved captures are kept by history until they expire.
- Preferences → General → Capture History: keep captures for 1 day, 1 week
  (default), 1 month or forever, or turn history off; shows disk usage and
  can clear it.

## [0.3.0] - 2026-10-16

### Changed
- **New app ID: `io.github.baldybuilds.Glint`** (was `app.glint.Glint`, a
  domain the project doesn't own). Upgrading migrates everything
  automatically: your shortcuts, including Print Screen if Glint took it
  over, and "Launch at login". Screen-sharing permission was tied to the old
  ID, so recording asks for it once more.

### Added
- A log file at `~/.cache/glint/glint.log` (Preferences → General →
  Troubleshooting) to attach to bug reports.
- Screenshots in the app's AppStream metadata, shown by Discover and GNOME
  Software.
- Bug report and feature request forms on GitHub.
- CI: tests, lint and metadata checks run on every push and pull request on
  Fedora 44 and 45.

## [0.2.1] - 2026-10-15

### Changed
- Recording and scrolling capture now ask for screen-sharing permission
  *before* you select the area, with a one-time explanation, so the selection
  is always the last step and recording starts immediately. Once your desktop
  remembers the choice, the sharing dialog doesn't appear again, and
  recording and scrolling capture share that one choice.

### Fixed
- Recording video/GIFs and scrolling capture failed with "Recording pipeline
  failed to start" when the source shared in the screen-sharing dialog wasn't
  a whole screen (e.g. a region or window) or came without geometry. Glint
  now crops using the stream's real pixel size; if the shared source doesn't
  match your selection it records/captures all of it, explains why, and asks
  you to pick a screen again next time.
- More accurate cropping on fractionally scaled displays.
- The trimmer preview no longer shows black bars around wide or tall
  recordings (they were never part of the video); its window is now sized
  to the recording's shape.

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

[0.4.0]: https://github.com/baldybuilds/glint/releases/tag/v0.4.0
[0.3.0]: https://github.com/baldybuilds/glint/releases/tag/v0.3.0
[0.2.1]: https://github.com/baldybuilds/glint/releases/tag/v0.2.1
[0.2.0]: https://github.com/baldybuilds/glint/releases/tag/v0.2.0
[0.1.0]: https://github.com/baldybuilds/glint/commit/5bcc6cd
