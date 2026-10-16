import copy
import os
import tempfile
import unittest

from glint.config import Config
from glint.takeover import (GLINT, LEGACY_GLINT, SPECTACLE, GnomeTakeover, KDETakeover, LegacyShortcutStore,
                            key_to_string, migrate_legacy_component, parse_key, remove_stray_portal_shortcuts,
                            restore_default_keys)

PRINT = 0x01000009
META, SHIFT, CTRL, ALT = 0x10000000, 0x02000000, 0x04000000, 0x08000000


class KeyCodeTests(unittest.TestCase):
    def test_decode_real_spectacle_codes(self):
        # Values read from a live Plasma 6.7 session.
        self.assertEqual(key_to_string(16777225), "Print")
        self.assertEqual(key_to_string(301989971), "Meta+Shift+S")
        self.assertEqual(key_to_string(50331657), "Shift+Print")
        self.assertEqual(key_to_string(352321545), "Meta+Ctrl+Print")
        self.assertEqual(key_to_string(402653266), "Meta+Alt+R")

    def test_round_trip(self):
        for text in ("Print", "Meta+Shift+4", "Ctrl+Alt+F12", "Meta+Space", "Shift+Print"):
            self.assertEqual(key_to_string(parse_key(text)), text)
        self.assertEqual(parse_key("Super+Shift+s"), META | SHIFT | ord("S"))


def spectacle_defaults():
    return {
        "_launch": [PRINT, META | SHIFT | ord("S")],
        "RectangularRegionScreenShot": [META | SHIFT | PRINT],
        "FullScreenScreenShot": [SHIFT | PRINT],
        "CurrentMonitorScreenShot": [],
        "ActiveWindowScreenShot": [META | PRINT],
        "WindowUnderCursorScreenShot": [META | CTRL | PRINT],
        "RecordRegion": [META | SHIFT | ord("R"), META | ord("R")],
        "RecordScreen": [META | ALT | ord("R")],
        "RecordWindow": [META | CTRL | ord("R")],
        "OpenWithoutScreenshot": [],
    }


def glint_defaults():
    return {
        "CaptureArea": [META | SHIFT | ord("4")],
        "CaptureFullscreen": [META | SHIFT | ord("3")],
        "CaptureWindow": [META | SHIFT | ord("5")],
        "RecordScreen": [META | SHIFT | ord("8")],
        "_launch": [],
    }


class FakeKGA:
    def __init__(self):
        self.components = {SPECTACLE: spectacle_defaults(), GLINT: glint_defaults(),
                           "org.kde.konsole": {"NewTab": [CTRL | SHIFT | ord("T")]}}
        self.defaults = {}
        self.friendly = {("org.kde.konsole", "capture-area"): "Capture area",
                         ("org.kde.konsole", "NewTab"): "New Tab"}

    def available(self):
        return True

    def has_component(self, c):
        return c in self.components

    def actions(self, c):
        return {a: {"keys": list(k), "friendly": a, "component_friendly": c,
                    "defaults": list(self.defaults.get((c, a), k))}
                for a, k in self.components.get(c, {}).items()}

    def owners(self, key):
        return [(c, a) for c, acts in self.components.items() for a, keys in acts.items() if key in keys]

    def set_keys(self, c, _cf, a, _af, keys):
        # KGlobalAccel refuses keys another action already owns.
        for other_c, acts in self.components.items():
            for other_a, other_keys in acts.items():
                if (other_c, other_a) != (c, a):
                    assert not set(keys) & set(other_keys), f"conflict on {(other_c, other_a)}"
        self.components[c][a] = list(keys)

    def all_infos(self):
        return [(c, a, self.friendly.get((c, a), a)) for c, acts in self.components.items() for a in acts]

    def unregister(self, c, a):
        del self.components[c][a]


class KDETakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(os.path.join(self.tmp.name, "config.json"))
        self.kga = FakeKGA()
        self.t = KDETakeover(self.cfg, self.kga)

    def tearDown(self):
        self.tmp.cleanup()

    def test_take_over_moves_keys_to_matching_actions(self):
        self.assertTrue(self.t.available())
        self.assertFalse(self.t.active())
        plan = self.t.take_over()
        self.assertIn(("Print", "Capture Area"), plan)
        self.assertIn(("Shift+Print", "Capture Fullscreen"), plan)
        g = self.kga.components[GLINT]
        self.assertEqual(g["CaptureArea"][0], META | SHIFT | ord("4"))      # own key kept first
        self.assertIn(PRINT, g["CaptureArea"])
        self.assertIn(META | SHIFT | ord("S"), g["CaptureArea"])
        self.assertIn(META | PRINT, g["CaptureWindow"])
        self.assertIn(META | SHIFT | ord("R"), g["RecordScreen"])
        self.assertTrue(all(not k for k in self.kga.components[SPECTACLE].values()))
        self.assertTrue(self.t.active())

    def test_restore_is_exact(self):
        before = copy.deepcopy(self.kga.components)
        self.t.take_over()
        self.assertTrue(self.t.restore())
        self.assertEqual(self.kga.components, before)
        self.assertFalse(self.t.active())
        self.assertFalse(self.t.restore())

    def test_preserves_user_customised_keys(self):
        self.kga.components[SPECTACLE]["_launch"] = [CTRL | PRINT]
        self.t.take_over()
        self.assertIn(CTRL | PRINT, self.kga.components[GLINT]["CaptureArea"])
        self.assertNotIn(PRINT, self.kga.components[GLINT]["CaptureArea"])
        self.t.restore()
        self.assertEqual(self.kga.components[SPECTACLE]["_launch"], [CTRL | PRINT])

    def test_backup_survives_restart(self):
        self.t.take_over()
        fresh = KDETakeover(Config(self.cfg.path), self.kga)
        self.assertTrue(fresh.active())
        fresh.restore()
        self.assertEqual(self.kga.components[SPECTACLE]["_launch"], [PRINT, META | SHIFT | ord("S")])

    def test_unavailable_without_glint_component(self):
        del self.kga.components[GLINT]
        self.assertFalse(self.t.available())
        self.assertIn("not registered", self.t.reason_unavailable())

    def test_stray_portal_cleanup_only_touches_glint_entries(self):
        # The portal filed Glint's shortcut under Konsole, so KDE refused
        # Glint's own default for that key.
        self.kga.components["org.kde.konsole"]["capture-area"] = [META | SHIFT | ord("4")]
        self.kga.components[GLINT]["CaptureArea"] = []
        self.kga.defaults[(GLINT, "CaptureArea")] = [META | SHIFT | ord("4")]
        removed = remove_stray_portal_shortcuts(self.kga, {"capture-area": "Capture area"})
        self.assertEqual(removed, [("org.kde.konsole", "capture-area")])
        self.assertIn("NewTab", self.kga.components["org.kde.konsole"])
        self.assertEqual(restore_default_keys(self.kga), ["CaptureArea"])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [META | SHIFT | ord("4")])

    def test_default_keys_not_stolen_from_other_apps(self):
        self.kga.components[GLINT]["CaptureArea"] = []
        self.kga.defaults[(GLINT, "CaptureArea")] = [CTRL | SHIFT | ord("T")]   # Konsole's
        self.assertEqual(restore_default_keys(self.kga), [])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [])


class FakeLegacy:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})
        self.cleared = []

    def read(self):
        return dict(self.saved)

    def clear(self, actions):
        self.cleared += actions
        for a in actions:
            self.saved.pop(a, None)


