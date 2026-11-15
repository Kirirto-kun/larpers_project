# larpool console

Independent React/TypeScript/Vite application mounted at `/admin/`. Pages: Overview, Devices, Jobs, Benchmarks.

The interface follows the colleague's larpool design through the shared `apps/shared/product.css` visual system. All interface copy is English. The overview charts use the current server DTOs; missing data remains distinct from measured zero. Signing in from a nested page returns to that page.

```sh
npm --prefix apps/console ci
npm --prefix apps/console run dev
npm --prefix apps/console run build
npm --prefix apps/console run test:e2e
```

Requires Node >=22.12 and Chromium installed through Playwright. Development URL: http://127.0.0.1:5174/admin/ . The API proxy uses `CAMPUS_API_URL` or http://127.0.0.1:8000. Start the real coordinator and sign in through the portal. No measured results are bundled into the app.

Member sessions stop before any administration data request. Owner/admin sessions see pool summary, actual hardware and task counters, device control, experiment creation and standalone import. Server authorization independently enforces these actions. Unknown measurements remain distinct from zero; snapshots refresh every 5 seconds.

`src/lib/types.ts` matches `server/campus/dto.py`. Benchmark creation consumes a reviewed preview ID/revision and at least two device IDs. Charts use actual report measurements; export is JSON. Serve dist at `/admin/` with SPA fallback. Playwright fixtures establish UI behavior, not live execution; deployment records physical verification separately.
