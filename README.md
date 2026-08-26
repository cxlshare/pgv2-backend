# pgv2-poc (flat-layout branch)

Both services' source lives directly at repo root — no `backend/`/`frontend/`
subfolders. Each service has its own Dockerfile, selected by name via
`docker build -f`, and its own requirements file so pip installs stay
independent.

## Layout

- `app.py` / `requirements.txt` / `Dockerfile` — **frontend-api** (port 8000, `templates/`, `static/`)
- `app_backend.py` / `requirements-backend.txt` / `Dockerfile.backend` — **backend-service** (port 8001)
- `docs/` — backend runbooks

`Dockerfile.backend` copies `requirements-backend.txt` → `requirements.txt`
and `app_backend.py` → `app.py` *inside the image*, so neither app's own
source needed to change — only the on-disk filenames at repo root, to
avoid the two services' identically-named files colliding in one
directory.

## Building each image

```bash
docker build -f Dockerfile          -t frontend-api:local .
docker build -f Dockerfile.backend  -t backend-service:local .
```

Both use the same build context (repo root, `.`) — only the Dockerfile
differs. See [buildspec.yml](buildspec.yml) for the AWS CodeBuild version
that also pushes each image to its own ECR repo.
