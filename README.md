# motion-to-prompt

A Claude Code skill. Give it a screen recording, video or GIF of a web animation or
motion design, and it writes a precise prompt a coding agent can use to rebuild it.

```
/motion-to-prompt ~/Desktop/hero-animation.mov
/motion-to-prompt https://dribbble.com/shots/12345-hero-reveal
/motion-to-prompt https://example.com/clip.gif --start 2 --end 5
```

Output goes to `./motion-prompts/<name>/` in your project:

```
motion-prompts/hero-animation-20261015-171200/
  prompt.md        # the rebuild prompt (paste it into any coding agent)
  frames/          # keyframes the prompt references
  analysis.json    # measured timeline, motion regions, palette
```

## How it works

1. `scripts/fetch.py` resolves the input: a local path, a direct media URL (via `curl`),
   or a page URL (via `yt-dlp`).
2. `scripts/analyze.py` measures the motion with `ffmpeg` and plain Python, frame by frame.
   It finds motion and rest segments with millisecond timings, where on screen the motion
   happens, a speed profile that hints at the easing, and whether the clip loops. It also
   pulls out the keyframes and the colour palette.
3. Claude looks at the keyframes, works out what each element is doing, detects your
   project's stack (React + Motion, GSAP, Tailwind, …) and writes `prompt.md`.

Timings in the prompt are measured. Element identity and easing curves are inferred, and
the prompt labels them that way.

## Install

```bash
ln -s "$(pwd)/motion-to-prompt" ~/.claude/skills/motion-to-prompt
```

Limits: videos longer than 3 minutes are rejected (page links are checked before
anything is downloaded). Within a video, the first 60 seconds are analysed unless you
pass `--start/--end`.

Requirements: `ffmpeg`, Python 3.9+. `yt-dlp` is optional and only needed for page URLs
(X, Dribbble, YouTube).

## Tests

```bash
python3 -m unittest discover -s motion-to-prompt/tests -v
```
