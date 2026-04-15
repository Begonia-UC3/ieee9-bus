# CLAUDE.md — ieee9-bus repository guide

This file briefs Claude Code (and any future contributor) on how this
repository is structured, deployed, and extended. The maintainer's
global preferences live in `~/CLAUDE.md`; this file only covers
project-specific knowledge.

## What this repo is

A simulation of the **IEEE 9-bus power grid**, packaged as **Cozystack
external apps** (plural). Topology:

- `grid-central` — FastAPI Newton–Raphson power-flow engine + WebSocket
  dashboard. Deployed via the `Ieee9Grid` CR.
- `diesel-gen` — diesel generator on bus 2 (PV, 163 MW).
- `battery` — battery BESS on bus 3 (PV±, 85 MW).
- `datacenter` — data-center load on bus 5 (PQ, 125 MW).
- `dso` — **D**istribution **S**ystem **O**perator: runs its own
  internal Newton–Raphson over a user-supplied grid model (JSON pulled
  from the operator's own Git repo via git-sync), then reports the
  aggregate flow at its external tie as a single asset upstream.

All four asset services (`diesel-gen` / `battery` / `datacenter` /
`dso`) are deployed through the **same** chart and the **same**
`ApplicationDefinition` (`Ieee9Zone`) — the mode is selected per CR
via `spec.mode`. One `Ieee9Zone` CR per asset role.

Asset simulators iteratively POST telemetry to `grid-central`, which
re-runs the power flow and broadcasts results back over WebSocket.

### Branch note

This is branch `dso`, forked from `main`. The diesel-gen split on
branch `distributed` is an older intermediate step (one dedicated
`Ieee9Diesel` kind); it is superseded by the generic `Ieee9Zone`
approach here. Ветка `distributed` does not merge anywhere — it stays
as a reference point. Ветка `dso` is the current direction.

## Repository layout

```text
.
├── grid-central/, diesel-gen/, battery/, datacenter/, dso/
│   ├── Containerfile        # one image per service, Python 3.12-slim
│   ├── main.py              # FastAPI app
│   ├── requirements.txt
│   └── frontend/index.html  # per-service control panel
├── dso/example-model.json   # reference DSO grid-model JSON (4-bus distribution feeder)
├── deploy/
│   └── compose.yaml         # local dev (docker compose / podman compose)
├── packages/
│   ├── apps/ieee9-grid/     # grid-central only (the power-flow engine)
│   │   ├── Chart.yaml       # version 0.0.0 (bumped by upstream tooling)
│   │   ├── Makefile
│   │   ├── values.yaml
│   │   ├── values.schema.json
│   │   └── templates/
│   ├── apps/ieee9-zone/     # generic asset zone (diesel | battery | datacenter | dso)
│   │   ├── Chart.yaml
│   │   ├── Makefile
│   │   ├── values.yaml      # mode + common wiring + DSO-only fields
│   │   ├── values.schema.json
│   │   └── templates/       # deployment.yaml is mode-conditional; adds git-sync sidecar when mode=dso
│   └── core/platform/       # Cozystack glue — registers both apps
│       ├── Chart.yaml
│       ├── Makefile
│       └── templates/
│           ├── cozyrds.yaml      # ApplicationDefinition/ieee9-grid + ApplicationDefinition/ieee9-zone
│           └── helmcharts.yaml   # Flux HelmChart per chart
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
   - `ApplicationDefinition/ieee9-grid` + `HelmChart/ieee9-bus-ieee9-grid`
     (registers `Ieee9Grid`).
   - `ApplicationDefinition/ieee9-zone` + `HelmChart/ieee9-bus-ieee9-zone`
     (registers `Ieee9Zone` with `spec.mode` ∈ {diesel, battery,
     datacenter, dso}).

After ~30 s `kubectl api-resources --api-group=apps.cozystack.io` lists
`ieee9grids` and `ieee9zones`. The dashboard exposes both under
category **Simulation**.

**Bootstrap gotcha — `reconcileStrategy`:** the platform `HelmRelease`
must use `reconcileStrategy: Revision` (set in `init.yaml`). Chart
`version` is permanently `0.0.0`, so Flux's default `ChartVersion`
strategy never re-packages the chart on new Git revisions — schema
edits silently stop applying. On clusters bootstrapped before this
field existed, patch in place:

```bash
kubectl -n cozy-system patch helmrelease ieee9-bus-platform \
  --type=merge -p '{"spec":{"chart":{"spec":{"reconcileStrategy":"Revision"}}}}'
```

### Creating an instance

A full simulation requires one `Ieee9Grid` plus one `Ieee9Zone` per
asset role — all in the **same** tenant namespace so in-namespace DNS
(`http://grid-central:8000`) resolves between pods.

```yaml
---
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Grid
metadata:
  name: demo
  namespace: tenant-root
spec:
  replicas: 1
  ingressEnabled: true
  ingressHost: ieee9.cozystack-demo.org
  ingressClassName: tenant-root
  tlsEnabled: true
  tlsClusterIssuer: letsencrypt-prod
---
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Zone
metadata:
  name: diesel
  namespace: tenant-root
spec:
  mode: diesel
  replicas: 1
  gridCentralUrl: "http://grid-central:8000"
  ingressEnabled: true
  ingressHost: diesel.cozystack-demo.org
  ingressClassName: tenant-root
---
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Zone
metadata:
  name: dso
  namespace: tenant-root        # same tenant as Ieee9Grid
spec:
  mode: dso
  modelRepoUrl: "https://github.com/<user>/<model-repo>.git"
  modelRepoBranch: main
  modelPath: model.json
  modelSyncIntervalSeconds: 30
  assetId: datacenter           # DSO replaces outer bus-5 role
  gridCentralUrl: "http://grid-central:8000"
  ingressEnabled: true
  ingressHost: dso.cozystack-demo.org
  ingressClassName: tenant-root
```

Cozystack renders each `Ieee9Zone` into a `HelmRelease` named
`zone-<instance-name>`. The Deployment / Service inside always use the
**mode's canonical name** as the workload name (`diesel-gen`, `battery`,
`datacenter`, `dso`) so cross-asset DNS stays predictable. Two
`Ieee9Zone` CRs with the same mode in the same namespace would
collide — by design, one asset role per tenant.

