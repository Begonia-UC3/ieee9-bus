# Classroom mode — operator runbook

Two student tenants, each lets one group click-deploy their own DSO
through the Cozystack dashboard. Groups take turns — the cluster
hosts at most one active `Ieee9Zone mode=dso` per tenant at any
given time.

> **Downsized 2026-04-16 from 4 tenants to 2.** The original plan
> was four isolated `tenant-group<N>` tenants with V-clamp tuned to
> 0.85 so the 4th DSO would deliberately fail NR — a teaching
> signal about grid stability. But the single-node dtu-cluster
> (12 vCPU) couldn't host four full Cozystack tenants (~3.5 vCPU
> overhead *each*, just for the observability stack). The 4-tenant
> layout left zero headroom for the actual DSO pods. Adding a
> second node was deemed risky for the demo window. The demo now
> reuses `tenant-dsos` (historical multi-DSO sandbox namespace) as
> the second classroom tenant — no dedicated `tenant-group2..4`.
> With only 2 concurrent DSOs the 4th-DSO-fails signal no longer
> applies, so V-clamp was widened back to `[0.80, 1.15]` for
> reliable convergence.

This folder is the cluster-state side; the chart + CI live on the
`classroom` branch. Student-facing task description is in
[`assignment.md`](assignment.md) — share that with the students,
keep this README for yourself.

## Apply order

Assumes Flux already tracks the `classroom` branch (see step 4
below if not). Run from a host with cluster-admin kubeconfig.

```bash
# 1. Student tenant. tenant-group1 is new; tenant-dsos already
#    exists from the dso-multi-era sandbox and is reused.
kubectl apply -f deploy/classroom/tenants.yaml
kubectl wait --for=condition=Ready -n tenant-root tenant/group1 tenant/dsos --timeout=120s

# 2. Cross-tenant CNP pair: egress for tenant-group1 + ingress in
#    tenant-root accepting from tenant-group1 AND tenant-dsos.
#    (tenant-dsos already has its own egress via
#    deploy/stage2/cnp-cross-tenant.yaml — independent of this
#    file.)
kubectl apply -f deploy/classroom/cnps.yaml

# 3. Clean slate — drop any existing DSOs from tenant-root and
#    tenant-dsos. Restart grid-central afterward so its in-memory
#    asset_overrides forget the deleted DSOs (no TTL — known
#    behavioural gotcha; otherwise the dashboard shows phantom
#    loads at buses 4/6/7/8/9 forever).
kubectl delete ieee9zones.apps.cozystack.io -n tenant-root dso 2>/dev/null || true
kubectl delete ieee9zones.apps.cozystack.io -n tenant-dsos --all 2>/dev/null || true
kubectl delete ieee9zones.apps.cozystack.io -n tenant-group1 --all 2>/dev/null || true
kubectl rollout restart deploy/grid-central -n tenant-root
kubectl rollout status  deploy/grid-central -n tenant-root --timeout=120s

# 4. Repoint Flux to the classroom branch (skip if already there).
kubectl patch gitrepository -n cozy-public ieee9-bus \
  --type=merge -p '{"spec":{"ref":{"branch":"classroom"}}}'
kubectl annotate gitrepository -n cozy-public ieee9-bus --overwrite \
  reconcile.fluxcd.io/requestedAt="$(date +%s)"

# 5. Bump Ieee9Grid/demo to the current classroom-HEAD image so
#    grid-central picks up the widened V-clamp and any other
#    physics tuning. Use the sha tag (immutable) not :classroom
#    (mutable — hits the IfNotPresent cache gotcha).
LATEST_SHA=$(git rev-parse --short=7 HEAD)
kubectl patch ieee9grids.apps.cozystack.io -n tenant-root demo \
  --type=merge -p "{\"spec\":{\"imageTag\":\"sha-$LATEST_SHA\"}}"
kubectl rollout status deploy/grid-central -n tenant-root --timeout=120s

# 6. (Operator side) Set up 2 Keycloak users (one per group) +
#    bind each to its own tenant via Cozystack RBAC. Shared
#    dashboard URL — students see only the tenant they own.
```

## Per-group form values

Each group fills the **IEEE 9-Bus Zone** form on the Cozystack
dashboard with values from the table below. Groups coordinate
to take turns — only one of them has an active `Ieee9Zone` at a
time if CPU headroom is tight.

| Group | namespace       | bus | assetId | upstreamBusId | ingressHost                    |
| ----- | --------------- | --- | ------- | ------------- | ------------------------------ |
| A     | `tenant-group1` |  4  | `dso-4` | `4`           | `group1-dso.cozystack-demo.org` |
| B     | `tenant-dsos`   | 7/8/9 | `dso-7/8/9` | `7/8/9`   | `dsos-dso.cozystack-demo.org`   |

Group B picks one of buses 7, 8, 9 depending on which is free in
`ASSET_BUS_MAP` that session. The IEEE-9 PQ-bus roster is 4, 5
(datacenter, taken), 6 (primary DSO slot, kept empty for classroom),
7, 8, 9 — so group B has three viable choices.

Common to both groups:

- `mode: dso`
- `imageTag: sha-<current-classroom-HEAD>` (immutable, dodges the
  IfNotPresent kubelet cache)
- `gridCentralUrl: http://grid-central.tenant-root:8000`
- `modelRepoUrl: https://github.com/Begonia-UC3/dso-model.git`
- `modelRepoBranch: main`, `modelPath: model.json`
- `ingressClassName: tenant-root`, `tlsClusterIssuer:
  letsencrypt-prod`

## Physics calibration

Current knobs on the `classroom` branch:

- `grid-central/main.py` V-clamp: `[0.80, 1.15]` (widened back to
  match `dso-multi` after the 2-tenant downsize).
- `diesel-gen/main.py` `RATED_POWER_MW = 500` (unchanged).

Expected behaviour with up to 2 concurrent DSOs:

| DSOs deployed | Expected NR result |
| --- | --- |
| 0                  | converged in ≤5 iter, all buses at base load |
| 1                  | converged, bus-level voltages nominal |
| 2                  | converged, voltages slightly depressed in the loaded quadrant |

If a single DSO already breaks NR on the live cluster, something
else is off (stale asset_overrides from a deleted DSO? grid-central
running an older image?). Grep `grid-central` logs for `converged:
false` and cross-check the `imageTag` on `Ieee9Grid/demo`.

## Teardown / classroom-end cleanup

```bash
# Remove student DSOs (no TTL on grid-central's asset_overrides,
# so restart grid-central afterward to forget them).
kubectl delete ieee9zones.apps.cozystack.io -n tenant-group1 --all
kubectl delete ieee9zones.apps.cozystack.io -n tenant-dsos --all
kubectl rollout restart deploy/grid-central -n tenant-root

# If tearing down the tenants themselves:
kubectl delete -f deploy/classroom/tenants.yaml   # deletes group1
# tenant-dsos is shared with non-classroom use — leave it alone
# unless explicitly reclaiming the namespace.
```
