# Campus Pool portal

Independent React/TypeScript/Vite application. Routes: `/`, `/contribute`, `/new`, `/jobs/:id`.

From the repository root:

```sh
npm --prefix apps/portal ci
npm --prefix apps/portal run dev
```

Requires Node >=22.12. The portal runs at http://127.0.0.1:5173 and proxies `/api` to `CAMPUS_API_URL` (default http://127.0.0.1:8000). Session cookies and CSRF are handled in `src/lib/api.ts`; all authorization and admission remain server responsibilities.

## Local preview without the other branches

In a separate terminal run `python3 apps/portal/dev/fixture_api.py`, then start portal and console normally. The session name clearly identifies **FIXTURE — Local preview**. This development-only API generates synthetic examples, executes no code, and sends nothing to AI. Never use its output as submission evidence. It is loopback-only and must not be deployed.

## Checks

```sh
npm --prefix apps/portal run build
npm --prefix apps/portal exec -- playwright install chromium
npm --prefix apps/portal run test:e2e
```

Browser tests intercept API traffic per test and use synthetic fixtures, including lost ACK, stale revisions, questions, disconnected events, partial output, and a downloadable model. Test fixtures are not imported by production code.

`src/lib/types.ts` contains provisional UI models while foundation is absent. During final integration, adapt the API layer to the control-plane contract and generate DTOs with its generator. `src/generated/` is ignored; never edit generated DTOs manually. See `docs/submission/usage.md` and `to do.md` for integration and physical-demo requirements.
