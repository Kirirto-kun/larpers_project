# larpool portal

React/TypeScript/Vite application. Signed-out visitors see the public landing at `/`; signed-in users see their workspace. Account pages are `/login` and `/register`. Workspace routes are `/contribute`, `/new`, `/jobs/:id`. Account links retain the requested destination through login or registration.

The visual system and logo are adapted from the colleague's `landing-page` branch (84611dc). Both web apps import `apps/shared/product.css`; the landing uses local React and canvas assets, with no CDN runtime dependencies. All interface copy is English; user files, logs and generated task content retain their original language.

From the repository root:

```sh
npm --prefix apps/portal ci
npm --prefix apps/portal run dev
```

Requires Node >=22.12. The portal runs at http://127.0.0.1:5173 and proxies `/api` to `CAMPUS_API_URL` (default http://127.0.0.1:8000). Session cookies and CSRF are handled in `src/lib/api.ts`; all authorization and admission remain server responsibilities.

## Local development

Start the real coordinator for interactive use. The legacy `dev/fixture_api.py` is not used and does not reflect current HTTP contracts. Production requires a shared HTTPS origin for portal, console and API.

## Checks

```sh
npm --prefix apps/portal run build
npm --prefix apps/portal exec -- playwright install chromium
npm --prefix apps/portal run test:e2e
```

Browser tests intercept API traffic per test with actual DTO-shaped synthetic fixtures. They cover accounts, activation, pending/offline devices, private invitations, project sharing, resumable uploads, clarification, idempotent retries and downloads. A tiny loopback HTTP artifact fixture on port 18991 lets the browser download manager verify actual bytes. Test fixtures are never production imports or physical-execution evidence.

`src/lib/types.ts` matches `server/campus/dto.py`. User uploads include whole-file and part SHA-256, immutable input bindings and finalized Artifact responses. Project ZIP/folder text disclosure is explicit; built-in CPU/GPU examples use ordinary prepared previews without sending data to external AI. Native readiness requires server/agent acknowledgement. Final live API, DeepSeek and native-device verification is recorded separately by deployment.
