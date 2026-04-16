# Manual test log

Reproducible manual test procedures for flows that can't easily be
covered by helm lint / unit tests — e.g. the Cozystack dashboard
click-deploy path. Each test has:

- **When & what**: date + branch/commit under test.
- **Pre-state**: what the cluster looks like going in.
- **Steps**: what to click / run.
- **Expected vs observed**: the pass criteria + a snapshot from a
  real execution.
- **Gotchas surfaced**: anything that would have broken a naïve
  operator, so the next run is faster.

Tests live alongside the code so they version with the chart and
schema changes they exercise.

---

## T-001 — click-deploy `Ieee9Zone mode=dso` via Cozystack dashboard

**When:** 2026-04-16  
**Branch:** `dso-multi`  
**Commit tested:** `00dd721` (+ docs polish on top)

### Purpose

End-to-end validate the "add another DSO by filling a form" flow on
the `dso-multi` branch:

- Chart refactor (instance-scoped workload names for `mode=dso`) lets
  a new DSO coexist with existing ones in the same tenant namespace.
- `upstreamBusId` dropdown exposes the IEEE-9 PQ buses.
- Pre-populated `ASSET_BUS_MAP` routes `dso-7` to bus 7 without a
  grid-central rebuild.
- Existing hand-applied cross-tenant CiliumNetworkPolicy pair
  (selecting `apps.cozystack.io/application.kind=Ieee9Zone`) covers
  any new DSO instance automatically.

### Pre-state

- `tenant-root`: `Ieee9Grid/demo`, `Ieee9Zone/diesel` (bus 2),
  `Ieee9Zone/dso` (bus 6).
- `tenant-dsos`: `Ieee9Zone/dso-bus8` (bus 8) deployed via
  `kubectl apply` (not button) earlier in the session.
- Cross-tenant CNP pair already applied (age 119m at test start).

### Step 1 — delete the existing `dso-bus7` to clean the slate

Either via dashboard: **Simulation → IEEE 9-Bus Zone → `dso-bus7`
→ Delete**, or CLI:

```bash
kubectl delete ieee9zones.apps.cozystack.io -n tenant-dsos dso-bus7
```

Verify gone:

```bash
kubectl get ieee9zones,helmrelease,deploy,svc,ingress -n tenant-dsos | grep dso-bus7
# expected: empty
```

Grid-central's `asset_overrides.dso-7` entry **persists** (no TTL,
behavioural gotcha). Bus 7 keeps reading ~160 MW until the new DSO's
first tick overwrites. Harmless — don't restart grid-central.

### Step 2 — create via Cozystack dashboard

**Simulation → IEEE 9-Bus Zone → Create**. Fields:

| Field | Value | Why it matters |
| --- | --- | --- |
| namespace | `tenant-dsos` | Not `tenant-root` — primary DSO lives there on bus 6 |
| name | `dso-bus7` | Becomes the Deployment/Service name (per-instance under the workload-name refactor) |
| mode | `dso` | — |
| imageTag | `sha-73d2ca7` | Immutable digest — bypasses kubelet's IfNotPresent cache |
| gridCentralUrl | `http://grid-central.tenant-root:8000` | Default `http://grid-central:8000` won't resolve in `tenant-dsos` |
| modelRepoUrl | `https://github.com/Begonia-UC3/dso-model.git` | — |
| modelRepoBranch | `main` | — |
| modelPath | `model.json` | — |
| modelSyncIntervalSeconds | `30` | — |
| assetId | `dso-7` | **Critical.** Empty → model's `"dso"` → collides with primary DSO on bus 6 |
| upstreamBusId | `7` (dropdown) | — |
| upstreamPollSeconds | `5` | — |
| ingressEnabled | `true` | — |
| ingressHost | `dso-bus7.cozystack-demo.org` | Covered by wildcard DNS |
| ingressClassName | `tenant-root` | `tenant-dsos` reuses root's ingress controller |
| tlsEnabled | `true` | — |
| tlsClusterIssuer | `letsencrypt-prod` | — |
| replicas | `1` | — |
| tickInterval | `"1.0"` | — |

Submit.

### Step 3 — verify

kubectl handles (using plural to bypass discovery-cache staleness):

```bash
kubectl get ieee9zones.apps.cozystack.io -n tenant-dsos dso-bus7
kubectl get helmrelease -n tenant-dsos zone-dso-bus7
kubectl get pod -n tenant-dsos -l app.kubernetes.io/name=dso-bus7
```

Closed-loop + upstream view:

