#!/usr/bin/env python3
"""Measure the motion in a video/GIF and extract keyframes.

Usage:
    analyze.py <video> --out <dir> [--start SEC] [--end SEC]

Writes <dir>/analysis.json and <dir>/frames/NN_<t>ms.png.
Exit codes: 0 ok, 1 error, 3 no motion detected (analysis.json still written).
"""
import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from operator import sub

ANALYSIS_WIDTH = 160      # frames are downscaled to this width for measurement
PIXEL_THRESHOLD = 12      # gray-level change for a pixel to count as "changed"
NOISE_LEVEL = 1           # encoders flicker static pixels by ±1; more than that is real change
MIN_CHANGED_FRACTION = 0.0005
CUT_FRACTION = 0.01       # a 1-frame change bigger than this is a cut, not noise
JUMP_RATIO = 2.5          # a frame changing this many times more than both neighbours
MAX_SECONDS = 60          # longest stretch analysed in one run
MAX_VIDEO_SECONDS = 180   # longest video accepted at all
MAX_KEYFRAMES = 20
PALETTE_SIZE = 6
MERGE_DISTANCE = 32       # RGB distance under which two palette colours count as one
PROFILE_POINTS = 12
SLOW_WINDOW_MS = 200      # span over which a gradual change (fade) must add up
MIN_REST_MS = 100         # shorter pauses between motion are part of the same motion

CHANGED_TABLE = bytes(1 if v > PIXEL_THRESHOLD else 0 for v in range(256))
MOVED_TABLE = bytes(1 if v > NOISE_LEVEL else 0 for v in range(256))


class AnalyzeError(Exception):
    pass


def format_duration(seconds):
    seconds = math.ceil(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def too_long_message(duration):
    return (f"Video is {format_duration(duration)} long; the limit is "
            f"{format_duration(MAX_VIDEO_SECONDS)}. Trim it to the part with the animation "
            "first, e.g.: ffmpeg -ss <start-sec> -t <length-sec> -i in.mp4 -c copy clip.mp4")


def run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip().splitlines()[-1:]
        raise AnalyzeError(f"{cmd[0]} failed: {err[0] if err else 'unknown error'}")
    return result.stdout


def parse_rate(rate):
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def probe(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,duration"
               ":format=duration", "-of", "json", path])
    data = json.loads(out)
    if not data.get("streams"):
        raise AnalyzeError(f"No video stream in {path}")
    stream = data["streams"][0]
    fps = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
    duration = None
    for raw in (data.get("format", {}).get("duration"), stream.get("duration")):
        try:
            duration = float(raw)
            break
        except (TypeError, ValueError):
            continue
    if not duration:
        raise AnalyzeError(f"Could not determine duration of {path}")
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": fps, "duration": duration}


def choose_fps(source_fps):
    if not source_fps or source_fps <= 0:
        return 30
    return int(min(60, max(10, round(source_fps))))


def decode(src, start, length, vf, pix_fmt):
    """Decode the analysed range at constant fps through filter `vf` to raw bytes."""
    return run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                "-i", src, "-vf", vf, "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"])


def split_frames(raw, size):
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def abs_diff(a, b):
    return bytes(map(abs, map(sub, a, b)))


def diff_mask(a, b):
    return abs_diff(a, b).translate(CHANGED_TABLE)


def mask_and(a, b):
    return (int.from_bytes(a, "big") & int.from_bytes(b, "big")).to_bytes(len(a), "big")


def mask_or(a, b):
    return (int.from_bytes(a, "big") | int.from_bytes(b, "big")).to_bytes(len(a), "big")


def gradual_mask(frames, i, moved, window):
    """Pixels moving in transition i (frame i → i+1) that also change clearly across
    the window before or after it: a fade or slow drift, not encoder flicker."""
    back = diff_mask(frames[max(0, i + 1 - window)], frames[i + 1])
    ahead = diff_mask(frames[i], frames[min(len(frames) - 1, i + window)])
    return mask_and(moved, mask_or(back, ahead))


