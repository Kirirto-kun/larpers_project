# Campus Pool admin panel

React/TypeScript/Vite admin console, styled to match the sibling `landing/campus-pool-site.html` landing page. Includes Overview, Devices, Jobs, and Benchmarks.

Requires Node >=22.12.

```sh
cd admin-panel
npm ci
npm run dev
```

Open http://127.0.0.1:5174/admin/ . This is an application, not a standalone HTML file.

## API or sample-data preview

The app uses cookie authentication and `/api/v1` endpoints. The development server proxies API requests to http://127.0.0.1:8000 by default; set `CAMPUS_API_URL` to use another coordinator. Sign in through the user portal when using a real backend.

To explore the interface without a backend, run this in a second terminal from the repository root:

```sh
python3 admin-panel/dev/fixture_api.py
```

The fixture server uses synthetic data only. It performs no real computation or AI calls and reports no real measurements. Do not use it as a production backend.

## Build and checks

```sh
npm run build
npx playwright install chromium
npm run test:e2e
```

Serve the built `dist/` directory at `/admin/` with SPA fallback and route `/api/v1` to your coordinator. Member sessions show observations; owner/admin sessions also expose device controls and experiment creation. The backend must enforce authorization.

Validated: production build, 9 existing browser tests, and desktop/mobile overflow checks across all four pages.
