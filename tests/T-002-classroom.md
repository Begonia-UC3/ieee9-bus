# T-002 — classroom mode: 4 student tenants, 4th DSO breaks NR

**When:** TBD (empirical run to be filled after first full class walkthrough)
**Branch:** `classroom`
**Commit tested:** `sha-<head-at-run>`

## Purpose

Validate the classroom flow end-to-end:

- 4 isolated student tenants (`tenant-group1..tenant-group4`) each
  behave like an independent operator.
- Each group uses the Cozystack dashboard to create an
  `Ieee9Zone mode=dso` CR against their own tenant.
- Physics tuning (V-clamp tightened to 0.85) causes NR to diverge
  on the 4th DSO deployment — the intended teaching signal.

This is the tenant-isolated counterpart to T-001. Where T-001
validated *one* operator using the dashboard, T-002 validates
*four parallel* operators without cross-tenant interference.

## Pre-state (after `deploy/classroom/README.md` apply sequence)

- Flux tracks `classroom`.
- `tenant-root` holds `Ieee9Grid/demo` + `Ieee9Zone/diesel`.
  **No** primary DSO (deleted for a clean slate).
- `tenant-dsos` exists but empty (sandbox).
- `tenant-group1..tenant-group4` exist, each with standard
  Cozystack CNPs + the egress CNP from `cnps.yaml`.
- One ingress CNP in `tenant-root` accepts from all four.
- `grid-central` image: `ghcr.io/.../grid-central:classroom`
  (V-clamp 0.85).
- `asset_overrides` in grid-central's memory is clean (pod
  restarted after deletions).

## Per-group form values

Each group logs into the shared Cozystack dashboard with their
own Keycloak creds → sees *only* their tenant → **Simulation →
IEEE 9-Bus Zone → Create**.

### Group 1 — bus 4 (transmission)

| Field | Value |
| --- | --- |
| namespace | `tenant-group1` |
| name | `my-dso` |
| mode | `dso` |
| imageTag | `sha-<classroom HEAD>` |
| replicas | `1` |
| gridCentralUrl | `http://grid-central.tenant-root:8000` |
| modelRepoUrl | `https://github.com/Begonia-UC3/dso-model.git` |
| modelRepoBranch | `main` |
| modelPath | `model.json` |
| modelSyncIntervalSeconds | `30` |
| assetId | **`dso-4`** |
| upstreamBusId | **`4`** |
| upstreamPollSeconds | `5` |
| tickInterval | `"1.0"` |
| ingressEnabled | `true` |
| ingressHost | `group1-dso.cozystack-demo.org` |
| ingressClassName | `tenant-root` |
| tlsEnabled | `true` |
| tlsClusterIssuer | `letsencrypt-prod` |

### Group 2 — bus 7 (transmission)

Same as group 1 except:

- namespace: `tenant-group2`
- assetId: **`dso-7`**
- upstreamBusId: **`7`**
- ingressHost: `group2-dso.cozystack-demo.org`

### Group 3 — bus 8 (load bus, replaces 100 MW anonymous load)

Same as group 1 except:

- namespace: `tenant-group3`
- assetId: **`dso-8`**
- upstreamBusId: **`8`**
- ingressHost: `group3-dso.cozystack-demo.org`

### Group 4 — bus 9 (transmission)

Same as group 1 except:

- namespace: `tenant-group4`
- assetId: **`dso-9`**
- upstreamBusId: **`9`**
- ingressHost: `group4-dso.cozystack-demo.org`

## Verification per group

After submit, from anywhere with cluster-admin kubeconfig:

```bash
GROUP=group1  # or group2/3/4
kubectl get ieee9zones.apps.cozystack.io -n tenant-$GROUP
kubectl get pod -n tenant-$GROUP
kubectl logs -n tenant-$GROUP deploy/my-dso -c my-dso --tail=20
```

Closed-loop health (no python REPL on the operator side needed —
cluster-admin runs this):

