# larpool frontend implementation plan

> **For agentic workers:** Use superpowers:executing-plans for implementation and a fresh whole-change review.

**Goal:** Integrate the colleague's larpool frontend into the working product.
**Architecture:** Keep both existing React/Vite apps and API integrations. Share visual CSS tokens, port the source logo, and implement a local React landing.
**Tech stack:** React 19, TypeScript, React Router, Vite, Tailwind, Lucide, Playwright.
**Spec:** `docs/superpowers/specs/2026-11-15-larpool-frontend-design.md`

## Global constraints
English copy, local assets, unchanged server contracts and role/consent checks. Preserve unrelated changes. Use the source branch's obsidian/mint/cream design.

## Tasks
- [x] Add failing public landing and destination-preservation browser journeys in `apps/portal/tests/landing.spec.ts`.
- [x] Port logo and favicon from `origin/landing-page`. Build `Landing.tsx`, `ParticleGlobe.tsx` and scoped landing styles; wire `/login`, `/register` and safe post-login destinations in portal `App.tsx`.
- [x] Add shared product visual styles, update both app shells, simplify workspace and console page copy while retaining controls and warnings.
- [x] Build both apps; run all existing and new browser journeys. Update assertions only where intentional visible copy/navigation changed.
- [x] Inspect desktop/mobile screenshots and overflow, review the final diff, and document validation.

## Review focus
Authentication redirect on direct links, one-time activation hash, unauthorized admin requests, no fake metrics, no mobile overflow, reduced motion and no dependency on public CDNs.

## Completion record
- Source design: `origin/landing-page` at `84611dc`; current product API/contracts retained.
- All static interface copy is English. Arbitrary user files, model output and execution logs keep their original language.
- Both production builds passed. Portal browser suite: 19 passed. Console browser suite: 8 passed.
- Fresh read-only review found one destination-preservation issue. Portal form-switch and nested-console regressions were reproduced with failing tests, fixed, and the full suites passed.
- A temporary preview server with the default API target caused one download check to fail. Its proxy log showed ECONNREFUSED on port 8000. Stopping that preview and letting Playwright start its configured download fixture restored the full 19/19 pass; no product or test behavior was changed to hide this setup issue.
- Visual QA uses synthetic DTO fixtures, not live compute results. See `docs/FRONTEND_VERIFICATION.md`.
