import io
import os
import re
import tempfile
import unittest

from glint import log


class LogTests(unittest.TestCase):
    def test_tee_writes_both_and_timestamps_lines(self):
        console, logfile = io.StringIO(), io.StringIO()
        tee = log.Tee(console, logfile)
        tee.write("first line\nsecond ")
        tee.write("half\n")
        self.assertEqual(console.getvalue(), "first line\nsecond half\n")
        lines = logfile.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertRegex(line, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")
        self.assertTrue(lines[1].endswith("second half"))

    def test_colour_codes_kept_out_of_the_file(self):
        console, logfile = io.StringIO(), io.StringIO()
        tee = log.Tee(console, logfile)
        tee.write("\x1b[1;35mKeyboardInterrupt\x1b[0m\n")
        self.assertIn("\x1b[", console.getvalue())
        self.assertTrue(logfile.getvalue().endswith(" KeyboardInterrupt\n"))

    def test_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "glint.log")
            with open(path, "w") as fh:
                fh.write("x" * 50)
            log.rotate(path, max_bytes=10)
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(path + ".1"))
            log.rotate(path, max_bytes=10)   # missing file is fine

    def test_start_writes_header(self):
        import sys
        saved = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "glint.log")
            try:
                log.start(path)
                print("hello from test")
            finally:
                sys.stdout.flush()
                sys.stdout, sys.stderr = saved
            text = open(path).read()
        self.assertTrue(re.search(r"--- Glint \S+ started", text))
        self.assertIn("hello from test", text)


if __name__ == "__main__":
    unittest.main()
