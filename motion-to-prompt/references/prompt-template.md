# prompt.md template

Fill in every section. Delete the guidance in *italics*. The finished prompt goes to a
coding agent that has **not** seen the video, so it must stand on its own (apart from the
frames it references).

---

```markdown
# Rebuild: <short descriptive title>

> Source: <original URL or filename> · analysed <range.start_s>–<range.end_s>s ·
> <video.width>×<video.height> @ <fps_source>fps
> **Timings are measured from the recording; element identity and easing are inferred.**

## Goal

<One paragraph: what the thing is, what happens and in what order, and how it should
*feel* (snappy, elastic, calm, premium…). Name the overall duration
(first_motion_ms → last_motion_end_ms) and whether it loops.>

## Target stack

<Stack + how it was detected. Or: "No project detected. Spec is library-agnostic;
recommended: GSAP 3 + SplitText because …">

## Scene & layout

<The elements involved and their resting layout: position (use segment regions and the
frames), approximate size as % of the viewport, z-order, border radius, shadows.>

**Palette** (measured): `#xxxxxx` background · `#xxxxxx` … *(map each colour to its role)*
**Typography** (observed): <family guess or class (geometric sans, serif…), weight, case,
approximate size relative to the viewport, tracking>

## Motion timeline

Times in ms from the start of the recording range. Start/duration are measured unless
marked ≈ (estimated within a measured segment).

| # | Element | Property | From → To | Start | Duration | Easing | Ref |
|---|---|---|---|---|---|---|---|
| 1 | <element> | <translateY / opacity / scale / clip-path / …> | <values with units> | <ms> | <ms> | <cubic-bezier / spring> | frames/NN_… |

<Stagger: "each word +80ms after the previous (≈, 6 words over a 480ms window)". Note
any properties that overshoot, and the end state each element must settle into.>

## Trigger & loop behaviour

<On page load / scroll into view / hover / click. Loops: yes or no (measured), plus the
loop delay. Mark each item that isn't visible in the video as **Assumption**.>

## Reference frames

Open these images and match the composition, colours and spacing:

- `frames/01_0ms.png`: <what this frame shows>
- …

## Implementation notes

<Stack-specific guidance, e.g. the structure of the variants, which props to animate
(stick to transform/opacity for performance), masking technique, how to split text,
`will-change`, cleanup. Always add: respect `prefers-reduced-motion` (fall back to an
opacity-only transition or the final state).>

## Acceptance checklist

- [ ] Total sequence ≈ <ms>, first motion at <ms>
- [ ] <ordering check, e.g. "headline settles before the CTA starts">
- [ ] <easing check, e.g. "elements decelerate into place with no linear stops">
- [ ] Final state matches `frames/NN_…png`
- [ ] No layout shift; only transform/opacity are animated
- [ ] Reduced-motion fallback works
```

---

## Easing reference

Pick a curve from the shape of `intensity_profile` and what the frames show:

| Feel | CSS | GSAP | Motion |
|---|---|---|---|
| Decelerate, standard UI enter | `cubic-bezier(0.22, 1, 0.36, 1)` | `power3.out` | `[0.22, 1, 0.36, 1]` |
| Strong decelerate, "expo" | `cubic-bezier(0.16, 1, 0.3, 1)` | `expo.out` | `[0.16, 1, 0.3, 1]` |
| Accelerate, exit | `cubic-bezier(0.55, 0, 1, 0.45)` | `power3.in` | `[0.55, 0, 1, 0.45]` |
| Symmetric | `cubic-bezier(0.65, 0, 0.35, 1)` | `power3.inOut` | `[0.65, 0, 0.35, 1]` |
| Overshoot, then settle | `cubic-bezier(0.34, 1.56, 0.64, 1)` | `back.out(1.7)` | `{ type: "spring", stiffness: 300, damping: 20 }` |
| Bouncy / elastic | n/a (use keyframes) | `elastic.out(1, 0.5)` | `{ type: "spring", stiffness: 400, damping: 10 }` |
| Constant | `linear` | `none` | `"linear"` |

If a frame shows an element **past** its final position, that's overshoot: use a back
ease or a spring.
