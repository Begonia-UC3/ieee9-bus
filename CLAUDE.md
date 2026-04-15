# CLAUDE.md — ieee9-bus repository guide

This file briefs Claude Code (and any future contributor) on how this
repository is structured, deployed, and extended. The maintainer's
global preferences live in `~/CLAUDE.md`; this file only covers
project-specific knowledge.

## What this repo is

A four-service simulation of the **IEEE 9-bus power grid**, packaged
as **Cozystack external apps** (plural — one app per grid "zone").
Topology:

- `grid-central` — FastAPI Newton–Raphson power-flow engine + WebSocket
  dashboard.
- `diesel-gen` — diesel generator on bus 2 (PV, 163 MW).
- `battery` — battery BESS on bus 3 (PV±, 85 MW).
- `datacenter` — data-center load on bus 5 (PQ, 125 MW).

Asset simulators iteratively POST telemetry to `grid-central`, which
re-runs the power flow and broadcasts results back over WebSocket.

### App split (zones)

The services are packaged across **multiple** Cozystack apps rather
than one monolithic chart:

- `ieee9-grid` (kind `Ieee9Grid`) — bundles `grid-central`, `battery`,
  `datacenter`.
- `ieee9-diesel` (kind `Ieee9Diesel`) — standalone `diesel-gen`.

Each zone app is registered as its own `ApplicationDefinition` +
`HelmChart` in `packages/core/platform/templates/`. Asset simulators
reach `grid-central` via in-namespace DNS (`http://grid-central:8000`),
so **all zone CRs of one simulation must live in the same tenant
namespace** — otherwise the cross-service lookup fails silently (and
grid-central falls back to the IEEE 9-bus base case for the missing
asset, see "Behavioural gotchas" below).

Cross-tenant DSO-style topologies (where a zone runs its own internal
power flow before reporting up to `grid-central`) are on the roadmap
but not yet wired — they would use FQDNs like
`grid-central.tenant-foo.svc.cluster.local:8000`.

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
│   ├── apps/ieee9-grid/     # grid-central + battery + datacenter
│   │   ├── Chart.yaml       # version 0.0.0 (bumped by upstream tooling)
│   │   ├── Makefile         # cozyhr targets via scripts/package.mk
│   │   ├── values.yaml      # user-facing knobs only
│   │   ├── values.schema.json
│   │   └── templates/       # _helpers, deployment, service, ingress
│   ├── apps/ieee9-diesel/   # standalone diesel-gen (mirrors ieee9-grid layout)
│   └── core/platform/       # Cozystack glue — registers all zone apps
│       ├── Chart.yaml
│       ├── Makefile
│       └── templates/
│           ├── cozyrds.yaml      # one ApplicationDefinition per zone
│           └── helmcharts.yaml   # one Flux HelmChart per zone
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
3. The platform chart in turn applies, per zone:
   - `ApplicationDefinition/<zone>` (registers the
     `apps.cozystack.io/v1alpha1 <Kind>` type into the dashboard).
   - `HelmChart/ieee9-bus-<zone>` in `cozy-public` pointing at
     `./packages/apps/<zone>`.

   Currently two zones: `ieee9-grid` (kind `Ieee9Grid`) and
   `ieee9-diesel` (kind `Ieee9Diesel`).

After ~30 s `kubectl api-resources --api-group=apps.cozystack.io` lists
`ieee9grids` and `ieee9diesels`. The dashboard exposes them under
category **Simulation**.

**Bootstrap gotcha — `reconcileStrategy`:** the platform
`HelmRelease` must use `reconcileStrategy: Revision` (set in
`init.yaml`). Because chart `version` is permanently `0.0.0`, Flux's
default `ChartVersion` strategy never re-packages the chart on new Git
revisions — zone additions / schema edits would silently stop
applying. On clusters bootstrapped before this field existed, patch in
place:

```bash
kubectl -n cozy-system patch helmrelease ieee9-bus-platform \
  --type=merge -p '{"spec":{"chart":{"spec":{"reconcileStrategy":"Revision"}}}}'
```

### Creating an instance

A full simulation requires **one CR per zone**, all in the same
tenant namespace (for in-cluster DNS between `diesel-gen` → `grid-central`):

```yaml
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
  tlsEnabled: true              # provisions LE cert via cert-manager
  tlsClusterIssuer: letsencrypt-prod
---
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Diesel
metadata:
  name: demo
  namespace: tenant-root
spec:
  replicas: 1
  gridCentralUrl: "http://grid-central:8000"
  ingressEnabled: true
  ingressHost: diesel.cozystack-demo.org
  ingressClassName: tenant-root
  tlsEnabled: true
  tlsClusterIssuer: letsencrypt-prod
```

