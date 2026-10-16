# Stack detection

Run from the user's current working directory (their project root).

## 1. Look for a project

Check for, in order: `package.json`, `index.html`, `pubspec.yaml`, `*.xcodeproj`/`Package.swift`,
`build.gradle*`. If none exist → **no project**, go to §4.

## 2. Read `package.json` dependencies (deps + devDeps)

**Framework**

| Dependency | Framework |
|---|---|
| `next` | Next.js (check `app/` vs `pages/` for router) |
| `react` (without next) | React (Vite/CRA — check for `vite`) |
| `vue` / `nuxt` | Vue / Nuxt |
| `svelte` / `@sveltejs/kit` | Svelte / SvelteKit |
| `astro` | Astro |
| `@angular/core` | Angular |
| none of the above | Plain HTML/JS |

**Animation library** — prefer what is already installed, in this order of fit:

| Dependency | Use |
|---|---|
| `motion` or `framer-motion` | `motion` components, `variants`, `staggerChildren`, `useScroll` |
| `gsap` | timelines, `ScrollTrigger`, `SplitText` (check plugins in imports) |
| `@react-spring/*` | springs |
| `animejs` | anime timelines |
| `lottie-web` / `@lottiefiles/*` | only if the motion is clearly illustrative vector art |
| `three` / `@react-three/fiber` | only for real 3D scenes |
| none | CSS transitions/keyframes + Web Animations API; recommend adding a library only if the motion needs it (complex stagger, scroll-scrubbing, springs) |

**Styling**: `tailwindcss` (also check `tailwind.config.*` or `@import "tailwindcss"`),
CSS modules, `styled-components`, `@emotion/*`, `sass`. Note the TypeScript setting
(`typescript` dep or `tsconfig.json`).

## 3. Other platforms

- `pubspec.yaml` → Flutter (`AnimationController`, implicit animations, `flutter_animate` if present)
- Swift → SwiftUI (`withAnimation`, `.spring(response:dampingFraction:)`, `matchedGeometryEffect`)
- Android → Jetpack Compose (`animate*AsState`, `AnimatedVisibility`, `spring()`)

## 4. No project / fallback

Write the motion spec stack-agnostically, and add **one** recommended implementation with a
one-line reason, chosen by the motion's nature:

- Simple enter/exit, hover, few elements → **CSS + vanilla JS** (zero deps)
- Text splitting, long sequenced timelines, scroll-scrubbed → **GSAP** (+ SplitText/ScrollTrigger)
- React-style component state transitions, layout animations, springs → **React + Motion**
- Illustrative vector character/icon animation → **Lottie** (exported from After Effects) or SVG + GSAP

## 5. Record the result

In the prompt's *Target stack* section write the stack and **how it was detected**, e.g.
"Next.js 15 (app router) + Motion 12 + Tailwind 4 — detected from package.json".