### DSO mode specifics

When `spec.mode: dso`, the chart injects a **git-sync** sidecar
(`registry.k8s.io/git-sync/git-sync:v4.3.0`) that clones the user's
model repo into a shared `emptyDir` volume at `/model/current/…`. The
main DSO process watches the model file's mtime each tick and
rebuilds its Y-bus + bus state in-place on any change — **no pod
restart**, WebSocket clients survive reloads.

The model JSON contract is documented at the top of `dso/main.py`.
Minimum shape: `s_base`, `buses[]`, `branches[]`, `external_tie`.
The `external_tie.asset_id` must match a key in `grid-central`'s
`ASSET_BUS_MAP` (currently `diesel-gen`, `battery`, `datacenter`) —
the DSO reports its net tie flow using that asset_id, so upstream it
occupies one of the outer IEEE-9 asset bus roles.

First iteration assumes **public** model repos — no Secret wiring for
git-sync auth yet. Add when needed.

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

There is no REST endpoint for this — UI only. The 5 services that need
flipping: `grid-central`, `diesel-gen`, `battery`, `datacenter`, `dso`.
Once flipped, kubelet retries on its own backoff.

## CI

- **`.github/workflows/images.yaml`** — matrix-builds 5 images
  (`grid-central`, `diesel-gen`, `battery`, `datacenter`, `dso`) on
  push to `main`/`dso`, on PRs (without push), and on `v*` tags. Cache
  via `gha` scoped per service. linux/amd64 only.
- **`.github/workflows/chart.yaml`** — runs `helm lint` and
  `helm template` on both packages plus `python3 -c 'yaml.safe_load_all'`
  on `init.yaml` for any change to `packages/`, `init.yaml`, or
  `scripts/`. **Does not** publish the chart anywhere.

## Local development

```bash
cd deploy
docker compose up --build      # or podman compose
```

Services bind to `localhost:8000–8004` (dso is 8004). The compose
file rebuilds from the `Containerfile` in each service dir, and mounts
`dso/example-model.json` into the dso container at `/model/model.json`
for local testing without a real Git repo.

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
- **Don't add `charts/` again.** Charts live under
  `packages/apps/<name>/`; the `charts/` directory was removed in PR #3
  and shouldn't come back.
