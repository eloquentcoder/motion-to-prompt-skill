import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import analyze  # noqa: E402

FPS = 30
FRAME_MS = 1000 / FPS


def make_clip(path, x_expr, duration=2, fps=FPS):
    """White 320x180 canvas with a 40x40 red box whose x follows x_expr(t)."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=white:s=320x180:d={duration}:r={fps}",
         "-f", "lavfi", "-i", f"color=c=red:s=40x40:d={duration}:r={fps}",
         "-filter_complex", f"[0][1]overlay=x='{x_expr}':y=70:eval=frame:shortest=1",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


def make_fade_clip(path, start=0.5, length=1.5, duration=3, fps=60):
    """White canvas; a black 120x40 bar fades in from `start` over `length` seconds."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=white:s=320x180:d={duration}:r={fps}",
         "-f", "lavfi", "-i", f"color=c=black:s=120x40:d={duration}:r={fps}",
         "-filter_complex",
         f"[1]format=rgba,fade=t=in:st={start}:d={length}:alpha=1[b];[0][b]overlay=100:70:shortest=1",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


def make_pop_clip(path):
    """The MOVE_ONCE box, plus a 60x30 blue badge that pops in at 0.8s mid-move."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=white:s=320x180:d=2:r={FPS}",
         "-f", "lavfi", "-i", f"color=c=red:s=40x40:d=2:r={FPS}",
         "-f", "lavfi", "-i", f"color=c=blue:s=60x30:d=2:r={FPS}",
         "-filter_complex",
         f"[0][1]overlay=x='{MOVE_ONCE}':y=70:eval=frame[a];"
         "[a][2]overlay=x=240:y=10:enable='gte(t,0.8)':shortest=1",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


# Rests 0–0.5s, moves 0.5–1.2s, rests to 2s.
MOVE_ONCE = "if(lt(t,0.5),20,if(lt(t,1.2),20+(t-0.5)/0.7*200,220))"
LOOPING = "140+100*sin(2*PI*t/2)"
# Cubic ease-out / ease-in from x=20 to x=260 between 0.5s and 1.5s.
EASE_OUT = "if(lt(t,0.5),20,if(lt(t,1.5),20+240*(1-pow(1.5-t,3)),260))"
EASE_IN = "if(lt(t,0.5),20,if(lt(t,1.5),20+240*pow(t-0.5,3),260))"


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class AnalyzeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.once = os.path.join(cls.tmp, "once.mp4")
        cls.loop = os.path.join(cls.tmp, "loop.mp4")
        cls.static = os.path.join(cls.tmp, "static.mp4")
        cls.gif = os.path.join(cls.tmp, "once.gif")
        make_clip(cls.once, MOVE_ONCE)
        make_clip(cls.loop, LOOPING)
        make_clip(cls.static, "20")
        cls.ease_out = os.path.join(cls.tmp, "ease_out.mp4")
        cls.ease_in = os.path.join(cls.tmp, "ease_in.mp4")
        make_clip(cls.ease_out, EASE_OUT, duration=2.5, fps=60)
        make_clip(cls.ease_in, EASE_IN, duration=2.5, fps=60)
        cls.pop = os.path.join(cls.tmp, "pop.mp4")
        make_pop_clip(cls.pop)
        cls.fade = os.path.join(cls.tmp, "fade.mp4")
        make_fade_clip(cls.fade)
        gif_src = os.path.join(cls.tmp, "once25.mp4")
        make_clip(gif_src, MOVE_ONCE, fps=25)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", gif_src, cls.gif], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def out(self, name):
        return os.path.join(self.tmp, "out-" + name)

    def test_single_motion_segment_timing(self):
        result = analyze.analyze(self.once, self.out("once"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1)
        self.assertAlmostEqual(motion[0]["start_ms"], 500, delta=FRAME_MS)
        self.assertAlmostEqual(motion[0]["end_ms"], 1200, delta=FRAME_MS)
        self.assertEqual([s["type"] for s in result["segments"]], ["rest", "motion", "rest"])
        self.assertFalse(result["loops"])

    def test_motion_region_tracks_box_path(self):
        result = analyze.analyze(self.once, self.out("region"))
        region = next(s for s in result["segments"] if s["type"] == "motion")["region"]
        # Box travels x 20→260 (of 320), y 70→110 (of 180).
        self.assertAlmostEqual(region["x"], 20 / 320, delta=0.03)
        self.assertAlmostEqual(region["x"] + region["w"], 260 / 320, delta=0.03)
        self.assertAlmostEqual(region["y"], 70 / 180, delta=0.03)
        self.assertAlmostEqual(region["h"], 40 / 180, delta=0.04)

    def test_keyframes_written_and_described(self):
        out = self.out("frames")
        result = analyze.analyze(self.once, out)
        # start, motion start, mid, end, clip end
        self.assertEqual(len(result["keyframes"]), 5)
        for kf in result["keyframes"]:
            self.assertTrue(os.path.isfile(os.path.join(out, kf["file"])), kf["file"])
        self.assertEqual(result["keyframes"][0]["label"], "start")
        self.assertEqual(result["keyframes"][-1]["label"], "end")
        with open(os.path.join(out, "analysis.json")) as fh:
            self.assertEqual(json.load(fh)["motion_count"], 1)

    def test_palette_contains_background_and_box(self):
        result = analyze.analyze(self.once, self.out("palette"))
        hexes = [p["hex"] for p in result["palette"]]
        r, g, b = (int(hexes[0][i:i + 2], 16) for i in (1, 3, 5))
        self.assertTrue(min(r, g, b) > 240, f"dominant colour should be white, got {hexes[0]}")
        self.assertTrue(any(int(h[1:3], 16) > 200 and int(h[3:5], 16) < 80 for h in hexes),
                        f"expected a red entry in {hexes}")

    def test_looping_clip_detected(self):
        result = analyze.analyze(self.loop, self.out("loop"))
        self.assertTrue(result["loops"])

    def test_static_clip_has_no_motion(self):
        result = analyze.analyze(self.static, self.out("static"))
        self.assertEqual(result["motion_count"], 0)
        self.assertEqual(analyze.main([self.static, "--out", self.out("static-cli")]), 3)

    def test_gif_input(self):
        result = analyze.analyze(self.gif, self.out("gif"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1)
        self.assertAlmostEqual(motion[0]["start_ms"], 500, delta=2 * FRAME_MS)
        self.assertAlmostEqual(motion[0]["end_ms"], 1200, delta=2 * FRAME_MS)

    def test_slow_fade_detected_as_one_segment(self):
        # Per-frame change (~3 grey levels at 60fps) is below PIXEL_THRESHOLD.
        result = analyze.analyze(self.fade, self.out("fade"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1, result["segments"])
        self.assertAlmostEqual(motion[0]["start_ms"], 500, delta=50)
        self.assertAlmostEqual(motion[0]["end_ms"], 2000, delta=50)
        self.assertEqual(motion[0]["kind"], "transition")
        self.assertTrue(motion[0]["subtle"])
        self.assertIn("linear", motion[0]["easing_hint"])
        region = motion[0]["region"]
        self.assertAlmostEqual(region["x"], 100 / 320, delta=0.03)
        self.assertAlmostEqual(region["w"], 120 / 320, delta=0.03)

    def test_ease_out_is_one_segment_with_exact_start(self):
        result = analyze.analyze(self.ease_out, self.out("ease-out"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1, result["segments"])
        self.assertAlmostEqual(motion[0]["start_ms"], 500, delta=17)
        # The last stretch moves less than an analysis pixel; it must still end late.
        self.assertGreater(motion[0]["end_ms"], 1250)
        self.assertIn("ease-out", motion[0]["easing_hint"])

    def test_ease_in_is_one_segment_with_exact_end(self):
        result = analyze.analyze(self.ease_in, self.out("ease-in"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1, result["segments"])
        self.assertAlmostEqual(motion[0]["end_ms"], 1500, delta=17)
        self.assertLess(motion[0]["start_ms"], 750)
        self.assertIn("ease-in", motion[0]["easing_hint"])

    def test_instant_change_inside_motion_reported_as_jump(self):
        result = analyze.analyze(self.pop, self.out("pop"))
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1, result["segments"])
        jumps = motion[0]["jumps"]
        self.assertEqual(len(jumps), 1, jumps)
        self.assertAlmostEqual(jumps[0]["t_ms"], 800, delta=FRAME_MS)
        self.assertAlmostEqual(jumps[0]["region"]["x"], 240 / 320, delta=0.03)
        self.assertAlmostEqual(jumps[0]["region"]["y"], 10 / 180, delta=0.03)

    def test_smooth_motion_has_no_jumps(self):
        result = analyze.analyze(self.once, self.out("no-jumps"))
        motion = next(s for s in result["segments"] if s["type"] == "motion")
        self.assertEqual(motion["jumps"], [])

    def test_translation_not_marked_subtle(self):
        result = analyze.analyze(self.once, self.out("not-subtle"))
        motion = next(s for s in result["segments"] if s["type"] == "motion")
        self.assertFalse(motion["subtle"])
        self.assertIn("linear", motion["easing_hint"])

    def test_range_trimming(self):
        result = analyze.analyze(self.once, self.out("trim"), start=1.0, end=2.0)
        motion = [s for s in result["segments"] if s["type"] == "motion"]
        self.assertEqual(len(motion), 1)
        self.assertEqual(motion[0]["start_ms"], 0)
        self.assertAlmostEqual(motion[0]["end_ms"], 200, delta=FRAME_MS)

    def test_video_over_length_limit_rejected(self):
        with mock.patch.object(analyze, "MAX_VIDEO_SECONDS", 1):
            with self.assertRaisesRegex(analyze.AnalyzeError, "limit is 0:01"):
                analyze.analyze(self.once, self.out("too-long"), start=0.5, end=1.0)

    def test_bad_range_rejected(self):
        with self.assertRaises(analyze.AnalyzeError):
            analyze.analyze(self.once, self.out("bad"), start=5.0)
        with self.assertRaises(analyze.AnalyzeError):
            analyze.analyze(self.once, self.out("bad"), start=1.0, end=0.5)


class PureFunctionTest(unittest.TestCase):
    def test_easing_hint(self):
        self.assertIn("ease-out", analyze.easing_hint([1, 0.8, 0.5, 0.3, 0.1, 0.05]))
        self.assertIn("ease-in", analyze.easing_hint([0.05, 0.1, 0.3, 0.5, 0.8, 1]))
        self.assertIn("linear", analyze.easing_hint([1, 0.95, 0.9, 1, 0.92, 0.97]))
        self.assertIn("ease-in-out", analyze.easing_hint([0.1, 0.5, 1, 1, 0.5, 0.1]))

    def test_one_frame_rest_inside_motion_is_merged(self):
        counts = [0, 0, 500, 500, 0, 500, 500, 0, 0]
        flags, _ = analyze.classify_diffs(counts, 14400)
        self.assertEqual(flags, [False, False, True, True, True, True, True, False, False])

    def test_one_frame_noise_dropped_but_cut_kept(self):
        noise, _ = analyze.classify_diffs([0, 0, 20, 0, 0], 14400)
        self.assertEqual(noise, [False] * 5)
        cut, _ = analyze.classify_diffs([0, 0, 5000, 0, 0], 14400)
        self.assertEqual(cut, [False, False, True, False, False])

    def test_short_rest_between_motion_is_merged(self):
        counts = [0, 0, 500, 0, 0, 500, 0, 0, 0, 0, 500, 500, 0]
        flags, _ = analyze.classify_diffs(counts, 14400, max_gap=2)
        self.assertEqual(flags[2:6], [True] * 4)
        self.assertEqual(flags[6:10], [False] * 4)

    def test_gradual_change_flags_motion(self):
        counts = [0] * 8
        gradual = [0, 0, 300, 300, 300, 2, 0, 0]
        flags, _ = analyze.classify_diffs(counts, 14400, gradual)
        self.assertEqual(flags, [False, False, True, True, True, False, False, False])

    def test_gradual_mask_ignores_flicker(self):
        # Pixel 0 fades 0→30 in steps of 3; pixel 1 flickers 50↔53 like encoder noise.
        frames = [bytes([3 * k, 50 + 3 * (k % 2)]) for k in range(11)]
        moved = analyze.abs_diff(frames[4], frames[5]).translate(analyze.MOVED_TABLE)
        self.assertEqual(moved, b"\x01\x01")
        self.assertEqual(analyze.gradual_mask(frames, 4, moved, window=6), b"\x01\x00")

    def test_rank_colors_merges_near_shades(self):
        # Three encoder shades of one navy background, plus a small indigo button.
        raw = (bytes([12, 22, 41]) * 500 + bytes([3, 12, 32]) * 300 + bytes([23, 33, 52]) * 200
               + bytes([99, 102, 241]) * 30 + bytes([255, 255, 255]) * 20)
        colors = analyze.rank_colors(raw)
        self.assertEqual(len(colors), 3, colors)
        self.assertEqual(colors[0]["share"], round(1000 / 1050, 3))
        self.assertEqual(colors[1]["hex"], "#6366f1")
        self.assertEqual(colors[2]["hex"], "#ffffff")

    def test_resample_averages_bins(self):
        self.assertEqual(analyze.resample([1, 3, 5, 7], 2), [2, 6])
        self.assertEqual(analyze.resample([1, 2], 12), [1, 2])

    def test_keyframe_cap(self):
        segments = []
        for i in range(15):
            segments.append({"type": "motion", "start_frame": i * 10, "end_frame": i * 10 + 6})
        picks = analyze.pick_keyframes(segments, 200)
        self.assertLessEqual(len(picks), analyze.MAX_KEYFRAMES)
        self.assertEqual(picks[0][0], 0)
        self.assertEqual(picks[-1][0], 200)


if __name__ == "__main__":
    unittest.main()