```bash
kubectl exec -n tenant-$GROUP deploy/my-dso -- python3 -c "
import urllib.request, json
ls = json.loads(urllib.request.urlopen('http://localhost:8000/api/status').read())['last_solve']
print('asset_id:', ls['external_tie']['asset_id'],
      '  converged:', ls['converged'], 'iter:', ls['iterations'])
print('tie:', ls['tie_injection'])
print('upstream_feedback:', ls['upstream_feedback'])
"
```

## Pass criteria by deployment order

| # DSOs | Expected | grid-central on dashboard |
| --- | --- | --- |
| 1 (group 1) | converged, iter ≤7 | bus 4 shows ~180 MW load, all voltages ≥ 0.95 pu |
| 2 (g1 + g2) | converged | buses 4 & 7 active, voltages start to sag at bus 6 (primary-DSO slot now carrying only base-case 90 MW) |
| 3 (g1 + g2 + g3) | converged, possibly tighter iter | bus 8 also active; some PQ buses in 0.86–0.92 pu range |
| **4 (all four)** | **`converged: false iter: 20`** | at least one bus binds 0.85 clamp; `status = undervoltage` across the ring |

Between deploys, give grid-central a few ticks (`TICK_INTERVAL`
is 1 s) for the new override to propagate and the solver to
stabilise before judging.

## Observed on <RUN_DATE> (to be filled after first empirical run)

Placeholder — filled in after the calibration pass:

```
Group 1 (bus 4) deployed at HH:MM:SS:
  — grid-central converged iter=N, voltages 0.xx–0.xx pu
  — closed-loop feedback engaged, upstream_feedback.age < 5s

Group 2 ...

Group 3 ...

Group 4:
  — grid-central converged=FALSE iter=20
  — bus X at 0.85 clamp, bus Y at 0.xx
  — student dashboards still up and showing internal convergence
    (DSOs solve their own feeders happily — only the transmission
    ring is broken)
```

### Calibration iteration log

If the observed outcome didn't match pass criteria on the first
run, each knob change gets a bullet here. Typical knobs:

- `grid-central/main.py` V-clamp lower bound (0.85 ↔ 0.83 ↔ 0.87)
- `diesel-gen/main.py` `RATED_POWER_MW` (500 ↔ 400)

Each change: commit on `classroom` → CI → bump
`Ieee9Grid/demo.spec.imageTag` to new sha → re-test.

## Gotchas expected during student run

From T-001 (all still apply here):

1. **Whitespace in `gridCentralUrl`** — Cozystack form doesn't
   strip. Tell students to double-check.
2. **`imageTag` default** (empty → `:0.1.0`) — `ImagePullBackOff`.
   Hand out the exact `sha-<short>` via a printed sheet / slide.
3. **`assetId` wrong or empty** — reports as `"dso"` and collides
   with any other group reporting as `dso`. Per-group table above
   prevents this if followed.
4. **Wrong bus picked** — dropdown lets them pick any of
   `{4,6,7,8,9}`. If two groups pick the same bus, last-writer-wins
   on the override. Instruct groups to stick to their assignment.

Plus one new for classroom:

5. **Student has no visibility of other groups' DSOs.** The
   Cozystack dashboard filters by tenant RBAC — group 1 can't see
   group 2's CRs. But `grid-central`'s dashboard at
   `ieee9.cozystack-demo.org` is shared (read-only visualisation)
   and shows the combined IEEE-9 state including everyone's
   injections. Make sure students know to watch *there* for the
   global convergence story.

## Recovery paths

If a student's DSO pod stays `CrashLoopBackOff` / `ImagePullBackOff`:

```bash
# Operator patches their CR with a known-good imageTag:
kubectl patch ieee9zones.apps.cozystack.io -n tenant-groupN my-dso \
  --type=merge -p '{"spec":{"imageTag":"sha-73d2ca7"}}'
```

If grid-central keeps phantom overrides after you delete a DSO:

```bash
# No TTL on overrides (behavioural gotcha). Restart clears them.
kubectl rollout restart deploy/grid-central -n tenant-root
```

If someone botches their CR beyond repair:

```bash
# Operator nukes and they re-submit.
kubectl delete ieee9zones.apps.cozystack.io -n tenant-groupN my-dso
```
