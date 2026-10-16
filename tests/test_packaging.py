"""Guards against the app ID and its many copies drifting apart."""
import configparser
import os
import re
import unittest
import xml.etree.ElementTree as ET

from glint import APP_ID, LEGACY_APP_ID, kwin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class PackagingTests(unittest.TestCase):
    def test_app_id_is_reverse_dns_we_control(self):
        self.assertEqual(APP_ID, "io.github.baldybuilds.Glint")
        self.assertNotEqual(APP_ID, LEGACY_APP_ID)

    def test_desktop_file(self):
        path = os.path.join(ROOT, "data", f"{APP_ID}.desktop")
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp.read(path)
        self.assertEqual(cp["Desktop Entry"]["Icon"], APP_ID)
        self.assertIn("x-scheme-handler/glint", cp["Desktop Entry"]["MimeType"])
        for action in cp["Desktop Entry"]["Actions"].strip(";").split(";"):
            self.assertIn(f"Desktop Action {action}", cp)

    def test_metainfo(self):
        root = ET.parse(os.path.join(ROOT, "data", f"{APP_ID}.metainfo.xml")).getroot()
        self.assertEqual(root.findtext("id"), APP_ID)
        self.assertEqual(root.findtext("launchable"), f"{APP_ID}.desktop")
        self.assertEqual(root.findtext("replaces/id"), LEGACY_APP_ID)
        self.assertGreaterEqual(len(root.findall("screenshots/screenshot")), 1)
        for img in root.iter("image"):
            name = img.text.rsplit("/", 1)[-1]
            self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "images", name)), name)
        from glint import VERSION
        self.assertEqual(root.find("releases/release").get("version"), VERSION)

    def test_icons(self):
        apps = os.path.join(ROOT, "data", "icons", "hicolor", "scalable", "apps")
        self.assertTrue(os.path.exists(os.path.join(apps, f"{APP_ID}.svg")))
        self.assertTrue(os.path.exists(os.path.join(apps, f"{APP_ID}-tray-symbolic.svg")))
        self.assertIn(f"{APP_ID}-tray-symbolic", read("glint", "tray.py"))

    def test_dbus_names(self):
        object_path = "/" + APP_ID.replace(".", "/")
        launcher = read("bin", "glint")
        self.assertIn(f"--dest {APP_ID}", launcher)
        self.assertIn(f"--object-path {object_path}", launcher)
        self.assertTrue(kwin.BRIDGE_PATH.startswith(object_path + "/"))

    def test_spec_and_installer(self):
        spec = read("packaging", "glint.spec")
        for name in (f"{APP_ID}.desktop", f"{APP_ID}.metainfo.xml", f"{APP_ID}.svg", f"{APP_ID}-tray-symbolic.svg"):
            self.assertIn(name, spec)
        self.assertNotIn(LEGACY_APP_ID, spec.split("%changelog")[0])
        self.assertIn(f"APP_ID={APP_ID}", read("install.sh"))
        from glint import VERSION
        self.assertRegex(spec, rf"%global glint_version {re.escape(VERSION)}\b")
