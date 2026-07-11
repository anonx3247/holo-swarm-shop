# Holo Swarm Shop

No-backend Vercel demo app for testing Holo/Codex PR regression workflows.

The app is intentionally interaction-heavy without needing a database:

- Storefront search, category filtering, product imagery, and cart quantity controls.
- Checkout with promo code `HOLO15`, delivery form, local order creation, and confirmation.
- Operations board with order advancement and low-stock inventory watchlist.
- QA surface that points Holo agents at stable workflows for regression checks.

All order and cart state is browser-local. Vercel can deploy this as a static
Vite app without provisioning any backend service.

## Local Development

```sh
npm install
npm run dev
```

Build the production artifact:

```sh
npm run build
```

## Vercel

Import the GitHub repo into Vercel. The included `vercel.json` sets:

```text
buildCommand = npm run build
outputDirectory = dist
framework = vite
```

## Holo PR QA

`.github/workflows/holo-pr-qa.yml` runs on pull requests and tests the Vercel
preview deployment with Holo browser agents.

Required repository secret:

```text
HAI_API_KEY
```

Optional repository variables for model/agent scaling:

```text
HOLO_AGENT_TRY_1=h/web-surfer-flash
HOLO_AGENT_TRY_2=h/web-surfer-flash
HOLO_AGENT_TRY_3=h/web-surfer-flash
```

The workflow reads the PR diff, selects up to five checks, runs three Holo
agents per check concurrently, posts a PR comment, and fails CI only when agents
agree on a major/critical regression or one agent reports a critical regression.
