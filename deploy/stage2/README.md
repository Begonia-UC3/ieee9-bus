# Stage-2 cluster manifests — dso-bus8

Hand-applied manifests for the second manual DSO on bus 8
(`tenant-dsos` / `Ieee9Zone/dso-bus8`). Complementary to the chart —
the chart renders the DSO workload from the CR, but the cross-tenant
CNPs and the CR itself live here because they're cluster-state that
Flux does not provision (CR lives in a tenant namespace, CNPs are
cross-namespace).

Full narrative and rationale: see `CLAUDE.md` → **Second DSO on bus 8
→ Stage 2**.

## Apply order

Assumes Flux `GitRepository/ieee9-bus` is already pointing at the
`dso-bus8` branch (so `ApplicationDefinition/ieee9-zone` is
registered and CI has produced `grid-central:dso-bus8`).

```bash
# 1. Open the cross-tenant channel. Both CNPs must be present.
kubectl apply -f cnp-cross-tenant.yaml

# 2. Bump tenant-root's grid-central to the image that has
#    ASSET_BUS_MAP["dso-8"] = 8. Picks up the rebuild from CI.
kubectl patch ieee9grids.apps.cozystack.io -n tenant-root demo \
  --type=merge -p '{"spec":{"imageTag":"dso-bus8"}}'

# 3. Create or upgrade the bus-8 DSO. Pin to sha-<short> to bypass
#    the IfNotPresent kubelet cache on the dso image (see CLAUDE.md
#    behavioural gotcha).
kubectl apply -f ieee9zone-dso-bus8.yaml
```

## Verify closed loop

```bash
# from inside the dso-bus8 pod
kubectl exec -n tenant-dsos -c dso deploy/dso -- python3 -c '
import urllib.request, json
ls = json.loads(urllib.request.urlopen("http://localhost:8000/api/status").read())["last_solve"]
print("tie_injection:", ls["tie_injection"])
print("upstream_feedback:", ls["upstream_feedback"])
print("slack V:", ls["buses"][0]["v_pu"])
'
```

Expected: `upstream_feedback.v_pu` non-null with `age_s` < 10 s;
`slack V` follows `upstream_feedback.v_pu` (closed loop engaged);
`asset_overrides.dso-8` present on `grid-central.tenant-root`'s
`/api/grid-state`.
