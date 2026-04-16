# Multi-tenant architecture — how the simulation splits across Cozystack tenants

The IEEE-9 simulation is deliberately *not* a single HelmRelease.
Packing everything into one chart would conflate the transmission
operator's concerns (generators, central Newton–Raphson, grid-wide
observability) with each distribution operator's (their own feeder,
model repo, chart version, failure domain). Cozystack's tenant model
fits naturally: one tenant per operator.

This file documents the current layout, the cross-tenant wiring, and
what's intentionally left to grow on the next branch.

## Current tenant layout

```
┌──────────────────────────────────────────────────────────────┐
│ tenant-root                                                    │
│                                                                │
│   Ieee9Grid/demo                                               │
│     ├─ grid-central   (IEEE-9 NR engine + dashboard)           │
│     ├─ battery        (bus 3, PV ± 85 MW)                      │
│     └─ datacenter     (bus 5, PQ 125 MW)                       │
│                                                                │
│   Ieee9Zone/diesel    (bus 2, PV 500 MW — central dispatch)    │
│   Ieee9Zone/dso       (bus 6, PQ — primary DSO tie)            │
│                                                                │
│   Child: tenant-dsos                                           │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ tenant-dsos (child of tenant-root)                             │
│                                                                │
│   Ieee9Zone/dso-bus7  (reports as dso-7 → outer bus 7)         │
│   Ieee9Zone/dso-bus8  (reports as dso-8 → outer bus 8)         │
│   Ieee9Zone/dso-*     (future: dso-4, dso-9 slots available)   │
│                                                                │
│   Reuses tenant-root's ingress controller                      │
└──────────────────────────────────────────────────────────────┘
```

### Who lives where, and why

**`tenant-root`** holds the "system operator" side: the central
Newton–Raphson solver and the always-on physical assets that don't
have a distribution operator (diesel, battery, datacenter, the
dashboard-facing grid-central). Also the primary DSO — one DSO is the
reference operator on outer bus 6; it's baked into the central topology
and shares the same failure domain as the rest of the central plant.

