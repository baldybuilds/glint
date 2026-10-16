import datetime
import json
import os
import tempfile
import time
import unittest

from glint.history import History, day_label, human_size


def fake_thumb(src, dest):
    with open(dest, "w") as fh:
        fh.write("thumb of " + src)
    return True


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "history")
        self.user_dir = os.path.join(self.tmp.name, "Pictures")
        self.cache_dir = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.user_dir)
        os.makedirs(self.cache_dir)
        self.clock = Clock()

    def make(self, retention="week"):
        return History(self.root, retention=retention, clock=self.clock, thumbnailer=fake_thumb)

    def file(self, directory, name):
        path = os.path.join(directory, name)
        with open(path, "w") as fh:
            fh.write(name)
        return path

    def test_saved_capture_is_referenced_not_moved(self):
        h = self.make()
        saved = self.file(self.user_dir, "Shot.png")
        e = h.add(saved, "screenshot", width=800, height=600)
        self.assertEqual(e["path"], saved)
        self.assertTrue(os.path.exists(saved))
        self.assertTrue(os.path.exists(h.thumb_path(e)))
        self.assertFalse(h.owns(saved))

    def test_unsaved_capture_is_adopted(self):
        h = self.make()
        cached = self.file(self.cache_dir, "area-123.png")
        e = h.add(cached, "screenshot", adopt=True)
        self.assertFalse(os.path.exists(cached))
        self.assertTrue(h.owns(e["path"]))
        self.assertTrue(os.path.exists(e["path"]))

    def test_newest_first_and_persistence(self):
        h = self.make()
        a = h.add(self.file(self.user_dir, "a.png"))
        self.clock.t += 10
        b = h.add(self.file(self.user_dir, "b.png"))
        self.assertEqual([e["id"] for e in h.entries], [b["id"], a["id"]])
        again = self.make()
        self.assertEqual([e["id"] for e in again.entries], [b["id"], a["id"]])

    def test_expiry_deletes_only_owned_files(self):
        h = self.make("day")
        saved = self.file(self.user_dir, "keep-me.png")
        h.add(saved)
        owned = h.add(self.file(self.cache_dir, "temp.png"), adopt=True)["path"]
        self.clock.t += 2 * 86400
        h2 = self.make("day")          # pruned on load
        self.assertEqual(h2.entries, [])
        self.assertTrue(os.path.exists(saved), "never deletes the user's saved files")
        self.assertFalse(os.path.exists(owned))

    def test_forever_keeps_old_entries(self):
        h = self.make("forever")
        h.add(self.file(self.user_dir, "old.png"))
        self.clock.t += 400 * 86400
        self.assertEqual(len(self.make("forever").entries), 1)

    def test_missing_files_are_forgotten(self):
        h = self.make()
        path = self.file(self.user_dir, "gone.png")
        h.add(path)
        os.remove(path)
        self.assertEqual(self.make().entries, [])

    def test_off_records_nothing_and_clears(self):
        h = self.make()
        owned = h.add(self.file(self.cache_dir, "x.png"), adopt=True)["path"]
        h.set_retention("off")
        self.assertEqual(h.entries, [])
        self.assertFalse(os.path.exists(owned))
        self.assertIsNone(h.add(self.file(self.user_dir, "y.png")))

    def test_relink_after_save_drops_own_copy(self):
        h = self.make()
        e = h.add(self.file(self.cache_dir, "x.png"), adopt=True)
        owned = e["path"]
        saved = self.file(self.user_dir, "Saved.png")
        h.relink(owned, saved)
        self.assertEqual(h.find(e["id"])["path"], saved)
        self.assertFalse(os.path.exists(owned))

    def test_remove(self):
        h = self.make()
        saved = self.file(self.user_dir, "s.png")
        e = h.add(saved)
        self.assertTrue(h.remove(e["id"]))
        self.assertTrue(os.path.exists(saved), "remove keeps user files unless asked")
        self.assertFalse(os.path.exists(h.thumb_path(e)))
        self.assertFalse(h.remove("nope"))

    def test_listeners_and_corrupt_index(self):
        h = self.make()
        calls = []
        h.connect(lambda: calls.append(1))
        h.add(self.file(self.user_dir, "a.png"))
        self.assertEqual(calls, [1])
        with open(os.path.join(self.root, "index.json"), "w") as fh:
            fh.write("{broken")
        self.assertEqual(self.make().entries, [])

    def test_thumbnail_refreshes_when_capture_changes(self):
        h = self.make()
        path = self.file(self.user_dir, "a.png")
        e = h.add(path)
        thumb = h.thumb_path(e)
        old = time.time() - 100
        os.utime(thumb, (old, old))
        before = os.path.getmtime(thumb)
        self.assertEqual(h.fresh_thumb(e), thumb)
        self.assertGreater(os.path.getmtime(thumb), before)   # annotated → regenerated

    def test_video_entry_uses_poster(self):
        h = self.make()
        video = self.file(self.user_dir, "rec.mp4")
        poster = self.file(self.cache_dir, "poster.png")
        e = h.add(video, "video", duration=12.5, poster=poster)
        with open(h.thumb_path(e)) as fh:
            self.assertIn("poster.png", fh.read())
        with open(os.path.join(self.root, "index.json")) as fh:
            index = json.load(fh)
        self.assertEqual(index["entries"][0]["duration"], 12.5)


class HelperTests(unittest.TestCase):
    def test_day_label(self):
        now = datetime.datetime(2026, 10, 16, 12, 0).timestamp()
        at = lambda *d: datetime.datetime(*d).timestamp()  # noqa: E731
        self.assertEqual(day_label(at(2026, 10, 16, 1, 0), now), "Today")
        self.assertEqual(day_label(at(2026, 10, 15, 23, 0), now), "Yesterday")
        self.assertEqual(day_label(at(2026, 10, 12, 9, 0), now), "Monday")
        self.assertEqual(day_label(at(2026, 9, 1, 9, 0), now), "1 September 2026")

    def test_human_size(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2 * 1024 * 1024), "2.0 MB")


if __name__ == "__main__":
    unittest.main()
