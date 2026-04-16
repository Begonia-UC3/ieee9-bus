# Classroom mode — operator runbook

Four student tenants, each lets one group click-deploy their own DSO
through the Cozystack dashboard. Physics is tuned so that **the
first three DSOs converge, the fourth fails Newton–Raphson** — an
explicit teaching signal about grid stability limits.

This folder is the cluster-state side; the chart + CI live on the
`classroom` branch.

## Apply order

Assumes Flux already tracks the `classroom` branch (see step 4
below if not). Run from a host with cluster-admin kubeconfig.

```bash
# 1. Four student tenants. Cozystack provisions namespaces
#    tenant-group1..tenant-group4 and the standard CNP bundle
#    + per-tenant Flux ServiceAccount.
kubectl apply -f deploy/classroom/tenants.yaml
kubectl wait --for=condition=Ready -n tenant-root tenant/group1 tenant/group2 tenant/group3 tenant/group4 --timeout=120s

# 2. Cross-tenant CNP pair: 4 egress (one per student tenant) +
#    1 ingress in tenant-root accepting from all four.
kubectl apply -f deploy/classroom/cnps.yaml

# 3. Clean slate — drop the existing DSOs from tenant-root and
#    tenant-dsos. Restart grid-central afterward so its in-memory
#    asset_overrides forget the deleted DSOs (no TTL — known
#    behavioural gotcha; otherwise the dashboard shows phantom
#    180 MW loads at buses 6, 7, 8 forever).
kubectl delete ieee9zones.apps.cozystack.io -n tenant-root dso 2>/dev/null || true
kubectl delete ieee9zones.apps.cozystack.io -n tenant-dsos dso-bus7 dso-bus8 2>/dev/null || true
kubectl rollout restart deploy/grid-central -n tenant-root
kubectl rollout status  deploy/grid-central -n tenant-root --timeout=120s

# 4. Repoint Flux to the classroom branch (skip if already there).
kubectl patch gitrepository -n cozy-public ieee9-bus \
  --type=merge -p '{"spec":{"ref":{"branch":"classroom"}}}'
kubectl annotate gitrepository -n cozy-public ieee9-bus --overwrite \
  reconcile.fluxcd.io/requestedAt="$(date +%s)"

# 5. Bump Ieee9Grid/demo to the classroom image so grid-central
#    picks up the V-clamp tightening (and any other physics tuning).
kubectl patch ieee9grids.apps.cozystack.io -n tenant-root demo \
  --type=merge -p '{"spec":{"imageTag":"classroom"}}'
kubectl rollout status deploy/grid-central -n tenant-root --timeout=120s

# 6. (Operator side) Set up 4 Keycloak users (one per group) + bind
#    each to its own tenant via Cozystack RBAC. Single shared
#    dashboard URL — students see only the tenant they own.
```

## Per-group form values

Each group fills the **IEEE 9-Bus Zone** form on the Cozystack
dashboard with values from the table below. Full step-by-step
runbook (with verification commands and the gotcha checklist) is
in [`../../tests/T-002-classroom.md`](../../tests/T-002-classroom.md).

| Group | namespace        | bus | assetId | upstreamBusId | ingressHost                   |
| ----- | ---------------- | --- | ------- | ------------- | ----------------------------- |
| 1     | `tenant-group1`  |  4  | `dso-4` | `4`           | `group1-dso.cozystack-demo.org` |
| 2     | `tenant-group2`  |  7  | `dso-7` | `7`           | `group2-dso.cozystack-demo.org` |
| 3     | `tenant-group3`  |  8  | `dso-8` | `8`           | `group3-dso.cozystack-demo.org` |
| 4     | `tenant-group4`  |  9  | `dso-9` | `9`           | `group4-dso.cozystack-demo.org` |

Common to all groups:

- `mode: dso`
- `imageTag: sha-<current-classroom-HEAD>` (immutable, dodges the
  IfNotPresent kubelet cache)
- `gridCentralUrl: http://grid-central.tenant-root:8000`
- `modelRepoUrl: https://github.com/Begonia-UC3/dso-model.git`
- `modelRepoBranch: main`, `modelPath: model.json`
- `ingressClassName: tenant-root`, `tlsClusterIssuer:
  letsencrypt-prod`

## Physics calibration

Starting knobs on the `classroom` branch:

- `grid-central/main.py` V-clamp: `[0.85, 1.15]` (tightened from
  the `dso-multi` `[0.80, 1.15]`).
- `diesel-gen/main.py` `RATED_POWER_MW = 500` (unchanged from
  `dso-multi`).

Expected behaviour as students arrive in order:

| Groups deployed | Expected NR result |
| --- | --- |
| 1                  | converged in ≤7 iter, all buses nominal |
| 1, 2               | converged, voltages slightly depressed |
| 1, 2, 3            | converged, bus 6 (no longer carrying primary DSO) at base 90 MW load only; voltages 0.86–0.95 pu range |
| 1, 2, 3, 4         | **`converged: false iter: 20`** — at least one PQ bus binds the 0.85 clamp |

If the empirical run differs (group 3 already breaks, or group 4
still converges), the calibration knobs are:

- Widen V-clamp to 0.83 to give NR more headroom.
- Or tighten V-clamp to 0.87 to break NR sooner.
- Or reduce `RATED_POWER_MW` to 400 (less local gen, more slack
  pull, voltages depress faster).

Each iteration: code change on `classroom` branch → push → CI
rebuilds → bump `Ieee9Grid/demo.spec.imageTag` to the new
`sha-<short>` → grid-central rolls → re-test.

## Teardown / classroom-end cleanup

```bash
# Drop all student DSOs first (so grid-central doesn't keep their
# phantom loads).
kubectl delete ieee9zones.apps.cozystack.io --all -n tenant-group1 || true
kubectl delete ieee9zones.apps.cozystack.io --all -n tenant-group2 || true
kubectl delete ieee9zones.apps.cozystack.io --all -n tenant-group3 || true
kubectl delete ieee9zones.apps.cozystack.io --all -n tenant-group4 || true
kubectl rollout restart deploy/grid-central -n tenant-root

# Then either keep the tenants for the next class:
kubectl rollout restart deploy/grid-central -n tenant-root

# ...or drop them entirely (uninstalls the per-tenant CNPs as well):
kubectl delete -f deploy/classroom/cnps.yaml
kubectl delete -f deploy/classroom/tenants.yaml
```

(`tenant-dsos` is left alone in either case — it's the
non-classroom sandbox.)
