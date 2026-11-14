# Campus Pool console

Independent React/TypeScript/Vite application mounted at `/admin/`. Pages: Overview, Devices, Jobs, Benchmarks.

```sh
npm --prefix apps/console ci
npm --prefix apps/console run dev
npm --prefix apps/console run build
npm --prefix apps/console run test:e2e
```

Requires Node >=22.12 and Chromium installed through Playwright. Development URL: http://127.0.0.1:5174/admin/ . The API proxy uses `CAMPUS_API_URL` or http://127.0.0.1:8000. For an explicitly synthetic preview, run `python3 apps/portal/dev/fixture_api.py` separately. No measured results are bundled into the app.

Member sessions see observations; owner/admin sessions additionally see device control, experiment creation and standalone import. Server authorization must enforce these actions. Unknown measurements are shown separately from zero. Event disconnects reconnect and refresh snapshots.

Provisional models live in `src/lib/types.ts` pending final integration. Generated foundation types belong in ignored `src/generated/`. Serve dist at `/admin/` with SPA fallback; the control-plane owner handles shared-origin deployment. See `tools/benchmark/README.md` for reproducible reporting and `docs/submission/` for demo materials.
