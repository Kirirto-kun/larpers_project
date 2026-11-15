# larpool frontend integration

## Intent
Use the colleague's `origin/landing-page` design (84611dc) for a usable public landing page, member workspace and administration console. The user asked for implementation, clear UX and less service copy; work continues within that authorization.

## Constraints
- Retain the existing React/Vite applications, API contracts, authentication, account activation, role checks, uploads, job review and device controls. All frontend copy must be English, including statuses, errors and accessible labels.
- Use the colleague's larpool mark, obsidian #0B0F0E, moss #102A22, mint #4DE8B0 and cream #F3F4F1. Apply the same typography, controls, surfaces and navigation in both apps.
- Public `/` explains the product. `/login` and `/register` open account forms. Authenticated `/` remains the workspace; direct task/device links retain their destination after sign-in.
- All calls to action work. Avoid invented performance metrics, pricing, customer claims or operational statistics. Preserve consequential consent and warnings.
- Support keyboard navigation, reduced motion and 375/768/1440px layouts. Keep runtime assets local; no CDN scripts.

## Approaches
Importing the standalone HTML/admin app verbatim would duplicate apps and regress current authentication/contracts. Merging the entire colleague branch would mix unrelated history and dataset changes. Selected integration is the implementation choice: port the recognizable landing composition and logo into the existing portal, and apply shared visual tokens to existing functional screens.

## Views
Landing: dark header and hero, mint particle globe, short product benefits, three launch steps, resource contribution section, native FAQ and final working CTA. No architecture dump or speculative prices. Mobile navigation exposes section links and sign-in.

Workspace: concise intro, task/device actions, real account metrics, recent jobs and participation history. Consistent sidebar contains account and sign-out; no duplicate topbar or sidebar slogans. Existing creation, plan review, upload, results and contribution flows receive the same visual treatment.

Console: sidebar matches the colleague's layout. Overview shows real summary, job distribution, available resources and devices; other pages retain current controls. Technical details remain available when useful to an administrator.

## Validation
Add journey coverage for public navigation and post-authentication destinations. Run both TypeScript/production builds and complete browser suites. Inspect desktop and mobile screenshots, overflow, keyboard focus and console errors. Preserve unrelated work in the shared checkout.
