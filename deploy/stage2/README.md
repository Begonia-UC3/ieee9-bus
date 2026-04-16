# Cluster manifests — DSOs in `tenant-dsos`

Hand-applied manifests for DSOs deployed into the existing
`tenant-dsos` Cozystack tenant. Complementary to the chart — the
chart renders the DSO workload from the CR, but the cross-tenant
CNPs and the CRs themselves live here because they're cluster-state
that Flux does not provision (CR lives in a tenant namespace, CNPs
are cross-namespace).

Full narrative and rationale: `CLAUDE.md` → **Second DSO on bus 8**
and **Adding a DSO via the dashboard**.

## Files

- `cnp-cross-tenant.yaml` — CiliumNetworkPolicy pair opening the
  channel from any DSO pod in `tenant-dsos` (label
  `app.kubernetes.io/name=dso`) to `grid-central` in `tenant-root`.
  One-time per cluster; covers all present and future DSOs in
  `tenant-dsos`.
- `ieee9zone-dso-bus8.yaml` — first click-deploy DSO, bus 8 (load
  slot). Introduced on branch `dso-bus8`.
- `ieee9zone-dso-bus7.yaml` — second click-deploy DSO, bus 7
  (transmission). Introduced on branch `dso-multi` to validate the
  multi-DSO-per-namespace refactor.

## Apply order

Assumes Flux `GitRepository/ieee9-bus` is already pointing at a
branch that registers `ApplicationDefinition/ieee9-zone` and whose
CI has produced matching images.

```bash
# 1. Open the cross-tenant channel (one-time per cluster). Both
#    CNPs must be present — Cilium requires both ends to allow.
kubectl apply -f cnp-cross-tenant.yaml

# 2. Bump tenant-root's grid-central to a tag whose ASSET_BUS_MAP
#    knows about the asset_id each DSO will report as (dso-4, dso-7,
#    dso-8, dso-9). On the `dso-multi` branch, :dso-multi covers
#    all five dsoids. Pick up the rebuild from CI.
kubectl patch ieee9grids.apps.cozystack.io -n tenant-root demo \
  --type=merge -p '{"spec":{"imageTag":"dso-multi"}}'

# 3. Apply one or more DSO instances. Pin imageTag to sha-<short>
#    of the dso-image commit you want, to bypass the IfNotPresent
#    kubelet cache (CLAUDE.md behavioural gotcha).
kubectl apply -f ieee9zone-dso-bus8.yaml
kubectl apply -f ieee9zone-dso-bus7.yaml
```

## Verify closed loop

For each DSO — replace `$NAME` with `dso-bus7` or `dso-bus8`:

```bash
kubectl exec -n tenant-dsos -c dso deploy/$NAME -- python3 -c '
import urllib.request, json
ls = json.loads(urllib.request.urlopen("http://localhost:8000/api/status").read())["last_solve"]
print("asset_id:", ls["external_tie"]["asset_id"])
print("tie_injection:", ls["tie_injection"])
print("upstream_feedback:", ls["upstream_feedback"])
print("slack V:", ls["buses"][0]["v_pu"])
'
```

Expected for each: `upstream_feedback.v_pu` non-null, `age_s < 10`,
`slack V` tracks `upstream_feedback.v_pu`.

On `grid-central.tenant-root` (`/api/grid-state`):
`asset_overrides.dso-7` and `asset_overrides.dso-8` both present
with `last_update` < 2s ago; bus-7 and bus-8 carry their respective
tie injections.
