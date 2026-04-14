# CLAUDE.md — ieee9-bus repository guide

This file briefs Claude Code (and any future contributor) on how this
repository is structured, deployed, and extended. The maintainer's
global preferences live in `~/CLAUDE.md`; this file only covers
project-specific knowledge.

## What this repo is

A four-service simulation of the **IEEE 9-bus power grid**, packaged
as a **Cozystack external app**. Topology:

- `grid-central` — FastAPI Newton–Raphson power-flow engine + WebSocket
  dashboard.
- `diesel-gen` — diesel generator on bus 2 (PV, 163 MW).
- `battery` — battery BESS on bus 3 (PV±, 85 MW).
- `datacenter` — data-center load on bus 5 (PQ, 125 MW).

Asset simulators iteratively POST telemetry to `grid-central`, which
re-runs the power flow and broadcasts results back over WebSocket.

## Repository layout

```text
.
├── grid-central/, diesel-gen/, battery/, datacenter/
│   ├── Containerfile        # one image per service, Python 3.12-slim
│   ├── main.py              # FastAPI app
│   ├── requirements.txt
│   └── frontend/index.html  # per-service control panel
├── deploy/
│   └── compose.yaml         # local dev (docker compose / podman compose)
├── packages/
│   ├── apps/ieee9-grid/     # Helm chart rendering the four workloads
│   │   ├── Chart.yaml       # version 0.0.0 (bumped by upstream tooling)
│   │   ├── Makefile         # cozyhr targets via scripts/package.mk
│   │   ├── values.yaml      # user-facing knobs only
│   │   ├── values.schema.json
│   │   └── templates/       # _helpers, deployment, service, ingress
│   └── core/platform/       # Cozystack glue
│       ├── Chart.yaml
│       ├── Makefile
│       └── templates/
│           ├── cozyrds.yaml      # ApplicationDefinition (dashboard wiring)
│           └── helmcharts.yaml   # Flux HelmChart pointing at apps/ieee9-grid
├── scripts/package.mk       # cozyhr apply/diff/delete shared helpers
├── init.yaml                # GitRepository + bootstrap HelmRelease
└── .github/workflows/
    ├── images.yaml          # build & push 4 images to GHCR (linux/amd64)
    └── chart.yaml           # helm lint + template (no OCI publish)
```

## Conventions specific to this repo

- **Containerfile, not Dockerfile.** Each service has a `Containerfile`
  in its directory. Compose references it explicitly via
  `dockerfile: Containerfile`.
- **Images are amd64 only.** No ARM build legs — the maintainer
  explicitly opted out. Don't add ARM matrix entries.
- **Image registry:** `ghcr.io/begonia-uc3/ieee9-bus/<service>`. Tags
  follow `docker/metadata-action` defaults: `sha-<short>`, `latest`
  on default branch, semver tags on `v*` git tags.
- **Helm chart is not OCI-published.** Cozystack pulls the chart
  directly from this repo's `GitRepository` via Flux. Do not
  reintroduce a chart push step.
- **Chart version is `0.0.0`.** Versioning is intentional — Cozystack
  treats every Git revision as a new chart build, so the chart's own
  semver is unused. Bump `appVersion` instead when releasing.
- **No `charts/` directory.** Earlier scaffolding (PR #1) had
  `charts/ieee9-grid`; that was removed in PR #3. The single chart
  lives under `packages/apps/ieee9-grid/`.

## Deploying to a Cozystack cluster

The user's reference cluster is **dtu-cluster** (Hetzner, single Talos
node). SSH access lives at `root@157.180.46.22`; cozystack tooling at
`~/dtu-cluster/`.

### Bootstrap (once per cluster)

```bash
scp init.yaml root@157.180.46.22:~/dtu-cluster/
ssh root@157.180.46.22 'cd ~/dtu-cluster && \
  KUBECONFIG=./kubeconfig ./kubectl apply --filename init.yaml'
```

That creates:

1. `GitRepository/ieee9-bus` in `cozy-public` (1m sync of `main`).
2. `HelmRelease/ieee9-bus-platform` in `cozy-system` deploying
   `./packages/core/platform`.
3. The platform chart in turn applies:
   - `ApplicationDefinition/ieee9-grid` (registers the
     `apps.cozystack.io/v1alpha1 Ieee9Grid` CRD into the dashboard).
   - `HelmChart/ieee9-bus-ieee9-grid` in `cozy-public` pointing at
     `./packages/apps/ieee9-grid`.

After ~30 s `kubectl api-resources --api-group=apps.cozystack.io` lists
`ieee9grids`. The dashboard exposes the app under category
**Simulation**.

