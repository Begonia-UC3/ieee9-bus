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

The topology is packaged as **three** Cozystack apps:

- `Ieee9Grid` (chart `ieee9-grid`) — deploys `grid-central`, `battery`,
  and `datacenter` together as one central "plant". Inside-tenant
  simulation core.
- `Ieee9Zone` (chart `ieee9-zone`) with `spec.mode: diesel` — the
  diesel generator on outer bus 2.
- `Ieee9Zone` (chart `ieee9-zone`) with `spec.mode: dso` — a DSO on
  outer bus 6, running its own Newton–Raphson over a user-supplied
  grid model pulled from Git via a git-sync sidecar.

`Ieee9Zone` supports exactly two modes (`diesel`, `dso`). Battery and
datacenter are **not** modes — they live inside `Ieee9Grid`.

Asset simulators iteratively POST telemetry to `grid-central`, which
re-runs the power flow and broadcasts results back over WebSocket.

### Branch note

This is branch `dso`, forked from `main`. The diesel-gen split on
branch `distributed` is an older intermediate step (one dedicated
`Ieee9Diesel` kind); it is superseded by the generic `Ieee9Zone`
approach here. Ветка `distributed` does not merge anywhere — it stays
as a reference point. Ветка `dso` is the current direction.

Side branches forked from `dso`:

- `dso-bus8` — first iteration of the second-DSO pattern. Stages
  1+2 on this branch brought up a single manual `Ieee9Zone mode:
  dso` on outer bus 8 in `tenant-dsos`, closed-loop. Superseded by
  `dso-multi`; kept for history.
- `dso-multi` — generalises `dso-bus8`: multiple `Ieee9Zone mode:
  dso` in one tenant (workload name instance-scoped), `upstreamBusId`
  dropdown, `ASSET_BUS_MAP` pre-declares `dso-4/7/8/9`. Kept as
  stable fallback.
- `classroom` — **current Flux target**. Forked from `dso-multi`.
  Adds four isolated student tenants
  (`tenant-group1..tenant-group4`), cross-tenant CNPs per tenant,
  and V-clamp tightened back to 0.85 so that the 4th student DSO
  deliberately fails Newton–Raphson — an explicit teaching signal
  about grid stability. See [`deploy/classroom/README.md`](deploy/classroom/README.md)
  for the operator runbook and [`tests/T-002-classroom.md`](tests/T-002-classroom.md)
  for the per-group form values.
- `dso-auto-archive` — a click-deploy `Ieee9ZoneAuto` kind
  (auto-allocated slot, child Cozystack `Tenant`, GitHub repo
  auto-create) that was prototyped but not shipped — the install
  pipeline turned out too brittle for first delivery. Lessons from
  it are distilled in the **Cozystack platform gotchas** section
  below; full chart code stays on the archived branch as a
  reference if/when auto-provisioning is reattempted.

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
│   ├── apps/ieee9-grid/     # grid-central + battery + datacenter (central plant)
│   │   ├── Chart.yaml       # version 0.0.0 (bumped by upstream tooling)
│   │   ├── Makefile
│   │   ├── values.yaml
│   │   ├── values.schema.json
│   │   └── templates/
│   ├── apps/ieee9-zone/     # separately-deployed asset zone (mode: diesel | dso)
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
     (registers `Ieee9Zone` with `spec.mode` ∈ {diesel, dso}).

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

A full simulation = one `Ieee9Grid` + two `Ieee9Zone` (modes `diesel`
and `dso`), all in the **same** tenant namespace so in-namespace DNS
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
  namespace: tenant-root
spec:
  mode: dso
  modelRepoUrl: "https://github.com/Begonia-UC3/dso-model.git"
  modelRepoBranch: main
  modelPath: model.json
  modelSyncIntervalSeconds: 30
  gridCentralUrl: "http://grid-central:8000"
  ingressEnabled: true
  ingressHost: dso.cozystack-demo.org
  ingressClassName: tenant-root