- **Don't re-merge into one chart.** `ieee9-grid` (just grid-central)
  and `ieee9-zone` (all asset types via `mode`) are intentionally
  separate so tenants can deploy assets independently of the central
  engine.
- **Don't add a new kind per asset.** The point of `Ieee9Zone` with
  `spec.mode` is that battery/datacenter/diesel/dso all share one
  ApplicationDefinition. A new asset type = a new image + an added
  value in the `mode` enum + updated schema, not a new kind.
- **Don't bump chart `version`.** Cozystack uses Git revision as the
  artifact version. Bump `appVersion` to track image releases.
- **Don't pin images by digest in templates.** The chart deploys
  `:<appVersion>` (overridable via `imageTag`). Digest pinning is
  done at the registry tag layer.
- **Tenant namespace matters.** Both `Ieee9Grid` and `Ieee9Zone` CRs
  must live in a Cozystack tenant namespace (not `default`), and
  **all CRs of one simulation must share a namespace** — cross-zone
  DNS resolves in-namespace. The reference cluster's tenant is
  `tenant-root`.
- **DNS for ingress.** `*.cozystack-demo.org` resolves to
  `95.217.144.125` (the cluster's floating IP). Any other host
  requires an A/CNAME record before LE will issue a cert.

## When extending the schema

Three places must be kept in sync per chart — JSON Schema is the
source of truth:

1. `packages/apps/<chart>/values.yaml` (default values + `@param`
   docstrings).
2. `packages/apps/<chart>/values.schema.json` (validation).
3. `packages/core/platform/templates/cozyrds.yaml`
   (`spec.application.openAPISchema` mirrors values.schema.json;
   `spec.dashboard.keysOrder` controls the YAML editor field order).

Forgetting the third one means new fields stay invisible in the
dashboard.

## When adding a new asset type to `Ieee9Zone`

1. Create `<new-asset>/` service dir (Containerfile, main.py,
   requirements.txt, frontend/index.html). Follow the diesel-gen
   pattern: POST to `{GRID_CENTRAL_URL}/api/asset-update` each tick.
2. Add the type to `.github/workflows/images.yaml` matrix and the
   `paths:` triggers.
3. Add the value to the `mode` enum in both
   `packages/apps/ieee9-zone/values.schema.json` and the platform's
   `openAPISchema` in `cozyrds.yaml`.
4. If the image directory name differs from the mode string (as with
   `diesel` → `diesel-gen`), update
   `packages/apps/ieee9-zone/templates/_helpers.tpl` → `imageName`.
5. If the asset needs an outer bus on the IEEE 9 topology, extend
   `grid-central/main.py:ASSET_BUS_MAP` (and document which bus).

## Behavioural gotchas

- **`grid-central` has no TTL on asset overrides.**
  `grid-central/main.py:apply_asset_overrides` resets buses to the
  IEEE 9-bus base case each solve, then overlays the latest per-asset
  value. Entries never expire — a dead asset looks identical to a
  live one holding a steady setpoint until `grid-central` itself
  restarts. Keep this in mind when debugging "frozen power flow".
- **DSO model hot-reload triggers on mtime.** git-sync atomically
  swaps the `current` symlink, but the watched file's mtime may or may
  not change depending on the git object actually changing. If pushes
  to the model repo don't visibly reload, check `kubectl logs -c
  git-sync` and make sure the commit actually modified the target
  file.
- **DSO `external_tie.asset_id` must match `ASSET_BUS_MAP`.** DSO
  reports as that asset_id to `grid-central`; if the id isn't in
  `ASSET_BUS_MAP`, the injection is silently ignored. Current valid
  values: `diesel-gen`, `battery`, `datacenter`.

## Quick links

- Reference cluster dashboard: <https://dashboard.cozystack-demo.org>
- Demo deployment (when running): <https://ieee9.cozystack-demo.org>
- GHCR org: <https://github.com/orgs/Begonia-UC3/packages>
- Upstream pattern: <https://github.com/cozystack/external-apps-example/pull/2>