class LegacyMigrationTests(unittest.TestCase):
    """0.3.0 renamed the app ID; keys must move from the old component."""

    def setUp(self):
        self.kga = FakeKGA()
        # Old component holds the user's keys, including a Print takeover…
        self.kga.components[LEGACY_GLINT] = {
            "CaptureArea": [META | SHIFT | ord("4"), PRINT],
            "CaptureFullscreen": [SHIFT | PRINT],
            "RecordScreen": [],
            "_launch": [],
        }
        # …so the freshly registered new component got nothing.
        self.kga.components[GLINT] = {a: [] for a in ("CaptureArea", "CaptureFullscreen", "RecordScreen",
                                                      "CaptureWindow", "_launch")}
        self.kga.components[SPECTACLE] = {a: [] for a in spectacle_defaults()}

    def test_keys_move_to_new_component(self):
        moved = migrate_legacy_component(self.kga, store=FakeLegacy())
        self.assertEqual(sorted(moved), ["CaptureArea", "CaptureFullscreen"])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [META | SHIFT | ord("4"), PRINT])
        self.assertEqual(self.kga.components[GLINT]["CaptureFullscreen"], [SHIFT | PRINT])
        self.assertEqual(self.kga.components[LEGACY_GLINT], {})

    def test_waits_for_new_component(self):
        del self.kga.components[GLINT]
        self.assertIsNone(migrate_legacy_component(self.kga, store=FakeLegacy()))
        self.assertIn(PRINT, self.kga.components[LEGACY_GLINT]["CaptureArea"])   # untouched

    def test_nothing_to_migrate(self):
        del self.kga.components[LEGACY_GLINT]
        self.assertEqual(migrate_legacy_component(self.kga, store=FakeLegacy()), [])

    def test_component_already_dropped_uses_saved_section(self):
        # What really happens on an RPM upgrade: the old .desktop file is gone,
        # so KDE no longer lists the component; only kglobalshortcutsrc has it.
        del self.kga.components[LEGACY_GLINT]
        self.kga.components[GLINT].update({"CaptureArea": [META | SHIFT | ord("4")],
                                           "CaptureFullscreen": [META | SHIFT | ord("3")]})
        F11 = parse_key("F11")
        store = FakeLegacy({"CaptureArea": [F11, META | SHIFT | ord("4")],
                            "CaptureFullscreen": [SHIFT | PRINT, META | SHIFT | ord("3")]})
        moved = migrate_legacy_component(self.kga, store=store)
        self.assertEqual(sorted(moved), ["CaptureArea", "CaptureFullscreen"])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [META | SHIFT | ord("4"), F11])
        self.assertIn(SHIFT | PRINT, self.kga.components[GLINT]["CaptureFullscreen"])
        self.assertEqual(sorted(store.cleared), ["CaptureArea", "CaptureFullscreen"])
        self.assertEqual(migrate_legacy_component(self.kga, store=store), [])   # runs once

    def test_saved_keys_owned_elsewhere_are_not_stolen(self):
        del self.kga.components[LEGACY_GLINT]
        self.kga.components["org.kde.konsole"] = {"NewTab": [parse_key("F11")]}
        store = FakeLegacy({"CaptureArea": [parse_key("F11")]})
        self.assertEqual(migrate_legacy_component(self.kga, store=store), [])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [])

    def test_saved_section_parser(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kglobalshortcutsrc")
            with open(path, "w") as fh:
                fh.write("[kwin]\nExpose=Ctrl+F9\n\n"
                         "[services][app.glint.Glint.desktop]\n"
                         "CaptureArea=F11\\tMeta+Shift+4\n"
                         "CaptureWindow=Meta+Print\\tMeta+Shift+5\\tMeta+Ctrl+Print\n"
                         "_launch=none\n\n"
                         "[services][org.kde.spectacle.desktop]\n_launch=none\n")
            saved = LegacyShortcutStore(path).read()
        self.assertEqual(saved["CaptureArea"], [parse_key("F11"), META | SHIFT | ord("4")])
        self.assertEqual(saved["CaptureWindow"], [META | PRINT, META | SHIFT | ord("5"), META | CTRL | PRINT])
        self.assertEqual(saved["_launch"], [])
        self.assertEqual(set(saved), {"CaptureArea", "CaptureWindow", "_launch"})

    def test_takeover_restore_works_after_migration(self):
        cfg_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cfg_dir.cleanup)
        cfg = Config(os.path.join(cfg_dir.name, "config.json"))
        # Backup recorded by a 0.2.x takeover (same action names).
        cfg.set("state.takeover", {"desktop": "kde", "spectacle": {"_launch": [PRINT]},
                                   "glint": {"CaptureArea": [META | SHIFT | ord("4")]}})
        migrate_legacy_component(self.kga, store=FakeLegacy())
        KDETakeover(cfg, self.kga).restore()
        self.assertEqual(self.kga.components[SPECTACLE]["_launch"], [PRINT])
        self.assertEqual(self.kga.components[GLINT]["CaptureArea"], [META | SHIFT | ord("4")])


class FakeStore:
    def __init__(self):
        self.schemas = {"org.gnome.shell.keybindings", "org.gnome.settings-daemon.plugins.media-keys"}
        self.values = {
            ("org.gnome.shell.keybindings", "show-screenshot-ui", None): ["Print"],
            ("org.gnome.shell.keybindings", "screenshot", None): ["<Shift>Print"],
            ("org.gnome.shell.keybindings", "screenshot-window", None): ["<Alt>Print"],
            ("org.gnome.shell.keybindings", "show-screencast-ui", None): ["<Shift><Control><Alt>R"],
            ("org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", None): ["/mine/"],
        }

    def has_schema(self, s):
        return s in self.schemas

    def get_strv(self, schema, key, path=None):
        return list(self.values.get((schema, key, path), []))

    def set_strv(self, schema, key, value, path=None):
        self.values[(schema, key, path)] = list(value)

    def set_string(self, schema, key, value, path=None):
        self.values[(schema, key, path)] = value

    def reset(self, schema, key, path=None):
        self.values.pop((schema, key, path), None)


class GnomeTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(os.path.join(self.tmp.name, "config.json"))
        self.store = FakeStore()
        self.t = GnomeTakeover(self.cfg, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_take_over_and_restore(self):
        before = copy.deepcopy(self.store.values)
        plan = self.t.take_over()
        self.assertIn(("Print", "Capture Area"), plan)
        self.assertEqual(self.store.get_strv(GnomeTakeover.SHELL, "show-screenshot-ui"), [])
        paths = self.store.get_strv(GnomeTakeover.MEDIA, "custom-keybindings")
        self.assertEqual(paths[0], "/mine/")
        area = f"{GnomeTakeover.BASE}glint-capture-area-0/"
        self.assertIn(area, paths)
        self.assertEqual(self.store.values[(GnomeTakeover.CUSTOM, "command", area)], "glint capture-area")
        self.assertEqual(self.store.values[(GnomeTakeover.CUSTOM, "binding", area)], "Print")
        self.assertTrue(self.t.restore())
        self.assertEqual(self.store.values, before)


if __name__ == "__main__":
    unittest.main()
