# Contributing to Glint

Thanks for helping make Glint better! Bug reports, testing on other desktops,
documentation fixes and code are all welcome, whether it's a typo or a whole
feature.

- 🐛 **Found a bug?** [Open an issue](https://github.com/baldybuilds/glint/issues/new)
- 💡 **Have an idea?** Open an issue first so we can agree on the approach before you write code
- 🔧 **Want to code?** Pick an issue, or something from the [roadmap](README.md#%EF%B8%8F-roadmap), and open a pull request

## Licensing of contributions

Glint is open source under the [MIT License](LICENSE). By submitting a pull
request, you agree that your contribution is licensed under the same MIT
License, and you confirm that you wrote it or otherwise have the right to
submit it under that license.

Please **don't submit code, icons, sounds or other assets copied from
proprietary software** (including CleanShot X), and don't include screenshots
or recordings that contain other people's personal information.

## Reporting bugs

Glint talks to a lot of desktop plumbing (KWin, portals, PipeWire,
GStreamer), so the environment matters. Please include:

| | How to find it |
| --- | --- |
| Glint version | `glint --version` |
| Fedora version | `cat /etc/fedora-release` |
| Desktop and session | `echo $XDG_CURRENT_DESKTOP $XDG_SESSION_TYPE` |
| Plasma version (KDE) | `plasmashell --version` |
| GPU | `lspci \| grep -iE 'vga\|3d'` |
| Monitors and scaling | e.g. "2 monitors, 1440p at 100% + 4K at 150%" |

…plus what you did, what you expected and what happened instead. Logs help
a lot:

```sh
glint quit
GLINT_DEBUG=1 glint --background 2>&1 | tee glint.log
# reproduce the problem, then attach glint.log
```

> [!TIP]
> Check the log for file paths or window titles you'd rather not share
> before attaching it.

## Development setup

Glint is plain Python on top of GTK 4, libadwaita and GStreamer. There's no
build step and no virtualenv: the bindings come from Fedora packages, so
always use the **system** interpreter (`/usr/bin/python3`).

```sh
git clone https://github.com/baldybuilds/glint.git
cd glint
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita \
                 gstreamer1-plugins-base gstreamer1-plugins-good pipewire-gstreamer \
                 spectacle ffmpeg-free tesseract wl-clipboard
```

Run it straight from the tree. Quit any installed copy first, because only
one instance can own the tray:

```sh
glint quit 2>/dev/null
GLINT_DEBUG=1 GLINT_NO_SHORTCUTS=1 ./bin/glint --background
./bin/glint capture-area            # talk to the running instance
```

`GLINT_NO_SHORTCUTS=1` skips shortcut registration and the first-run
"use Glint for Print Screen" prompt, which keeps your real desktop settings
untouched while you hack.

## Tests and checks

```sh
/usr/bin/python3 -m unittest discover -s tests -t .       # must pass
uvx ruff check --select F,E9 glint tests                  # optional lint (undefined/unused names)
packaging/build-rpm.sh                                    # optional: build the RPM (runs the tests too)
```

The unit tests need no display. They cover the logic that can be tested in
isolation: geometry, file naming, config, the annotation model, scrolling
stitcher, trimmer helpers, click highlighting and the shortcut takeover
(against fake KGlobalAccel/GSettings backends). Please add tests when you
change any of that.

Most UI and desktop integration (overlays, portals, KWin scripts, recording)
can only be checked by running Glint. In your pull request, say what you
tested by hand and on which desktop.

## Code style

- **Match the surrounding code.** Same naming, comment density and
  structure. Keep modules focused (see the
  [project layout](README.md#%EF%B8%8F-development)).
- **Keep pure logic GTK-free** where possible (like `stitcher.py`,
  `geometry.py`, `naming.py`) so it can be unit-tested.
- **New settings** go in `DEFAULTS` in `glint/config.py`, with a row in
  `glint/prefs.py`.
- **New commands** go in `COMMANDS` and `run_command()` in `glint/app.py`, the
  fast-path list in `bin/glint` if they take no arguments, and the README.
- **Never leave the user's desktop changed.** Anything Glint changes outside
  its own config (KWin effects, global shortcuts) must be backed up first and
  restored afterwards, including after a crash. See `clicks.py` and
  `takeover.py`.

<details>
<summary><b>GTK / PyGObject pitfalls we've already hit</b></summary>

- **Don't use the return value of `init` methods on boxed structs.**
  `Gsk.RoundedRect().init_from_rect(...)` and `Graphene.Rect().init(...)`
  return a pointer into a freed temporary. Use the helpers in
  `glint/widgets.py` (`rect()`, `rounded()`, `point()`).
- **Never modify widgets inside a draw function or snapshot.** It invalidates
  their allocation mid-frame and GTK silently skips drawing them. Defer the
  change with `GLib.idle_add`.
- **`Gtk.Picture` reports the image's pixel size as its natural size**, which
  makes undecorated windows huge. Use `widgets.ImageView` when you need an
  explicit display size.
- **Wayland clients can't position or raise their own windows.** On Plasma,
  Glint's KWin rules script places any window titled
  `glint:<role>:<x>:<y>` and keeps it above (see `glint/kwin.py`).
- **Test harnesses must not unload the real KWin rules script** (plugin
  `glint-window-rules`) that a running Glint depends on. Use a separate config
  directory (`XDG_CONFIG_HOME=$(mktemp -d)`) for throwaway runs.

</details>

## Pull requests

1. Fork the repo and create a branch from `main` (`fix/overlay-hidpi`, `feat/upload`, …).
2. Keep each PR focused on one change; small PRs get reviewed faster.
3. Make sure the tests pass, and add tests for new logic.
4. Update the README if you change behaviour, commands, shortcuts or settings.
5. Describe **what** changed, **why**, and **how you tested it** (desktop,
   session, GPU). Screenshots or short recordings are great for UI changes.

Commit messages: a short imperative summary line ("Fix loupe on HiDPI
monitors"), then a blank line and any detail.

## Releasing (maintainers)

1. Bump `VERSION` in `glint/__init__.py` and the fallback `glint_version` in `packaging/glint.spec`.
2. Add a `<release>` entry to `data/app.glint.Glint.metainfo.xml` and a `%changelog` entry to the spec.
3. Add the version's section to `CHANGELOG.md`; it becomes the release notes.
4. Commit, then tag and push:
   ```sh
   git tag v0.3.0 && git push origin main v0.3.0
   ```
   The **Release** workflow builds the RPM in Fedora, runs the tests and publishes the GitHub Release with the RPMs and `SHA256SUMS` attached. It refuses to publish if the tag doesn't match `VERSION`.

## Be kind

Be respectful and constructive, and assume good intent. Everyone here is
volunteering their time.
