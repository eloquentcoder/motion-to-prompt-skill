#!/usr/bin/env python3
"""Resolve a URL or local path to a local video file.

Usage:
    fetch.py <url-or-path> [--root motion-prompts]

Creates <root>/<name>/ and prints JSON to stdout:
    {"path": "<local video>", "out_dir": "<root>/<name>", "name": "<name>"}

Errors go to stderr with a non-zero exit code.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse

from analyze import MAX_VIDEO_SECONDS, too_long_message

DIRECT_MEDIA_EXTS = (".mp4", ".mov", ".webm", ".gif", ".m4v", ".mkv")


class FetchError(Exception):
    pass


def is_url(value):
    return value.startswith(("http://", "https://"))


def is_direct_media_url(url):
    return urlparse(url).path.lower().endswith(DIRECT_MEDIA_EXTS)


def slugify(text, max_len=40):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "recording"


def base_name(value):
    if is_url(value):
        parsed = urlparse(value)
        segments = [s for s in parsed.path.split("/") if s]
        raw = os.path.splitext(unquote(segments[-1]))[0] if segments else parsed.netloc
    else:
        raw = os.path.splitext(os.path.basename(value))[0]
    return slugify(raw)


def make_name(value, now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return f"{base_name(value)}-{stamp}"


def require_tool(tool, hint):
    if shutil.which(tool) is None:
        raise FetchError(f"'{tool}' is not installed. Install it with: {hint}")


def validate_video(path):
    """Check `path` is a readable video and return its duration in seconds."""
    require_tool("ffprobe", "brew install ffmpeg")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type:format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    if result.returncode != 0 or not data.get("streams"):
        detail = result.stderr.strip().splitlines()[-1:] or ["no video stream found"]
        raise FetchError(f"Not a readable video/GIF: {path} ({detail[0]})")
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise FetchError(f"Could not determine the duration of {path}")


def check_length(duration):
    if duration is not None and duration > MAX_VIDEO_SECONDS:
        raise FetchError(too_long_message(duration))


def page_duration(url):
    """Ask yt-dlp for the video's length without downloading it (None if unknown)."""
    result = subprocess.run(
        ["yt-dlp", "--skip-download", "--no-playlist", "--print", "duration", url],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()
    try:
        return float(lines[-1]) if result.returncode == 0 and lines else None
    except ValueError:
        return None


def download_direct(url, out_dir):
    require_tool("curl", "brew install curl")
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    dest = os.path.join(out_dir, "source" + ext)
    result = subprocess.run(["curl", "-L", "-f", "-s", "-S", "-o", dest, url],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise FetchError(
            f"Download failed ({result.stderr.strip()}). "
            "Download the file manually and pass its local path instead."
        )
    return dest


def download_page(url, out_dir):
    template = os.path.join(out_dir, "source.%(ext)s")
    result = subprocess.run(
        ["yt-dlp", "-f", "bv*[ext=mp4]/bv*/b", "--no-playlist", "--no-simulate",
         "--print", "after_move:filepath", "-o", template, url],
        capture_output=True, text=True,
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    if result.returncode != 0 or not lines or not os.path.exists(lines[-1]):
        err = result.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise FetchError(
            f"Could not download video from page ({err[0]}). The post may be "
            "private or behind a login. Download it manually and pass the local path."
        )
    return lines[-1]


def fetch(value, root="motion-prompts", now=None):
    if not is_url(value):
        path = os.path.abspath(os.path.expanduser(value))
        if not os.path.isfile(path):
            raise FetchError(f"File not found: {value}")
    name = make_name(value, now)
    out_dir = os.path.abspath(os.path.join(root, name))
    created = not os.path.exists(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    try:
        if is_url(value) and is_direct_media_url(value):
            path = download_direct(value, out_dir)
        elif is_url(value):
            require_tool("yt-dlp", "brew install yt-dlp  (or: pipx install yt-dlp)")
            check_length(page_duration(value))  # before downloading anything
            path = download_page(value, out_dir)
        check_length(validate_video(path))
    except FetchError:
        # Remove only what this run created; a local input file lives elsewhere.
        if created:
            shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return {"path": os.path.abspath(path), "out_dir": out_dir, "name": name}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="URL or local path to a video/GIF")
    parser.add_argument("--root", default="motion-prompts",
                        help="output root folder (default: motion-prompts)")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(fetch(args.input, args.root)))
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
