---
name: motion-to-prompt
description: Turn a screen recording, video, or GIF of a web animation or motion design into a precise prompt a coding agent can use to rebuild it in code. Use when the user passes a video/GIF path or URL (Dribbble, X/Twitter, YouTube, direct .mp4/.gif) and wants to replicate, recreate, copy, or "translate" the animation, or asks for a prompt describing it.
argument-hint: <url-or-path> [--start <sec>] [--end <sec>]
---

# motion-to-prompt

Analyse a recording of a web animation and write a rebuild prompt for a coding agent to
`./motion-prompts/<name>/prompt.md`, next to the keyframes it references.

The scripts **measure** (timings, motion regions, colours). You **interpret** (what the
elements are, what transforms they undergo, which easing curve, how to build it). Keep
those two apart in the output: never present an inference as a measurement.

Scripts live next to this file: `<skill-dir>/scripts/`. Use their absolute paths; run
them from the user's project root so `motion-prompts/` lands there.

## Workflow

### 1. Fetch

```bash
python3 <skill-dir>/scripts/fetch.py "<url-or-path>"
```

Prints JSON: `{"path": ..., "out_dir": ..., "name": ...}`. On a non-zero exit, show the
user the error (it includes an install hint or a "download it manually" suggestion) and
stop. Don't try workarounds like scraping the page.

Videos longer than **3:00** are rejected (page URLs are checked before downloading).
Don't get around the limit by yourself. Tell the user, and ask which part holds the
animation. If they give you a local file and a time, you may trim that part out with
`ffmpeg -ss <start> -t <length> -i in.mp4 -c copy clip.mp4` (keep it under 3:00) and
run the workflow on the clip.

### 2. Analyse

```bash
python3 <skill-dir>/scripts/analyze.py "<path>" --out "<out_dir>" [--start S] [--end E]
```

Pass `--start/--end` through if the user gave them.
- Exit 3 means no motion was detected. Tell the user and stop; don't write a prompt.
- If it prints a warning that only the first 60s were analysed, mention it in your
  final message.

### 3. Study the analysis

Read `<out_dir>/analysis.json`, then **view every keyframe image** listed in `keyframes`
with the Read tool. Fields that matter:

- `segments`: alternating `rest`/`motion` stretches with measured `start_ms`,
  `duration_ms` and `region` (where on screen the change happened, as fractions of the
  frame). `kind: "cut"` means an instant change and no tween.
- `intensity_profile` / `easing_hint`: total pixel change per frame across a motion
  segment, in 12 bins. This is a proxy for speed (or for the rate of an opacity fade):
  front-loaded suggests ease-out, back-loaded suggests ease-in. With several elements
  overlapping in one segment it shows their sum, so check it against the frames.
- `jumps`: instants inside a motion segment where something changed in a single
  frame (an element popping in or out, a state swap), with `t_ms` and `region`. Write
  these as instant changes (`duration: 0` / `display` toggle), not tweens.
- `subtle: true`: a gradual change, usually an opacity fade, a colour shift or a slow
  drift, as opposed to clear movement. The keyframes will show which.
- Eased tails: the last stretch of an ease-out (or the first of an ease-in) moves less
  than a pixel, so the measured span stops where *visible* motion stops. When you turn a
  measured ease-out span into a tween duration, add roughly 10–20% and say so (≈).
- `loops`, `palette`, `video`.

One motion segment often holds **several overlapping animations**, e.g. staggered words
or a card moving while its shadow grows. Use the keyframes and the region to separate
them, and estimate per-element offsets in proportion to the segment's measured span. If
you need more frames to resolve a stagger or an easing curve, pull them yourself:

```bash
# <seconds> = range.start_s + t_ms / 1000 (analysis times are relative to the range)
ffmpeg -v error -ss <seconds> -i "<path>" -frames:v 1 "<out_dir>/frames/extra_<t>ms.png"
```

### 4. Detect the stack

Follow `references/stack-detection.md`.

### 5. Write the prompt

Write `<out_dir>/prompt.md` following `references/prompt-template.md` exactly. Rules:
- Every timing in the table is taken from or derived from `analysis.json`. Round to
  whole milliseconds; don't invent precision you don't have.
- Every easing is concrete: a `cubic-bezier(...)`, a named library ease
  (`power3.out`), or a spring config. Never write "smooth" or "natural".
- Reference frames by their relative path (`frames/03_500ms.png`).
- Anything the recording can't show (the trigger, hover states, responsive behaviour) is
  labelled **Assumption**.

### 6. Report

Give the user the path to `prompt.md`, a 2–3 line summary of the animation, the detected
stack, and any warnings. Don't paste the whole prompt unless they ask for it.