def mask_bbox(mask, w, h):
    top = bottom = None
    left, right = w, -1
    for r in range(h):
        row = mask[r * w:(r + 1) * w]
        first = row.find(1)
        if first == -1:
            continue
        top = r if top is None else top
        bottom = r
        left = min(left, first)
        right = max(right, row.rfind(1))
    if top is None:
        return None
    return (left, top, right, bottom)


def union_bbox(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def runs(flags):
    """[(value, start_index, end_index_inclusive), ...]"""
    out = []
    for i, f in enumerate(flags):
        if out and out[-1][0] == f:
            out[-1] = (f, out[-1][1], i)
        else:
            out.append((f, i, i))
    return out


def classify_diffs(counts, n_pixels, gradual=None, max_gap=1):
    """Flag each frame-to-frame transition as motion.

    counts:  pixels changed by more than PIXEL_THRESHOLD since the previous frame.
    gradual: pixels changing a little that add up to a clear change (see gradual_mask).
    """
    min_changed = max(3, int(MIN_CHANGED_FRACTION * n_pixels))
    flags = [c >= min_changed for c in counts]
    if gradual is not None:
        flags = [f or g >= min_changed for f, g in zip(flags, gradual)]
    # Merge short rests between motion (sub-pixel steps of an eased tail land on
    # alternate frames), then drop 1-frame noise blips.
    for value, s, e in runs(flags):
        if not value and e - s + 1 <= max_gap and 0 < s and e < len(flags) - 1:
            flags[s:e + 1] = [True] * (e - s + 1)
    for value, s, e in runs(flags):
        if value and s == e and counts[s] < CUT_FRACTION * n_pixels:
            flags[s] = False
    return flags, min_changed


def resample(values, points):
    """Average `values` into `points` equal bins (smooths per-frame quantisation noise)."""
    n = len(values)
    if n <= points:
        return list(values)
    bounds = [round(i * n / points) for i in range(points + 1)]
    return [sum(values[lo:hi]) / (hi - lo) for lo, hi in zip(bounds, bounds[1:])]


def easing_hint(profile):
    if len(profile) < 3:
        return "too short to judge (likely a cut or very fast transition)"
    total = sum(profile) or 1
    centroid = sum(i * v for i, v in enumerate(profile)) / total / (len(profile) - 1)
    peak = max(profile) or 1
    if min(profile) / peak > 0.7:
        return "roughly constant speed (linear)"
    if centroid < 0.42:
        return "front-loaded: fast start, slow settle (ease-out / decelerate)"
    if centroid > 0.58:
        return "back-loaded: slow start, fast finish (ease-in / accelerate)"
    return "peaks in the middle (ease-in-out)"


def find_jumps(frames, diffs, counts, s, e, w, h, window, min_changed):
    """Interior transitions of motion run s..e where something changes instantly
    (an element popping in) on top of the ongoing motion. Returns [(i, bbox)]."""
    n_pixels = w * h
    jumps = []
    for i in range(s + 1, e):
        if counts[i] < 4 * min_changed:
            continue
        mask = diffs[i].translate(CHANGED_TABLE)
        moving = union_bbox([mask_bbox(diffs[j].translate(CHANGED_TABLE), w, h)
                             for j in (i - 1, i + 1)])
        changed = [p for p in range(n_pixels) if mask[p]]
        # New change outside the area that was already moving...
        pixels = [p for p in changed if not (
            moving and moving[0] <= p % w <= moving[2] and moving[1] <= p // w <= moving[3])]
        if len(pixels) < 4 * min_changed:
            # ...or, inside it, a frame that changes far more than its neighbours.
            around = max(counts[i - 1], counts[i + 1])
            if counts[i] < JUMP_RATIO * around or counts[i] - around < CUT_FRACTION * n_pixels:
                continue
            pixels = changed
        # A pop is stable before and after; a fade step that crossed the threshold isn't.
        before, after = frames[max(0, i - window)], frames[min(len(frames) - 1, i + 1 + window)]
        jump = sum(diffs[i][p] for p in pixels)
        drift = sum(abs(frames[i][p] - before[p]) + abs(after[p] - frames[i + 1][p])
                    for p in pixels)
        if drift > 0.3 * jump:
            continue
        box = (min(p % w for p in pixels), min(p // w for p in pixels),
               max(p % w for p in pixels), max(p // w for p in pixels))
        jumps.append((i, box))
    return jumps


def region(box, w, h):
    return {"x": round(box[0] / w, 3), "y": round(box[1] / h, 3),
            "w": round((box[2] - box[0] + 1) / w, 3),
            "h": round((box[3] - box[1] + 1) / h, 3)}


def build_segments(flags, counts, boxes, fps, w, h, n_pixels, sad=None, min_changed=0,
                   jumps=None):
    to_ms = lambda frame: round(frame * 1000 / fps)
    segments = []
    for value, s, e in runs(flags):
        seg = {"type": "motion" if value else "rest",
               "start_frame": s, "end_frame": e + 1,
               "start_ms": to_ms(s), "end_ms": to_ms(e + 1)}
        seg["duration_ms"] = seg["end_ms"] - seg["start_ms"]
        if value:
            seg_counts = counts[s:e + 1]
            speed = (sad or counts)[s:e + 1]
            binned = resample(speed, PROFILE_POINTS)
            peak = max(binned) or 1
            profile = [round(c / peak, 2) for c in binned]
            box = union_bbox(boxes[s:e + 1])
            seg["kind"] = "cut" if e == s else "transition"
            seg["subtle"] = sum(c < min_changed for c in seg_counts) * 2 > len(seg_counts)
            seg["peak_changed_pct"] = round(100 * max(seg_counts) / n_pixels, 2)
            seg["intensity_profile"] = profile
            seg["easing_hint"] = easing_hint(profile)
            if box:
                seg["region"] = region(box, w, h)
            # t_ms is the first frame showing the change.
            seg["jumps"] = [{"t_ms": to_ms(i + 1), "region": region(box, w, h)}
                            for i, box in (jumps(s, e) if jumps else [])]
        segments.append(seg)
    return segments


def pick_keyframes(segments, last_frame):
    labels = {}
    mids = []  # (segment length, frame, label)

    def add(frame, label):
        labels.setdefault(frame, []).append(label)

    add(0, "start")
    motion = [s for s in segments if s["type"] == "motion"]
    for i, seg in enumerate(motion, 1):
        add(seg["start_frame"], f"motion {i} start")
        mid = (seg["start_frame"] + seg["end_frame"]) // 2
        if mid not in (seg["start_frame"], seg["end_frame"]):
            mids.append((seg["end_frame"] - seg["start_frame"], mid, f"motion {i} mid"))
            add(mid, f"motion {i} mid")
        add(min(seg["end_frame"], last_frame), f"motion {i} end")
    add(last_frame, "end")

    for _, frame, label in sorted(mids):
        if len(labels) <= MAX_KEYFRAMES:
            break
        labels[frame].remove(label)
        if not labels[frame]:
            del labels[frame]

    frames = sorted(labels)
    if len(frames) > MAX_KEYFRAMES:
        inner = frames[1:-1]
        step = len(inner) / (MAX_KEYFRAMES - 2)
        frames = [frames[0]] + [inner[int(i * step)] for i in range(MAX_KEYFRAMES - 2)] + [frames[-1]]
    return [(f, " + ".join(labels[f])) for f in frames]


def extract_keyframes(src, start, length, fps, picks, frames_dir):
    os.makedirs(frames_dir, exist_ok=True)
    expr = "+".join(f"eq(n,{f})" for f, _ in picks)
    tmp_pattern = os.path.join(frames_dir, "tmp_%03d.png")
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", src, "-vf", f"fps={fps},select='{expr}'", "-fps_mode", "passthrough",
         tmp_pattern])
    written = sorted(f for f in os.listdir(frames_dir) if f.startswith("tmp_"))
    if len(written) != len(picks):
        raise AnalyzeError(f"Expected {len(picks)} keyframes, ffmpeg wrote {len(written)}")
    keyframes = []
    for idx, ((frame, label), tmp) in enumerate(zip(picks, written), 1):
        t_ms = round(frame * 1000 / fps)
        name = f"{idx:02d}_{t_ms}ms.png"
        os.replace(os.path.join(frames_dir, tmp), os.path.join(frames_dir, name))
        keyframes.append({"file": f"frames/{name}", "frame": frame, "t_ms": t_ms, "label": label})
    return keyframes


def palette(src, start, length, fps, last_frame, src_w, src_h):
    pw = ANALYSIS_WIDTH
    ph = max(2, round(pw * src_h / src_w / 2) * 2)
    sample = sorted({0, last_frame // 2, last_frame})
    expr = "+".join(f"eq(n,{f})" for f in sample)
    # Nearest-neighbour sampling keeps real colours; averaging invents in-between shades.
    raw = decode(src, start, length,
                 f"fps={fps},select='{expr}',scale={pw}:{ph}:flags=neighbor", "rgb24")
    return rank_colors(raw)


def rank_colors(raw):
    """Most common colours in packed RGB bytes, with near-identical shades merged."""
    buckets = {}
    for i in range(0, len(raw) - 2, 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        key = (r >> 4, g >> 4, b >> 4)
        acc = buckets.setdefault(key, [0, 0, 0, 0])
        acc[0] += 1
        acc[1] += r
        acc[2] += g
        acc[3] += b
    total = sum(a[0] for a in buckets.values()) or 1
    kept = []  # [count, (r, g, b)]
    for count, r, g, b in sorted(buckets.values(), reverse=True):
        rgb = (r // count, g // count, b // count)
        near = next((k for k in kept if sum((x - y) ** 2 for x, y in zip(k[1], rgb))
                     <= MERGE_DISTANCE ** 2), None)
        if near:
            near[0] += count
        else:
            kept.append([count, rgb])
    kept.sort(key=lambda k: k[0], reverse=True)
    return [{"hex": "#{:02x}{:02x}{:02x}".format(*rgb), "share": round(count / total, 3)}
            for count, rgb in kept[:PALETTE_SIZE] if count / total >= 0.001]


def analyze(src, out_dir, start=0.0, end=None):
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise AnalyzeError(f"'{tool}' is not installed. Install it with: brew install ffmpeg")
    if not os.path.isfile(src):
        raise AnalyzeError(f"File not found: {src}")

    info = probe(src)
    if info["duration"] > MAX_VIDEO_SECONDS:
        raise AnalyzeError(too_long_message(info["duration"]))
    warnings = []
    if start < 0 or start >= info["duration"]:
        raise AnalyzeError(f"--start {start} is outside the video (duration {info['duration']:.2f}s)")
    if end is not None:
        if end <= start:
            raise AnalyzeError("--end must be greater than --start")
        length = min(end, info["duration"]) - start
    else:
        length = info["duration"] - start
        if length > MAX_SECONDS:
            warnings.append(f"Video is {length:.1f}s long; only the first {MAX_SECONDS}s were "
                            "analysed. Use --start/--end to pick the part you want.")
            length = MAX_SECONDS

    fps = choose_fps(info["fps"])
    w = ANALYSIS_WIDTH
    h = max(2, round(w * info["height"] / info["width"] / 2) * 2)
    n_pixels = w * h
    frames = split_frames(decode(src, start, length, f"fps={fps},scale={w}:{h},format=gray", "gray"),
                          n_pixels)
    if len(frames) < 2:
        raise AnalyzeError("Video has fewer than 2 frames in the selected range")

    window = max(2, round(fps * SLOW_WINDOW_MS / 1000))
    diffs = [abs_diff(a, b) for a, b in zip(frames, frames[1:])]
    counts = [d.translate(CHANGED_TABLE).count(1) for d in diffs]
    sad = [sum(d) for d in diffs]
    min_changed = max(3, int(MIN_CHANGED_FRACTION * n_pixels))
    gradual_masks = {}
    gradual = [0] * len(diffs)
    for i, d in enumerate(diffs):
        if counts[i] >= min_changed:
            continue
        moved = d.translate(MOVED_TABLE)
        if moved.count(1) >= min_changed:
            gradual_masks[i] = gradual_mask(frames, i, moved, window)
            gradual[i] = gradual_masks[i].count(1)
    max_gap = max(1, round(fps * MIN_REST_MS / 1000))
    flags, min_changed = classify_diffs(counts, n_pixels, gradual, max_gap)
    boxes = []
    for i, (d, f) in enumerate(zip(diffs, flags)):
        if not f:
            boxes.append(None)
        elif i in gradual_masks:
            boxes.append(mask_bbox(gradual_masks[i], w, h))
        else:
            boxes.append(mask_bbox(d.translate(CHANGED_TABLE), w, h))
    segments = build_segments(
        flags, counts, boxes, fps, w, h, n_pixels, sad, min_changed,
        jumps=lambda s, e: find_jumps(frames, diffs, counts, s, e, w, h, window, min_changed))
    motion = [s for s in segments if s["type"] == "motion"]
    last_frame = len(frames) - 1

    loops = False
    if motion:
        wrap = diff_mask(frames[-1], frames[0]).count(1)
        typical = statistics.median(c for c, f in zip(counts, flags) if f)
        loops = wrap < min_changed or wrap <= 1.5 * typical

    os.makedirs(out_dir, exist_ok=True)
    keyframes = extract_keyframes(src, start, length, fps, pick_keyframes(segments, last_frame),
                                  os.path.join(out_dir, "frames"))

    result = {
        "source": os.path.abspath(src),
        "range": {"start_s": round(start, 3), "end_s": round(start + length, 3)},
        "video": {"width": info["width"], "height": info["height"],
                  "fps_source": round(info["fps"], 3), "fps_analyzed": fps,
                  "duration_s": round(info["duration"], 3), "frames_analyzed": len(frames),
                  "analysis_resolution": [w, h]},
        "loops": loops,
        "motion_count": len(motion),
        "total_motion_ms": sum(s["duration_ms"] for s in motion),
        "first_motion_ms": motion[0]["start_ms"] if motion else None,
        "last_motion_end_ms": motion[-1]["end_ms"] if motion else None,
        "segments": segments,
        "keyframes": keyframes,
        "palette": palette(src, start, length, fps, last_frame, info["width"], info["height"]),
        "warnings": warnings,
        "notes": ("Times are relative to range.start_s. Segment timings are measured; "
                  "intensity_profile/easing_hint come from the total pixel change per "
                  "frame and are only a hint — confirm against the keyframes. "
                  f"subtle=true means a gradual change (fade, colour shift, slow drift) "
                  f"that only adds up to a visible change over ~{SLOW_WINDOW_MS}ms."),
    }
    with open(os.path.join(out_dir, "analysis.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--start", type=float, default=0.0, help="start of range (seconds)")
    parser.add_argument("--end", type=float, default=None, help="end of range (seconds)")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.video, args.out, args.start, args.end)
    except AnalyzeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"analysis: {os.path.join(args.out, 'analysis.json')}")
    print(f"motion segments: {result['motion_count']}, keyframes: {len(result['keyframes'])}, "
          f"loops: {result['loops']}")
    if result["motion_count"] == 0:
        print("error: no motion detected — the clip looks static. Check the file or trim "
              "with --start/--end.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