```

Cozystack renders each `Ieee9Zone` into a `HelmRelease` named
`zone-<instance-name>`. Workload-name derivation depends on `mode`:

- `mode: diesel` → Deployment / Service named `diesel-gen`
  (canonical, singleton per tenant — in-namespace DNS stays
  predictable for assets that need to address it).
- `mode: dso` → Deployment / Service named after the *instance*
  (Release name minus the `zone-` prefix that Cozystack adds). So
  `Ieee9Zone/dso-bus8` renders Deployment `dso-bus8`, and
  `Ieee9Zone/dso-bus7` renders `dso-bus7` — multiple DSOs can
  coexist in the same tenant namespace. DSOs are source-only (they
  POST to grid-central and serve their own UI via Ingress) so
  losing a fixed `dso` service name costs nothing.

Two `Ieee9Zone mode: diesel` in the same namespace would collide
on workload name `diesel-gen` — that's by design (one diesel per
tenant). Two `mode: dso` instances **do not collide** as long as
their CR `metadata.name` differs.

### Adding a DSO via the dashboard (branch `dso-multi`)

> End-to-end validated 2026-04-16 — see
> [`tests/README.md#T-001`](tests/README.md) for the field-by-field
> form values and verification commands.


From the Cozystack dashboard **Simulation → IEEE 9-Bus Zone →
Create** form:

1. `mode` → `dso`.
2. `upstreamBusId` → pick from the dropdown `{4, 6, 7, 8, 9}` (IEEE-9
   PQ buses; 5 is taken by datacenter). **Match this with `assetId`
   below** — e.g. bus 7 → `dso-7`. Free slots depend on who else is
   deployed; `grid-central`'s `ASSET_BUS_MAP` accepts all five, but
   two DSOs reporting as the same `asset_id` overwrite each other.
3. `assetId` → `dso-<N>` where N matches `upstreamBusId`. (Leave
   empty for the conventional `dso → 6` mapping — that's the
   tenant-root primary DSO.)
4. `modelRepoUrl` → the operator's own public Git repo. An example
   4-bus feeder lives at
   <https://github.com/Begonia-UC3/dso-model>.
5. `gridCentralUrl` → `http://grid-central.tenant-root:8000` when
   deploying in `tenant-dsos` (cross-tenant), or leave the default
   in-namespace value when co-deploying in `tenant-root` alongside
   an `Ieee9Grid`.
6. `ingressHost` → something under the cluster's wildcard domain
   (e.g. `dso-bus7.cozystack-demo.org`), `ingressClassName:
   tenant-root`.

Submit. Flux installs a HelmRelease `zone-<name>`, which renders a
Deployment / Service / Ingress named `<name>`. Verify: the Pod
reaches Ready, `/api/status` on the Ingress shows
`converged: true`, and `grid-central.tenant-root`'s
`/api/grid-state` gains an `asset_overrides.<assetId>` entry within
a tick.

