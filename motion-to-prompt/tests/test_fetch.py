import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch  # noqa: E402


class ClassificationTest(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(fetch.is_url("https://dribbble.com/shots/123"))
        self.assertTrue(fetch.is_url("http://example.com/a.mp4"))
        self.assertFalse(fetch.is_url("~/Desktop/rec.mov"))
        self.assertFalse(fetch.is_url("/tmp/https-demo.mp4"))

    def test_direct_media_url(self):
        self.assertTrue(fetch.is_direct_media_url("https://cdn.x.com/a/b/clip.MP4?sig=1"))
        self.assertTrue(fetch.is_direct_media_url("https://i.imgur.com/abc.gif"))
        self.assertFalse(fetch.is_direct_media_url("https://x.com/user/status/123"))
        self.assertFalse(fetch.is_direct_media_url("https://dribbble.com/shots/123-hero"))

    def test_names(self):
        self.assertEqual(fetch.base_name("/Users/me/Screen Recording 2026.mov"),
                         "screen-recording-2026")
        self.assertEqual(fetch.base_name("https://dribbble.com/shots/123-Hero-Reveal"),
                         "123-hero-reveal")
        self.assertEqual(fetch.base_name("https://example.com/"), "example-com")
        self.assertEqual(fetch.base_name("https://x.org/Rotating_earth_%28large%29.gif"),
                         "rotating-earth-large")
        name = fetch.make_name("clip.gif", now=0)
        self.assertRegex(name, r"^clip-\d{8}-\d{6}$")


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_missing_local_file(self):
        with self.assertRaisesRegex(fetch.FetchError, "File not found"):
            fetch.fetch("/definitely/not/here.mp4", self.root)
        self.assertEqual(os.listdir(self.root), [])

    def test_local_file_used_in_place(self):
        src = os.path.join(self.root, "clip.mp4")
        open(src, "w").close()
        with mock.patch.object(fetch, "validate_video", return_value=5.0) as validate:
            result = fetch.fetch(src, self.root)
        validate.assert_called_once_with(src)
        self.assertEqual(result["path"], src)
        self.assertTrue(os.path.isdir(result["out_dir"]))
        self.assertTrue(result["name"].startswith("clip-"))

    def test_routes_direct_media_to_curl(self):
        with mock.patch.object(fetch, "download_direct", return_value="/x/source.mp4") as d, \
             mock.patch.object(fetch, "download_page") as p, \
             mock.patch.object(fetch, "validate_video", return_value=5.0):
            fetch.fetch("https://cdn.example.com/clip.mp4", self.root)
        d.assert_called_once()
        p.assert_not_called()

    def test_routes_page_to_ytdlp(self):
        with mock.patch.object(fetch, "download_direct") as d, \
             mock.patch.object(fetch, "download_page", return_value="/x/source.mp4") as p, \
             mock.patch.object(fetch, "require_tool"), \
             mock.patch.object(fetch, "page_duration", return_value=12.0), \
             mock.patch.object(fetch, "validate_video", return_value=5.0):
            fetch.fetch("https://x.com/user/status/123", self.root)
        p.assert_called_once()
        d.assert_not_called()

    def test_missing_ytdlp_has_install_hint(self):
        with mock.patch.object(fetch.shutil, "which", return_value=None):
            with self.assertRaisesRegex(fetch.FetchError, "brew install yt-dlp"):
                fetch.fetch("https://x.com/user/status/123", self.root)
        self.assertEqual(os.listdir(self.root), [], "empty output dir should be cleaned up")

    def test_long_local_file_rejected(self):
        src = os.path.join(self.root, "talk.mp4")
        open(src, "w").close()
        with mock.patch.object(fetch, "validate_video", return_value=181.0):
            with self.assertRaisesRegex(fetch.FetchError, r"3:01 long.*limit is 3:00"):
                fetch.fetch(src, self.root)
        self.assertEqual(os.listdir(self.root), ["talk.mp4"], "output dir should be removed")
        self.assertTrue(os.path.isfile(src), "the user's own file must never be deleted")

    def test_exactly_three_minutes_allowed(self):
        src = os.path.join(self.root, "clip.mp4")
        open(src, "w").close()
        with mock.patch.object(fetch, "validate_video", return_value=180.0):
            fetch.fetch(src, self.root)

    def test_long_page_video_rejected_before_download(self):
        with mock.patch.object(fetch, "require_tool"), \
             mock.patch.object(fetch, "page_duration", return_value=600.0), \
             mock.patch.object(fetch.subprocess, "run") as run:
            with self.assertRaisesRegex(fetch.FetchError, r"10:00 long"):
                fetch.fetch("https://x.com/user/status/123", self.root)
        run.assert_not_called()
        self.assertEqual(os.listdir(self.root), [])

    def test_long_direct_download_rejected_and_deleted(self):
        def fake_download(url, out_dir):
            path = os.path.join(out_dir, "source.mp4")
            open(path, "w").close()
            return path
        with mock.patch.object(fetch, "download_direct", side_effect=fake_download), \
             mock.patch.object(fetch, "validate_video", return_value=900.0):
            with self.assertRaisesRegex(fetch.FetchError, "limit is 3:00"):
                fetch.fetch("https://cdn.example.com/long.mp4", self.root)
        self.assertEqual(os.listdir(self.root), [], "downloaded file should be cleaned up")

    def test_page_duration_parsing(self):
        done = lambda out, code=0: mock.Mock(returncode=code, stdout=out, stderr="")
        with mock.patch.object(fetch.subprocess, "run", return_value=done("245.5\n")):
            self.assertEqual(fetch.page_duration("https://x.com/a/status/1"), 245.5)
        with mock.patch.object(fetch.subprocess, "run", return_value=done("NA\n")):
            self.assertIsNone(fetch.page_duration("https://x.com/a/status/1"))
        with mock.patch.object(fetch.subprocess, "run", return_value=done("", 1)):
            self.assertIsNone(fetch.page_duration("https://x.com/a/status/1"))

    def test_main_returns_error_code(self):
        self.assertEqual(fetch.main(["/nope.mp4", "--root", self.root]), 1)


if __name__ == "__main__":
    unittest.main()
