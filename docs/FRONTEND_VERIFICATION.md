# larpool frontend verification — 2026-11-15

The colleague's `landing-page` design at `84611dc` is integrated into the existing portal and console. The original logo and favicon are reused; the obsidian, mint and cream palette is shared through `apps/shared/product.css`. The public landing uses local React and canvas instead of CDN scripts.

The public root opens the landing for signed-out visitors and the workspace for signed-in users. `/login` and `/register` retain allowed task/device/console destinations through authentication and form switching. Account activation, role checks, upload contracts, explicit DeepSeek consent, device control and result downloads remain covered.

All static interface text, statuses, errors, accessible labels and page metadata are English. The current server's known example descriptions are mapped into English at the display boundary. User content, filenames, logs and generated prose retain their original language.

## Automated checks

- `npm --prefix apps/portal run build` — passed.
- `npm --prefix apps/console run build` — passed.
- `npm --prefix apps/portal run test:e2e -- --workers=3` — 19 passed.
- `npm --prefix apps/console run test:e2e -- --workers=2` — 8 passed.
- `git -c core.autocrlf=false diff --check` — passed.

Browser tests use HTTP DTO fixtures. Their download check also verifies bytes from the loopback download server. These checks validate UI and API request behavior; they are not evidence of live computation or a deployment.

## Visual review

Reviewed landing, sign-in, sign-up, workspace, new task, devices, task details, console overview, pool devices, pool tasks and benchmarks at 1440×1000, 375×812, 768×1024 and 812×375. The inspection checks horizontal overflow, JavaScript exceptions and Cyrillic interface text with controlled English fixtures. Screenshots cover desktop and mobile with reduced motion enabled. A separate read-only review checked authentication, translation boundaries, data accuracy and lifecycle cleanup; its destination-preservation finding is fixed and covered by two regression journeys.

No server, runner or database changes are part of this frontend integration. Changes are local to the checkout; no deployment was performed.
