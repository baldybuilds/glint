# Build with packaging/build-rpm.sh (it passes glint_version from glint/__init__.py).
%{!?glint_version: %global glint_version 0.4.0}

Name:           glint
Version:        %{glint_version}
Release:        1%{?dist}
Summary:        Capture, annotate and record your screen
License:        MIT
URL:            https://github.com/baldybuilds/glint
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
# For the unit tests in %%check.
BuildRequires:  python3-gobject
BuildRequires:  python3-cairo
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  pango
BuildRequires:  glib2
BuildRequires:  gdk-pixbuf2
BuildRequires:  graphene
# Ships the cairo-1.0 typelib (only a weak dependency elsewhere, so minimal
# build roots like Copr's mock don't get it otherwise).
BuildRequires:  gobject-introspection
# Text-rendering tests need real fonts in minimal build roots (mock/Copr).
BuildRequires:  (dejavu-sans-fonts or google-noto-sans-fonts)
BuildRequires:  (dejavu-sans-mono-fonts or google-noto-sans-mono-fonts)

Requires:       python3
Requires:       python3-gobject >= 3.50
Requires:       python3-cairo
Requires:       gtk4 >= 4.14
Requires:       libadwaita >= 1.5
Requires:       graphene
Requires:       pango
Requires:       glib2
Requires:       gdk-pixbuf2
Requires:       gobject-introspection
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       pipewire-gstreamer
Requires:       xdg-desktop-portal
# An H.264 encoder for recordings: x264 (RPM Fusion) or Cisco's OpenH264.
Requires:       (gstreamer1-plugins-ugly or gstreamer1-plugin-openh264)

# Silent, fast captures and window rules on Plasma.
Recommends:     spectacle
Recommends:     kf6-kconfig
# GIFs and trimming (ffmpeg-free from Fedora or ffmpeg from RPM Fusion).
Recommends:     /usr/bin/ffmpeg
Recommends:     wl-clipboard
Recommends:     libcanberra-gtk3
Suggests:       tesseract
Suggests:       gifsicle

%description
Glint is a tray-based screenshot and screen-recording tool for KDE Plasma on
Wayland, inspired by CleanShot X: area/window/fullscreen capture with a
magnifier, Quick Access thumbnails, an annotation editor, pinned screenshots,
scrolling capture, OCR, MP4/GIF recording with click highlighting and a video
trimmer.

%prep
%autosetup

%build
# Pure Python; nothing to build.

%install
install -d %{buildroot}%{_datadir}/%{name}
cp -a glint data bin %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -name __pycache__ -type d -prune -exec rm -rf {} +
rm %{buildroot}%{_datadir}/%{name}/data/io.github.baldybuilds.Glint.desktop \
   %{buildroot}%{_datadir}/%{name}/data/io.github.baldybuilds.Glint.metainfo.xml
chmod 0755 %{buildroot}%{_datadir}/%{name}/bin/glint
%{python3} -m compileall -q -d %{_datadir}/%{name}/glint %{buildroot}%{_datadir}/%{name}/glint

install -d %{buildroot}%{_bindir}
ln -s ../share/%{name}/bin/glint %{buildroot}%{_bindir}/glint

desktop-file-install --dir=%{buildroot}%{_datadir}/applications data/io.github.baldybuilds.Glint.desktop
# Plasma registers the desktop file's X-KDE-Shortcuts from share/kglobalaccel
# (the same mechanism Spectacle uses).
install -d %{buildroot}%{_datadir}/kglobalaccel
ln -s ../applications/io.github.baldybuilds.Glint.desktop %{buildroot}%{_datadir}/kglobalaccel/io.github.baldybuilds.Glint.desktop
install -Dpm 0644 -t %{buildroot}%{_datadir}/icons/hicolor/scalable/apps \
    data/icons/hicolor/scalable/apps/io.github.baldybuilds.Glint.svg \
    data/icons/hicolor/scalable/apps/io.github.baldybuilds.Glint-tray-symbolic.svg
install -Dpm 0644 data/io.github.baldybuilds.Glint.metainfo.xml %{buildroot}%{_metainfodir}/io.github.baldybuilds.Glint.metainfo.xml

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.baldybuilds.Glint.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/io.github.baldybuilds.Glint.metainfo.xml
HOME=$(mktemp -d) PYTHONPATH=%{buildroot}%{_datadir}/%{name} \
    %{python3} -m unittest discover -s tests -t . 2>&1

%files
%license LICENSE
%doc README.md
%{_bindir}/glint
%{_datadir}/%{name}/
%{_datadir}/applications/io.github.baldybuilds.Glint.desktop
%{_datadir}/kglobalaccel/io.github.baldybuilds.Glint.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.baldybuilds.Glint.svg
%{_datadir}/icons/hicolor/scalable/apps/io.github.baldybuilds.Glint-tray-symbolic.svg
%{_metainfodir}/io.github.baldybuilds.Glint.metainfo.xml

%changelog
* Fri Oct 16 2026 baldybuilds - 0.4.0-1
- Capture History: recent screenshots and recordings grouped by day, with
  copy, annotate/trim, pin, drag out and delete; configurable retention

* Fri Oct 16 2026 baldybuilds - 0.3.0-1
- New app ID io.github.baldybuilds.Glint; shortcuts, Print Screen takeover
  and autostart migrate automatically from app.glint.Glint
- Log file at ~/.cache/glint/glint.log, linked from Preferences
- Screenshots in the AppStream metadata

* Thu Oct 15 2026 baldybuilds - 0.2.1-1
- Ask for screen-sharing permission before the area selection (once), and
  share one remembered choice between recording and scrolling capture
- Fix recording and scrolling capture failing to start when the shared
  source is not a full screen (crop now uses the real stream size)
- Trimmer preview matches the video's aspect ratio (no letterbox bars)

* Thu Oct 15 2026 baldybuilds - 0.2.0-1
- Native KDE global shortcuts via KGlobalAccel (no portal prompt)
- "Use Glint for Print Screen": take over / restore Spectacle's shortcuts
- Remove stray shortcuts the portal filed under the launching terminal
- Record shortcut moved to Meta+Shift+8 to avoid Spectacle's Meta+Shift+R

* Thu Oct 15 2026 baldybuilds - 0.1.0-1
- Initial package