**One-time cluster prerequisite** for cross-tenant DSOs (operator
deploying into `tenant-dsos`): the CNP pair in
`deploy/stage2/cnp-cross-tenant.yaml` must be applied once per
cluster. Without it the DSO pod's upstream POSTs time out silently
(Cozystack platform gotcha #10).

### Second DSO on bus 8 (branch `dso-bus8`, manual — historical)

A second `Ieee9Zone mode: dso` is deployed into the existing
`tenant-dsos` tenant namespace, attaching to outer **bus 8** — the
only remaining idle PQ load slot on IEEE-9 (5 = datacenter, 6 = root
DSO). The chart needs no changes; everything is set on the CR:

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Zone
metadata:
  name: dso-bus8
  namespace: tenant-dsos
spec:
  mode: dso
  imageTag: dso             # :dso branch tag on GHCR (no v0.1.0 tag yet)
  modelRepoUrl: https://github.com/Begonia-UC3/dso-model.git
  modelRepoBranch: main
  modelPath: model.json
  upstreamBusId: ""         # stage 1: open loop (disable closed-loop V feedback)
  ingressEnabled: true
  ingressHost: dso-bus8.cozystack-demo.org
  ingressClassName: tenant-root
  tlsEnabled: true
  tlsClusterIssuer: letsencrypt-prod
```

**Stage 1**: pure boot test — confirm the pod runs, the git-sync
sidecar pulls the model, and the DSO's internal Newton-Raphson
converges. `gridCentralUrl` is left at the in-namespace default
(`http://grid-central:8000`); since `tenant-dsos` has no such service,
the upstream POSTs DNS-fail harmlessly and the DSO renders its own
dashboard standalone. No changes to `tenant-root`.

**Stage 2**: closed-loop wiring to root `grid-central` cross-namespace.
Three independent pieces:

1. **`ASSET_BUS_MAP` entry** (`grid-central/main.py`):
   `"dso-8": 8`. Without this, POSTs with `asset_id=dso-8` return
   `{"status":"ok","bus":null}` and the injection is silently
   discarded (documented behavioural gotcha). A new
   `grid-central:dso-bus8` image is built by CI when `dso-bus8` is
   in `.github/workflows/images.yaml` `push.branches`.

2. **CR settings on `Ieee9Zone/dso-bus8`**:

   ```yaml
   spec:
     mode: dso
     assetId: dso-8
     upstreamBusId: "8"
     gridCentralUrl: http://grid-central.tenant-root:8000
     imageTag: dso              # dso image itself unchanged
     # ... rest as in the stage-1 template above
   ```

   Note the **short DNS form** `grid-central.tenant-root`. The cluster
   domain is `cozy.local` (not `cluster.local`), so the full FQDN is
   `grid-central.tenant-root.svc.cozy.local`; the short form relies on
   the pod's DNS search list (`tenant-dsos.svc.cozy.local
   svc.cozy.local cozy.local`).

3. **Two CiliumNetworkPolicies** to open the cross-tenant channel.
   Cozystack's default `allow-internal-communication` scopes
   `fromEndpoints: [{}]` to the policy's own namespace only, and
   `allow-external-communication`'s `fromEntities: [cluster]` does
   not, in practice, cover arbitrary pod-to-pod traffic — cross-tenant
   TCP times out until both of these are applied:

   ```yaml
   # egress in tenant-dsos: allow the dso pod to reach grid-central in tenant-root
   apiVersion: cilium.io/v2
   kind: CiliumNetworkPolicy
   metadata:
     name: allow-dso-to-grid-central-tenant-root
     namespace: tenant-dsos
   spec:
     endpointSelector:
       matchLabels: { app.kubernetes.io/name: dso }
     egress:
     - toEndpoints:
       - matchLabels:
           k8s:io.kubernetes.pod.namespace: tenant-root
           app.kubernetes.io/name: grid-central
       toPorts:
       - ports: [{ port: "8000", protocol: TCP }]
   ---
   # ingress in tenant-root: accept from the dso pod in tenant-dsos
   apiVersion: cilium.io/v2
   kind: CiliumNetworkPolicy
   metadata:
     name: allow-from-tenant-dsos-dso-to-grid-central
     namespace: tenant-root
   spec:
     endpointSelector:
       matchLabels: { app.kubernetes.io/name: grid-central }
     ingress:
     - fromEndpoints:
       - matchLabels:
           k8s:io.kubernetes.pod.namespace: tenant-dsos
           app.kubernetes.io/name: dso
       toPorts:
       - ports: [{ port: "8000", protocol: TCP }]
   ```

   Both CNPs must be present; missing either blocks the flow (Cilium
   requires both endpoints to allow). The namespace matcher
   `k8s:io.kubernetes.pod.namespace` is Cilium's way of scoping the
   selector to a specific namespace.

   Kept as hand-applied manifests (not chart-rendered) for the first
   iteration — they're cluster-scoped operator concern, and
   cross-namespace chart rendering added significant complexity on
   the abandoned `dso-auto` attempt. If a second cross-tenant DSO
   ever lands (`dso-4`, `dso-7`, `dso-9`), revisit and move into the
   `ieee9-zone` chart.

After stage 2 is live: POST `asset_id=dso-8` returns
`{"status":"ok","bus":8}`; bus 8 on `ieee9.cozystack-demo.org`
dashboard reflects `dso-bus8`'s tie injection; `dso-bus8`'s
`/api/status.last_solve.upstream_feedback` shows the polled V@bus-8
driving its internal slack setpoint.

### DSO mode specifics

When `spec.mode: dso`, the chart injects a **git-sync** sidecar
(`registry.k8s.io/git-sync/git-sync:v4.3.0`) that clones the operator's
public model repo into a shared `emptyDir` volume at `/model/current/…`.
The main DSO process watches the model file's mtime each tick and
rebuilds its Y-bus + bus state in-place on any change — **no pod
restart**, WebSocket clients survive reloads.

The model JSON contract is documented at the top of `dso/main.py`.
Minimum shape: `s_base`, `buses[]`, `branches[]`, `external_tie`. The
`external_tie.asset_id` must be a key in `grid-central`'s
`ASSET_BUS_MAP` (`diesel-gen`, `battery`, `datacenter`, `dso`). The
default DSO model uses `"dso"` → outer bus 6 (the anonymous 90 MW
load slot). The `Ieee9Zone.spec.assetId` value can override
`external_tie.asset_id` at runtime if the operator wants the same DSO
process to occupy a different outer role.

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
  push to `main`/`dso`/`dso-bus8`, on PRs (without push), and on
  `v*` tags. Cache via `gha` scoped per service. linux/amd64 only.
  Adding a feature branch to the cluster-facing list requires
  editing `push.branches` — otherwise pods trying to deploy that
  branch hit `ImagePullBackOff` on the nonexistent default tag
  (platform gotcha #9).
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

## Cozystack platform gotchas

Distilled from the abandoned `Ieee9ZoneAuto` effort on
`dso-auto-archive`. These are platform-level rules that apply to any
chart in this repo that creates child tenants, runs in-cluster Jobs,
or wires its own RBAC — they're hard to rediscover and easy to lose
when the offending chart is archived. Keep this list in sync with
new findings.

1. **Pod label `policy.cozystack.io/allow-to-apiserver: "true"`** is
   required for any pod in a tenant namespace that needs to call the
   Kubernetes API server. Without it Cozystack's Cilium policy
   silently drops traffic to `10.96.0.1:443`. Bit us in a bootstrap
   Job whose `kubectl wait` hung forever.
2. **Cozystack tenant names cannot contain dashes**; only the
   leading `tenant-` prefix is allowed. `dso-2` is rejected at
   HelmRelease creation ("release name should start with 'tenant-'
   and should not contain any other dashes"). Use `dso2` and embed
   the index without a separator.
3. **Child tenant namespace = `<parent-ns>-<child-name>`**. Parent
   `tenant-dsos` + child `dsoN` yields `tenant-dsos-dsoN`, NOT
   `tenant-dsoN`. Helpers that compute the namespace from just the
   child name will silently target a non-existent namespace.
4. **Provision child `Tenant` CRs as pre-install hooks** (weight ≤
   -10) with `helm.sh/resource-policy: keep`. As regular resources
   they only land after all pre-install hooks finish, so any earlier
   pre-install Job that touches the child namespace times out. The
   `keep` policy means `helm uninstall` leaks the Tenant — explicit
   out-of-band cleanup is the operator's responsibility.
5. **Kubernetes label *values* reject `/`** (only label *key*
   prefixes allow it). Owner-reference labels rendered as
   `<ns>/<name>` are rejected by Cozystack's HelmRelease validator
   with a generic "Invalid value". Use `_` as separator instead.
6. **Duplicate keys in rendered `metadata.labels` silently crash
   Cozystack's post-renderer.** If a chart's common-labels helper
   already emits a key, do not re-emit it inline on the same
   resource — the YAML unmarshal fails and the install never
   proceeds.
7. **kubectl image choice for in-cluster Jobs.** `bitnami/kubectl`
   is no longer anonymously pullable since the 2025 registry
   lockdown; `rancher/kubectl` is distroless so
   `command: ["/bin/sh", "-c"]` fails with "no such file"; use
   **`alpine/k8s:1.30.x`** (busybox + kubectl) for any Job that
   needs both kubectl and a shell.
8. **Init containers can't `apk add` under the pod's non-root
   securityContext.** apk DB writes need UID 0. Override
   `runAsUser: 0` / `runAsGroup: 0` on the init container only and
   leave the main container at 65534.
9. **CI image-tag gating.** `metadata-action` only publishes
   `:<branch-name>` for branches enumerated in
   `.github/workflows/images.yaml` `push.branches`. A new feature
   branch that wants to deploy from the cluster must be added to
   that list, otherwise pods sit in `ImagePullBackOff` on the
   nonexistent default `:0.1.0` tag.
10. **Cross-tenant traffic needs *both* egress and ingress CNPs.**
    Cozystack's stock `allow-internal-communication`
    (`fromEndpoints: [{}]`) scopes to the policy's own namespace
    only, and `allow-external-communication`'s
    `fromEntities: [cluster]` does not, in practice, cover arbitrary
    pod-to-pod cross-namespace TCP. Without a dedicated opt-in CNP
    pair, TCP from a tenant-A pod to a tenant-B Service TIMES OUT
    silently (DNS resolves fine — it's an L4 drop, not an L7 error).
    Template for the pair: see the "Second DSO on bus 8" → Stage 2
    section. Both sides select by
    `app.kubernetes.io/name` and scope cross-namespace via
    `k8s:io.kubernetes.pod.namespace: <target>`. Missing either side
    = still blocked.
11. **Cluster DNS domain is `cozy.local`, not `cluster.local`.**
    `/etc/resolv.conf` in tenant pods shows
    `search <tenant-ns>.svc.cozy.local svc.cozy.local cozy.local`.
    FQDN `<svc>.<ns>.svc.cluster.local` fails DNS. Use either the
    short form `<svc>.<ns>` (relies on the search list) or the
    explicit `<svc>.<ns>.svc.cozy.local`.

## Things to be careful about

- **Don't add a Helm OCI publish step.** Cozystack pulls from
  `GitRepository`. Republishing as OCI duplicates the artefact and
  drifts.
- **Don't add `charts/` again.** Charts live under
  `packages/apps/<name>/`; the `charts/` directory was removed in PR #3
  and shouldn't come back.
- **Don't re-merge zones into ieee9-grid.** `ieee9-grid` (gc + battery
  + datacenter) and `ieee9-zone` (modes diesel + dso) are intentionally
  separate. Battery and datacenter are bundled into the central plant
  because they're always on; diesel and DSO are deployed independently
  per tenant via `Ieee9Zone` because they vary per simulation.
- **Don't add a new kind per asset.** The point of `Ieee9Zone` with
  `spec.mode` is one ApplicationDefinition for all *external*
  assets. A new external asset type = a new image + an added value in
  the `mode` enum + updated schema, not a new kind.
- **Don't bump chart `version`.** Cozystack uses Git revision as the
  artifact version. Bump `appVersion` to track image releases.
- **Don't pin images by digest in templates.** The chart deploys
  `:<appVersion>` (overridable via `imageTag`). Digest pinning is
  done at the registry tag layer.
- **Image tag `:<appVersion>` may not exist on non-default branches.**
  CI's `metadata-action` publishes `:latest` only on `main`, plus
  `:sha-<short>`, `:<branch-name>`, and semver tags from `v*` git
  tags. The chart's default `image: …:0.1.0` only resolves if a
  `v0.1.0` tag was once pushed. When deploying a service that was
  first introduced on a feature branch (e.g., `dso` on branch `dso`),
  `:0.1.0` won't exist yet — set `spec.imageTag` on the CR to the
  branch tag (`dso`) or a `sha-…` tag until the next semver release.
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
- **Slack bus generation is back-computed after NR.** Without the
  back-fill, `P_gen[0]` stays at its static input (0) and the
  dashboard renders bus 1 as `offline`. The back-fill runs after
  the NR loop exits *whether or not it converged* — so when the
  system is overloaded the operator still sees a ballpark
  imbalance rather than a misleading zero. Code: just after the NR
  loop in `grid-central/main.py:solve()` compute
  `S_inj = V_1 · conj(Y_1k · V_k)` and write to
  `self.P_gen[0]` / `self.Q_gen[0]`.
- **NR V clamp depends on the branch.** `dso-multi` sets it at
  `[0.80, 1.15]` to accommodate 3+ co-deployed DSOs.
  **`classroom` tightens back to `[0.85, 1.15]`** on purpose — we
  want the 4th student DSO to push at least one bus below the
  clamp and trigger explicit non-convergence (teaching signal).
  If `converged: false iter: 20` shows up on `classroom` after
  four student DSOs, that's the expected outcome, not a bug.
- **Diesel-gen rated at 500 MW (uprated from 163).** Gives enough
  local generation (AUTO_MODE walks 325-450 MW) to keep the IEEE-9
  ring stable with 3 full-size DSOs. Comfortably converges at 3
  DSOs; 4-5 concurrent DSOs may need a further uprate or a second
  dispatchable generator.
- **`imagePullPolicy: IfNotPresent` + mutable branch tags = stale
  code on the node.** The chart defaults to `IfNotPresent`. When CI
  rebuilds `:<branch>` for an existing branch (e.g. `:dso` gets a
  new digest after a dso-branch push), a node that already cached
  `:<branch>` under its previous digest keeps serving the old
  image — kubelet does not re-pull on spec-identical Deployment
  updates. Symptom: new pod rolls out cleanly, features on HEAD
  missing at runtime. Fix: override `spec.imageTag` to an immutable
  `sha-<short>` tag on the CR (each build gets a unique sha tag, so
  kubelet is forced to pull). Example from dso-bus8 stage-2 recon:
  `:dso` was 14 KB, `:sha-73d2ca7` was 19 KB; same pull path, same
  branch, different content. Discovered when closed-loop
  `upstream_feedback` was missing despite living in dso-branch HEAD.
- **DSO model hot-reload triggers on mtime.** git-sync atomically
  swaps the `current` symlink, but the watched file's mtime may or may
  not change depending on the git object actually changing. If pushes
  to the model repo don't visibly reload, check `kubectl logs -c
  git-sync` and make sure the commit actually modified the target
  file. **Verified end-to-end** on dso branch deploy: a 40 → 80 MW
  load bump reflected in `tie_injection.p_mw` within ~50s of `git
  push`, with pod RESTARTS = 0 (no cold start).
- **DSO `external_tie.asset_id` must match `ASSET_BUS_MAP`.** DSO
  reports as that asset_id to `grid-central`; if the id isn't in
  `ASSET_BUS_MAP`, the injection is silently ignored. Current valid
  values: `diesel-gen`, `battery`, `datacenter`, `dso`. The conventional
  default for the nested-grid concept is `"dso"` → bus 6. Bus 8 is
  reserved for the second manual DSO (`dso-bus8` branch); the
  `ASSET_BUS_MAP` entry `"dso-8": 8` will be added in stage 2 of
  that work.

## Operational gotchas

- **Codespace `GITHUB_TOKEN` is scoped to *this* repo only.** Even if
  the user account has write access to `Begonia-UC3/dso-model` (or
  any other org repo), the codespace's auto-injected token can't push
  there — pushes 403 with "Permission denied to filokot". Two ways to
  extend:
  - Declarative (preferred): `.devcontainer/devcontainer.json` →
    `customizations.codespaces.repositories.<owner/repo>.permissions`
    declares additional repos. Already configured for
    `Begonia-UC3/dso-model` (write). Requires a fresh codespace to take
    effect — existing codespaces keep the old token.
  - Ad-hoc: have the user create a classic PAT
    (`https://github.com/settings/tokens`, scope `repo`) and pipe it
    via `! echo $PAT | gh auth login --hostname github.com
    --git-protocol https --with-token`. Use only as a last resort —
    PATs typed into chat are visible in conversation logs and must be
    revoked immediately after use.
- **`kubectl api-resources --api-group=apps.cozystack.io` is stale**
  after a platform `HelmRelease` upgrade adds/removes
  `ApplicationDefinition`s. The cozystack-api dynamically registers
  these via API aggregation, but `kubectl`'s discovery cache lags. To
  bypass, query the API directly:
  ```bash
  kubectl get --raw /apis/apps.cozystack.io/v1alpha1
  ```
  Returns the live list of currently-registered resources.
- **Switching the `GitRepository` ref between branches is a
  *migration*, not a fresh deploy.** Existing `HelmRelease`s in
  tenant namespaces upgrade in place to the new branch's chart.
  Workloads removed from the chart on the new branch (e.g.,
  `diesel-gen` was removed from `ieee9-grid` on the `dso` branch)
  disappear cleanly via Helm's normal upgrade reconciliation. CRs of
  kinds whose `ApplicationDefinition` is removed (e.g., `Ieee9Diesel`
  when switching `distributed` → `dso`) must be deleted manually
  *before* the switch, otherwise their `HelmRelease`s become orphans.

## Quick links

- Reference cluster dashboard: <https://dashboard.cozystack-demo.org>
- Demo deployment (when running):
  - `Ieee9Grid`: <https://ieee9.cozystack-demo.org>
  - `Ieee9Zone mode=diesel`: <https://diesel.cozystack-demo.org>
  - `Ieee9Zone mode=dso` (bus 6, `tenant-root`): <https://dso.cozystack-demo.org>
  - `Ieee9Zone mode=dso` (bus 8, `tenant-dsos`): <https://dso-bus8.cozystack-demo.org>
- DSO model repo: <https://github.com/Begonia-UC3/dso-model>
- GHCR org: <https://github.com/orgs/Begonia-UC3/packages>
- Upstream pattern: <https://github.com/cozystack/external-apps-example/pull/2>
