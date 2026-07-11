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
OPENAI_API_KEY
HAI_API_KEY
```

OpenAI reads the PR diff and chooses the five browser checks. Optional planner
variables:

```text
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_PLANNER_MODEL=gpt-5.6-luna
```

Optional repository variables for Holo agent scaling:

```text
HOLO_AGENT_TRY_1=h/web-surfer-flash
HOLO_AGENT_TRY_2=h/web-surfer-flash
HOLO_AGENT_TRY_3=h/web-surfer-flash
HOLO_MODEL_TRY_1=
HOLO_MODEL_TRY_2=
HOLO_MODEL_TRY_3=
HOLO_MAX_STEPS_TRY_1=35
HOLO_MAX_STEPS_TRY_2=45
HOLO_MAX_STEPS_TRY_3=60
HOLO_MAX_TIME_SECONDS_TRY_1=300
HOLO_MAX_TIME_SECONDS_TRY_2=360
HOLO_MAX_TIME_SECONDS_TRY_3=480
```

The workflow is split into visible GitHub Actions stages:

1. `Plan five Holo checks` reads the PR diff with OpenAI, selects up to five
   checks, and immediately posts a PR comment listing what will be tested.
2. `holo-agent` fans out into a GitHub Actions matrix: five checks times three
   explicit Holo attempt profiles (`fast`, `balanced`, `deep`). Each matrix job
   shows the check title and tier in the GitHub UI and uploads its own result.
3. `Summarize Holo QA results` downloads all agent artifacts, updates the PR
   comment with the final table, and fails CI only when agents agree on a
   major/critical regression or one agent reports a critical regression.
