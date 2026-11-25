# SupplyChain Planner dashboard

React, Vite, Tailwind CSS, and Recharts frontend for the SupplyChain Planner API.

## Run locally

From this directory, install dependencies and start the dashboard:

```powershell
npm ci
$env:VITE_DEV_API_PROXY = "http://127.0.0.1:8000"
npm run dev
```

The development server proxies `/api` requests to `VITE_DEV_API_PROXY`, which
defaults to `http://127.0.0.1:8000`. Set the proxy target to the running FastAPI
backend when it uses a different address.

For deployments where the API is on another origin, set `VITE_API_BASE_URL` to
that origin before building. Leave it unset to use relative `/api` URLs. The
backend must allow cross-origin requests when using a separate origin.

## Validate

```powershell
npm run build
npm test
```