Cozystack renders each CR into a `HelmRelease` with the zone's
`prefix` (→ `grid-demo`, `diesel-demo`). Flux pulls the charts from
the GitRepository and applies the Deployments + Services + Ingress.

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
- **Don't add `charts/` again.** Zone charts live under
  `packages/apps/<zone>/`; the `charts/` directory was removed in PR #3
  and shouldn't come back.
- **Don't re-merge zones into one chart.** The split (ieee9-grid /
  ieee9-diesel / future zones) is intentional — each zone is its own
  Cozystack app so tenants can deploy them independently.
- **Don't bump chart `version`.** Cozystack uses Git revision as the
  artifact version. Bump `appVersion` to track image releases.
- **Don't pin images by digest in templates.** The chart deploys
  `:<appVersion>` (overridable via `imageTag`). Digest pinning is
  done at the registry tag layer.
- **Tenant namespace matters.** Zone CRs (`Ieee9Grid`, `Ieee9Diesel`,
  …) must live in a Cozystack tenant namespace (not `default`), and
  **all zones of one simulation must share that namespace** —
  cross-zone DNS (`http://grid-central:8000`) is resolved in-namespace.
  The reference cluster's only tenant is `tenant-root`.
- **DNS for ingress.** `*.cozystack-demo.org` resolves to
  `95.217.144.125` (the cluster's floating IP). Any other host
  requires an A/CNAME record before LE will issue a cert.

## When extending the schema

Three places must be kept in sync per zone — JSON Schema is the source
of truth:

1. `packages/apps/<zone>/values.yaml` (default values + `@param`
   docstrings).
2. `packages/apps/<zone>/values.schema.json` (validation).
3. `packages/core/platform/templates/cozyrds.yaml`
   (`spec.application.openAPISchema` mirrors values.schema.json;
   `spec.dashboard.keysOrder` controls the YAML editor field order in
   the Cozystack UI).

Forgetting the third one means new fields stay invisible in the
dashboard.

## When splitting another asset into its own zone app

Reference: the diesel-gen split (first commit on branch `distributed`,
see `packages/apps/ieee9-diesel/` for the template). Same-tenant case
— cross-tenant / DSO-style splits are different and not yet done.

Steps, in order:

1. **Create `packages/apps/ieee9-<zone>/`** mirroring `ieee9-diesel/`:
   `Chart.yaml` (version `0.0.0`), `Makefile`, `values.yaml`,
   `values.schema.json`, `templates/{_helpers.tpl,deployment.yaml,service.yaml,ingress.yaml}`.
   Copy the ingress block verbatim — users expect the same
   `ingressEnabled / ingressHost / …` knobs on every zone.
2. **Remove the component** from `packages/apps/ieee9-grid/templates/`
   (both the `$services` dict in `deployment.yaml` and the list in
   `service.yaml`).
3. **Register the new zone in platform glue:**
   - Append a `HelmChart/ieee9-bus-ieee9-<zone>` entry to
     `packages/core/platform/templates/helmcharts.yaml` (set
     `reconcileStrategy: Revision`).
   - Append an `ApplicationDefinition/ieee9-<zone>` to
     `cozyrds.yaml` with a distinct `kind`, `plural`, `singular`, and
     `prefix`. Mirror `openAPISchema` from the chart's
     `values.schema.json`.
4. **Verify locally:** `helm lint` and `helm template` both charts
   plus `packages/core/platform`.
5. **Flux sync:** push the branch, then on the cluster point
   `GitRepository/ieee9-bus` at it and force-reconcile the platform
   HelmRelease (needs `reconcileStrategy: Revision`, see bootstrap
   gotcha above).
6. **Create the zone CR** in the same tenant namespace as the other
   zones. Cross-zone DNS is bare service name (`http://<service>:8000`).

When the new zone needs to *also* reach a grid-central running in a
different tenant (DSO topology), expose `gridCentralUrl` as a chart
value and let the operator set an FQDN like
`http://grid-central.tenant-other.svc.cluster.local:8000` —
NetworkPolicy between tenants is a separate problem.

## Behavioural gotchas

- **`grid-central` has no TTL on asset overrides.** See
  `grid-central/main.py:110-129` (`apply_asset_overrides`). Each
  solve resets buses to the IEEE 9-bus base case, then overlays the
  last value posted by each asset. Entries never expire — a dead
  asset looks identical to a live one holding a steady setpoint, and
  `grid-central` only falls back to base case after its own restart.
  Keep this in mind when debugging "why did the power flow freeze".

## Quick links

- Reference cluster dashboard: <https://dashboard.cozystack-demo.org>
- Demo deployment (when running):
  - `ieee9-grid`: <https://ieee9.cozystack-demo.org>
  - `ieee9-diesel`: <https://diesel.cozystack-demo.org>
- GHCR org: <https://github.com/orgs/Begonia-UC3/packages>
- Upstream pattern: <https://github.com/cozystack/external-apps-example/pull/2>