### Creating an instance

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Grid
metadata:
  name: demo
  namespace: tenant-root        # or any other Cozystack tenant ns
spec:
  replicas: 1
  ingressEnabled: true
  ingressHost: ieee9.cozystack-demo.org
  ingressClassName: tenant-root
  tlsEnabled: true              # default — provisions LE cert via cert-manager
  tlsClusterIssuer: letsencrypt-prod
```

Cozystack renders this into a `HelmRelease` named `grid-<instance-name>`
(prefix from `ApplicationDefinition.spec.release.prefix`). Flux then
pulls the chart from the GitRepository and applies the four
Deployments + Services + Ingress.

### Verifying

```bash
ssh root@157.180.46.22 'cd ~/dtu-cluster && \
  KUBECONFIG=./kubeconfig ./kubectl get ieee9grids,pods,ingress -n tenant-root'
```

Probe the API from inside a pod:

```bash
kubectl exec -n tenant-root deploy/grid-central -- \
  python3 -c 'import urllib.request as u; print(u.urlopen("http://localhost:8000/api/grid-state", timeout=3).read().decode())'
```

A converged power flow returns `"converged":true` and an `iterations`
count (typically 4–6).

## Image visibility on GHCR

Images default to **private** when first pushed. Kubernetes pulls fail
with `401 Unauthorized` until they are made public:

- https://github.com/orgs/Begonia-UC3/packages/container/ieee9-bus%2F<service>/settings → Change visibility → Public

There is no REST endpoint for this — UI only. The 4 services that need
flipping: `grid-central`, `diesel-gen`, `battery`, `datacenter`. Once
flipped, kubelet retries on its own backoff.

## CI

- **`.github/workflows/images.yaml`** — matrix-builds 4 images
  (`grid-central`, `diesel-gen`, `battery`, `datacenter`) on push to
  `main`, on PRs (without push), and on `v*` tags. Cache via `gha`
  scoped per service. linux/amd64 only.
- **`.github/workflows/chart.yaml`** — runs `helm lint` and
  `helm template` on both packages plus `python3 -c 'yaml.safe_load_all'`
  on `init.yaml` for any change to `packages/`, `init.yaml`, or
  `scripts/`. **Does not** publish the chart anywhere.

## Local development

```bash
cd deploy
docker compose up --build      # or podman compose
```

Services bind to `localhost:8000–8003`. The compose file rebuilds from
the `Containerfile` in each service dir.

## Reference patterns

- **Cozystack external-app structure** —
  [`cozystack/external-apps-example` PR #2](https://github.com/cozystack/external-apps-example/pull/2)
  is the canonical example. The repo's `master` uses an older layout;
  always cross-check against PR #2.
- **Helm chart conventions** — modelled on
  [`lexfrei/charts`](https://github.com/lexfrei/charts) (per-app
  Chart.yaml, security context defaults, `values.schema.json`).

## Things to be careful about

- **Don't add a Helm OCI publish step.** Cozystack pulls from
  `GitRepository`. Republishing as OCI duplicates the artefact and
  drifts.
- **Don't add `charts/` again.** Bridge chart and standalone chart
  are the same chart now (`packages/apps/ieee9-grid`).
- **Don't bump chart `version`.** Cozystack uses Git revision as the
  artifact version. Bump `appVersion` to track image releases.
- **Don't pin images by digest in templates.** The chart deploys
  `:<appVersion>` (overridable via `imageTag`). Digest pinning is
  done at the registry tag layer.
- **Tenant namespace matters.** `Ieee9Grid` CRs must live in a
  Cozystack tenant namespace (not `default`). The reference cluster's
  only tenant is `tenant-root`.
- **DNS for ingress.** `*.cozystack-demo.org` resolves to
  `95.217.144.125` (the cluster's floating IP). Any other host
  requires an A/CNAME record before LE will issue a cert.

## When extending the schema

Three places must be kept in sync — JSON Schema is the source of truth:

1. `packages/apps/ieee9-grid/values.yaml` (default values + `@param`
   docstrings).
2. `packages/apps/ieee9-grid/values.schema.json` (validation).
3. `packages/core/platform/templates/cozyrds.yaml`
   (`spec.application.openAPISchema` mirrors values.schema.json;
   `spec.dashboard.keysOrder` controls the YAML editor field order in
   the Cozystack UI).

Forgetting the third one means new fields stay invisible in the
dashboard.

## Quick links

- Reference cluster dashboard: <https://dashboard.cozystack-demo.org>
- Demo deployment (when running): <https://ieee9.cozystack-demo.org>
- GHCR org: <https://github.com/orgs/Begonia-UC3/packages>
- Upstream pattern: <https://github.com/cozystack/external-apps-example/pull/2>
