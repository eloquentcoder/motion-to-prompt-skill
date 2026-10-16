# motion-to-prompt — Design

**Date:** 2026-10-15
**Status:** Approved in brainstorming, pending spec review

## Purpose

A Claude Code skill that takes a screen recording, video, or GIF of a motion design / web animation and produces a prompt that lets a **coding agent** (Claude Code, Cursor, v0, …) rebuild the animation in code.

Usage:

```
/motion-to-prompt <url-or-path> [--start <sec>] [--end <sec>]
```

Output is written to `./motion-prompts/<name>/` at the root of the current project.

## Decisions

| Topic | Decision |
|---|---|
| Consumer of the prompt | A coding agent (not an AI video generator) |
| Input | Local file path **or** URL (direct media link or page URL) |
| Stack | Detect from current project; fall back to stack-agnostic spec + one recommended library |
| Output | `prompt.md` + `frames/` keyframes + `analysis.json` |
| Architecture | Skill (`SKILL.md`) + Python helper scripts; Claude does interpretation, scripts do measurement |

## Architecture

```
motion-to-prompt/
  SKILL.md                 # workflow Claude follows
  scripts/
    fetch.py               # URL/path → local video file
    analyze.py             # video → analysis.json + keyframes
  references/
    prompt-template.md     # structure of the generated prompt
    stack-detection.md     # how to detect stack & adapt the prompt
  tests/
    test_fetch.py
    test_analyze.py        # uses synthetic ffmpeg-generated fixtures
```

Installed by copying or symlinking the `motion-to-prompt/` directory into `~/.claude/skills/`.

### Data flow

1. **`fetch.py <input> --out-dir <dir>`**
   - Existing local path → validated with `ffprobe`, path printed as-is.
   - URL ending in `.mp4/.mov/.webm/.gif` → downloaded with `curl`.
   - Any other URL → downloaded with `yt-dlp` (best video, no audio needed).
   - Prints the absolute path of the local file on stdout. Errors go to stderr with a non-zero exit.
2. **`analyze.py <video> --out <motion-prompts/name> [--start s] [--end s]`**
   - Decodes the range at a constant fps through ffmpeg's `fps` filter (handles GIF variable frame delays); no working copy is written.
   - **Metadata:** duration, fps, width, height, `loops` (first vs last frame difference below the threshold).
   - **Motion timeline:** per-frame differences on downscaled grayscale raw frames (`ffmpeg -f rawvideo -pix_fmt gray`, 160px wide). A transition is motion if enough pixels change by more than 12 grey levels, or if pixels keep changing a little (more than encoder ±1 noise) and add up to a clear change over a 200ms window (fades, eased tails; flagged `subtle`). Rests shorter than 100ms between motion merge into it; 1-frame blips are dropped unless large enough to be a cut. Each `motion` / `rest` segment has `start_ms`, `end_ms` and `duration_ms`; motion segments also carry an `intensity_profile` (sum of absolute differences per frame, 12 bins), an `easing_hint`, and `jumps` (single-frame pops inside the segment, with time and region).
   - **Motion regions:** a bounding box (as a fraction of the frame) of pixels that changed in each motion segment.
   - **Keyframes:** first frame, then the start/mid/end of each motion segment, then the last frame, deduplicated and capped at 20 (when over the cap, the mid frames of the shortest segments are dropped first). Saved full-resolution as `frames/NN_<t>ms.png`.
   - **Palette:** up to 6 dominant colours as hex, from 3 nearest-neighbour-downscaled frames, bucketed with near-identical shades (RGB distance ≤ 32) merged.
   - Writes `analysis.json` holding all of the above.
3. **Claude (per SKILL.md)**
   - Reads `analysis.json` and views the keyframes.
   - Identifies the elements, transform types (translate, scale, rotate, opacity, clip/mask, morph, stagger), and easing character.
   - Detects the stack following `references/stack-detection.md` (`package.json` deps: framer-motion/motion, gsap, react-spring, @react-three/fiber, lottie, tailwindcss; plus vue/svelte/plain HTML).
   - Writes `prompt.md` following `references/prompt-template.md`.

`<name>` = a slug of the filename or URL path + `-YYYYMMDD-HHMMSS`, so runs never overwrite each other.

## `prompt.md` structure

The header line states: *"Timings are measured from the recording; element identity and easing are inferred."*

1. **Goal**: one paragraph covering what it is and how it feels.
2. **Target stack**: the detected stack with its source (e.g. "detected from package.json"), or the agnostic fallback with a recommended library and the reason.
3. **Scene & layout**: elements, positions, approximate sizes (% of viewport), palette hex values, and typography as observed.
4. **Motion timeline table**: `# | Element | Property | From → To | Start | Duration | Easing | Ref frame`. Easing is always a concrete curve (e.g. `cubic-bezier(0.22,1,0.36,1)` or a spring config), never "smooth". Stagger intervals are noted explicitly.
5. **Loop / trigger behaviour**: on load / scroll / hover / loop; anything not visible in the recording is labelled an assumption.
6. **Reference frames**: a list of `frames/…` with a one-line caption each, plus an instruction to open them and match the visuals.
7. **Implementation notes**: stack-specific hints, with `prefers-reduced-motion` handling.
8. **Acceptance checklist**: concrete, checkable criteria (total duration, ordering, no layout shift, …).

## Error handling

- `ffmpeg`/`ffprobe` missing → exit with `brew install ffmpeg` (or a platform equivalent) hint.
- `yt-dlp` missing and needed → exit with `brew install yt-dlp` / `pipx install yt-dlp` hint.
- Download failure (private post, login wall, 404) → message suggesting a manual download and passing the path.
- Unreadable or corrupt file → rejected by the `ffprobe` check before analysis.
- Longer than 3:00 → rejected outright with a trim hint, by both scripts. For page URLs, `fetch.py` asks `yt-dlp` for the duration before downloading; direct downloads are checked with `ffprobe` afterwards and deleted.
- Longer than 60s (but within 3:00) with no range given → analyse the first 60s and warn; `--start/--end` override this.
- No motion detected → report it explicitly (the clip is static, or the threshold is too high); no empty prompt is generated.

## Dependencies

- Required: `ffmpeg`, `ffprobe`, Python 3.9+ (standard library only, no pip installs).
- Optional: `yt-dlp` (for page URLs only), `curl`.

## Testing

- **Synthetic fixtures** generated by ffmpeg at test time (e.g. `color` source + `overlay` with time expressions): a box that rests 0–0.5s, moves 0.5–1.2s, then rests. Assert segment boundaries within ±1 frame, the expected keyframe count, and loop detection on a looping fixture vs a non-looping one. Also a static clip → "no motion" result, and a GIF input → normalized correctly.
- **`fetch.py` unit tests**: path vs URL classification, direct-media vs page URL routing, missing-tool error messages (by mocking `shutil.which`), and a nonexistent path.
- **Manual end-to-end check**: run the full skill on one real web-animation recording and judge the prompt by hand.

## Out of scope (YAGNI)

- Prompts for AI video generators.
- Audio analysis.
- Automatically rebuilding the animation, or diffing a rebuild against the original.
- Non-Claude-Code runtimes.
