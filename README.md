# pgv2-poc

Monorepo for the Hyventur PGv2 payment gateway POC. Merged from the
previously separate `pgv2-backend` and `pgv2-frontend` repositories
(commit history preserved via `git subtree`).

## Layout

- `backend/` — backend-service (deploys to ECS Fargate service `pgv2-poc-backend-service`)
- `frontend/` — frontend-api (deploys to ECS Fargate service `pgv2-poc-frontend-api`)

## CI/CD

A single workflow, [.github/workflows/deploy.yml](.github/workflows/deploy.yml),
triggers on PRs merged into `main`. It detects which subfolder(s) changed
and only runs the deploy job(s) for the affected service(s):

- `deploy-backend` — builds/pushes `backend/`, runs the Inspector
  vulnerability scan gate, then deploys to ECS.
- `deploy-frontend` — builds/pushes `frontend/`, then deploys to ECS.

Each job assumes its own per-service AWS IAM role via OIDC — see the
workflow file for the role ARNs. **Those roles' trust policies still
reference the old per-service repos and must be updated to trust this
repo instead** (see migration notes handed back alongside this repo).