```bash
# local: dso-bus7's own solve + upstream feedback
kubectl exec -n tenant-dsos -c dso-bus7 deploy/dso-bus7 -- python3 <<'PY'
import urllib.request, json
ls = json.loads(urllib.request.urlopen('http://localhost:8000/api/status').read())['last_solve']
print('asset_id:', ls['external_tie']['asset_id'],
      '  converged:', ls['converged'], 'iter:', ls['iterations'])
print('tie:', ls['tie_injection'])
print('upstream_feedback:', ls['upstream_feedback'])
print('slack V:', ls['buses'][0]['v_pu'])
PY

# grid-central: sees the new DSO?
kubectl exec -n tenant-root -c grid-central deploy/grid-central -- python3 <<'PY'
import urllib.request, json
d = json.loads(urllib.request.urlopen('http://localhost:8000/api/grid-state').read())
print('converged:', d['converged'], 'iter:', d['iterations'])
for aid in ('dso','dso-7','dso-8'):
    ovr = d['asset_overrides'].get(aid)
    if ovr:
        print(f'  {aid:6s} p_load={ovr.get("p_load_mw",0):7.2f} age={d["timestamp"]-ovr["last_update"]:.1f}s')
for b in d['buses']:
    if b['id'] in (6, 7, 8):
        print(f'  bus {b["id"]}: v={b["v_pu"]:.3f} p_load={b["p_load_mw"]:7.2f} {b["status"]}')
PY
```

### Pass criteria

- Ieee9Zone CR `READY True` within ~30s of submit.
- Pod `2/2 Running`, zero restarts.
- `last_solve.converged == true`, `iter ≤ 10`.
- `upstream_feedback.v_pu` non-null, `age_s < 10`.
- Local `slack V` == `upstream_feedback.v_pu` (closed loop engaged).
- `grid-central`: `asset_overrides.dso-7` present with `age < 2s`;
  bus 7 `status = nominal`; overall `converged = true`.
- Other DSOs (bus 6, bus 8) unaffected.

### Observed on 2026-04-16

```
dso-bus7 local:
  asset_id = dso-7   converged iter=3
  tie P=160.4 MW  Q=40.8 MVAr
  upstream V@7 = 0.9953   age=4.44s   no errors
  slack V = 0.9953  (tracks upstream)

grid-central:
  converged iter=4
  dso      bus 6: v=0.974  p_load=160.57  nominal
  dso-7    bus 7: v=0.991  p_load=160.40  nominal   ← from click
  dso-8    bus 8: v=0.977  p_load=160.56  nominal
  slack    bus 1: 304.5 MW  v=1.040
```

PASSED.

### Gotchas surfaced

1. **Whitespace in `gridCentralUrl`.** First submit had a leading
   space — `" http://grid-central.tenant-root:8000"`. Cozystack's
   form doesn't strip. Under httpx this may or may not parse
   cleanly depending on version; remove before submit. Worth a
   schema `pattern: '^https?://'` constraint in a follow-up.
2. **`kubectl get ieee9zone ...` (singular) fails** with "the server
   doesn't have a resource type". Discovery-cache staleness
   (CLAUDE.md operational gotcha). Use plural `ieee9zones` or the
   `.apps.cozystack.io` suffix.
3. **`imageTag` default (empty → `:0.1.0`) is still a trap.** The
   docstring says "Override image tag. Defaults to the chart
   appVersion." An operator who leaves it empty gets
   `ImagePullBackOff` on a non-existent tag. Worth a follow-up:
   either `default: "dso-multi"` in the schema (mutable but pulls
   something real) or a chart-level guard that errors on empty
   with a helpful message.
4. **`assetId` default (`""`) would collide with the primary DSO.**
   The chart doesn't auto-derive from `upstreamBusId`. Operator
   must fill, currently documented but easy to miss. Worth a
   follow-up: template-side `{{- default (printf "dso-%s"
   .Values.upstreamBusId) .Values.assetId }}`.

### Follow-ups this test suggests

- Tighten `gridCentralUrl` schema (`pattern` / min length checks).
- Make `assetId` auto-derive from `upstreamBusId` when empty, so
  the "wrong-assetId collision" risk goes away.
- Change `imageTag` default to `dso-multi` (or whichever branch
  Flux currently tracks) so empty submissions at least pull.
- Add `pattern: "^(dso-[4-9]|dso)$"` on `assetId` once auto-derive
  is in, to cap the valid values to what `ASSET_BUS_MAP` knows.