**`tenant-dsos`** is a Cozystack *child tenant* (Tenant CR `dsos` in
`tenant-root`'s namespace) dedicated to "secondary" distribution
operators — operators who attach via click-deploy to any of the idle
outer PQ buses (4, 7, 8, 9). The separation buys:

- **Independent failure domain.** A misbehaving DSO on bus 7 doesn't
  get to sit on `tenant-root`'s RBAC, Git source, or quota.
- **Distinct model repos per operator.** Each DSO pulls from its own
  public Git repo via `git-sync`; different operators can push feeder
  updates without coordinating.
- **Operator-level resource accounting.** Cozystack dashboards show
  per-tenant CPU / memory / storage, useful if this grows past a demo.
- **A natural place to expand.** When adding a 4th or 5th DSO, there's
  already a tenant for them — no new tenant plumbing per operator.

Currently `tenant-dsos` reuses **`tenant-root`'s ingress controller**
(label `namespace.cozystack.io/ingress=tenant-root` on the child
namespace). That's why every `Ieee9Zone` CR in `tenant-dsos` sets
`ingressClassName: tenant-root` — it's not a typo, it's the shared
ingress class. Keeps DNS and TLS (`letsencrypt-prod`) consistent
across all DSOs under one `*.cozystack-demo.org` wildcard cert chain.

## DNS inside the cluster

Cluster domain is **`cozy.local`**, not the usual `cluster.local` — a
Cozystack decision. Pod `/etc/resolv.conf`:

```
search tenant-dsos.svc.cozy.local svc.cozy.local cozy.local
nameserver 10.96.0.10
options ndots:5
```

That gives three working Service-DNS forms for a same-namespace
service (say `grid-central` in `tenant-root`, as seen *from*
`tenant-root`):

- `grid-central` — relies on `tenant-root.svc.cozy.local` search entry
- `grid-central.tenant-root` — explicit namespace, search suffix
- `grid-central.tenant-root.svc.cozy.local` — full FQDN

And one working form cross-namespace (*from* `tenant-dsos`):

- `grid-central.tenant-root` ✓
- `grid-central.tenant-root.svc.cozy.local` ✓
- `grid-central.tenant-root.svc.cluster.local` ✗ — wrong domain

The short `grid-central.tenant-root` is what the `Ieee9Zone`
click-deploy form takes — relies on the shared search suffix.

## Cross-tenant network policy

Cozystack wires a standard CNP bundle into every tenant namespace
(`allow-internal-communication`, `allow-external-communication`,
`allow-to-dns`, etc.). None of those actually permit arbitrary
pod-to-pod traffic across tenants — `allow-internal-communication`
scopes `fromEndpoints: [{}]` to the policy's own namespace, and
`allow-external-communication`'s `fromEntities: [cluster]` doesn't
cover pod-to-pod in practice. So a DSO in `tenant-dsos` trying to
reach `grid-central` in `tenant-root` gets a silent L4 drop — DNS
resolves, TCP times out.

Fixed by the pair in
[`../deploy/stage2/cnp-cross-tenant.yaml`](../deploy/stage2/cnp-cross-tenant.yaml):

- **Egress** in `tenant-dsos`: any pod labeled
  `apps.cozystack.io/application.kind=Ieee9Zone` can TCP-8000 out
  to pods labeled `app.kubernetes.io/name=grid-central` in
  `tenant-root`.
- **Ingress** in `tenant-root`: `grid-central` accepts TCP-8000 from
  pods labeled `apps.cozystack.io/application.kind=Ieee9Zone` in
  `tenant-dsos`.

Both sides are required — Cilium's default-deny semantics need the
permission at both ends.

The selector uses the Cozystack-auto-injected label
`apps.cozystack.io/application.kind` rather than the chart's own
`app.kubernetes.io/name` because the latter varies per DSO instance
(`dso-bus7`, `dso-bus8`, …) after the workload-name refactor. The
kind label is stable.

### One-time cluster prerequisite

The CNP pair is hand-applied — it's deliberately *not* rendered by the
`ieee9-zone` chart. The abandoned `dso-auto` branch tried
cross-namespace chart rendering and collapsed under the complexity.
Putting one pair of CNPs in a cluster manifest is simpler and matches
the "one cluster, one install" cadence.

Once applied, the CNPs cover every present and future DSO in
`tenant-dsos` — no per-DSO CNP needed.

## What click-deploy does *not* do

Deliberately out of scope (the abandoned `dso-auto` attempt tried
these and collapsed):

- **Auto-create child tenants.** Operators bring their own
  `tenant-dsos` or whatever child namespace they want to live in.
- **Auto-provision GitHub repos for the model JSON.** The operator
  already has a public repo; they paste the URL into the form.
- **Auto-copy PATs or any other cluster-scoped secret.** Nothing needs
  a PAT when the repo is public.
- **Auto-allocate outer bus slots.** The operator picks from the
  dropdown; if they pick a bus already in use, their DSO just
  overwrites the override (by design — the in-memory `ASSET_BUS_MAP`
  is last-writer-wins).

All of the above are legitimately useful but compound with Cozystack-
specific fragility (tenant naming rules, hook-ordering, init-container
RBAC, cross-namespace chart rendering) — they're in the "attempt again
later" column, not "ship v1".

## Futures for the next branch

Rough buckets of things that feel natural to tackle next, in rough
order of value:

1. **More outer-bus slots.** Only buses 4, 6, 7, 8, 9 are DSO
   candidates today. The PV buses 2, 3 could host "virtual power
   plant" aggregators — operators that inject gen, not load.
2. **Per-operator observability.** Cozystack gives per-tenant
   Grafana/VictoriaMetrics out of the box; hook each DSO's metrics
   (tie P/Q, upstream V, internal NR iter counts, model reload
   events) into that.
3. **Multi-tenant auth & UI split.** The `grid-central` dashboard is
   currently one URL for the whole cluster. Operators could have a
   per-tenant view that only shows their own DSO(s) + the outer buses
   they care about, without losing the system operator's global view.
4. **`Ieee9ZoneAuto` revisit.** With the stable chart base of
   `dso-multi` now known to work, a future attempt at full
   click-deploy-with-repo-provisioning could reuse the lessons
   captured on `dso-auto-archive` — this time without the
   cross-namespace chart rendering that torpedoed v1.
5. **Realistic physics extensions.** Line temperature models
   (reducing rated MVA under ambient heat), dynamic reactive power
   limits on PV buses, SCADA-style event streaming from asset sims to
   grid-central. Mostly `grid-central` / asset Python changes.
6. **Disaster recovery tests.** tests/T-002 onwards — what happens if
   `tenant-root` loses the ingress controller, if Flux switches
   branches mid-flight, if a DSO's model repo 404s. Formalise the
   recovery.

This file gets re-edited on each of those branches — start here when
picking the next one up.
