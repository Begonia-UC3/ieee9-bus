# HISTORY.md — ieee9-bus project arc

Narrative companion to `CLAUDE.md`. `CLAUDE.md` describes the repo as
it is *now*; this file records how it got there, phase by phase, so a
new contributor (or future Claude session) can recover intent without
re-reading every commit body.

Keep this in sync with `CLAUDE.md`: whenever `CLAUDE.md` is updated to
reflect a new architectural state, append the corresponding phase
here.

## Phase 1 — Scaffold (PRs #1, #3, #4, pre-`dso` branch)

- Imported the 4 original services (`grid-central`, `diesel-gen`,
  `battery`, `datacenter`) with `Containerfile`s and a compose file
  for local dev.
- Built a single `ieee9-grid` Helm chart deploying all four services.
  Added image CI to GHCR, amd64 only.
- Refactored into the **Cozystack external-app** pattern:
  `packages/apps/ieee9-grid` + `packages/core/platform` registering an
  `Ieee9Grid` `ApplicationDefinition`, with Flux pulling the chart
  straight from `GitRepository` (no OCI publish).
- Added Let's Encrypt TLS toggles on ingress and wrote the initial
  `CLAUDE.md`.

## Phase 2 — Abandoned `distributed` branch

- Experimented with splitting `diesel-gen` into its own `Ieee9Diesel`
  kind. Fixed the `reconcileStrategy: Revision` bootstrap bug there
  (chart `version` is pinned at `0.0.0`, so Flux's default
  `ChartVersion` strategy never re-packages).
- Concluded per-asset kinds don't scale. Parked the branch as a
  reference; it does not merge anywhere.

## Phase 3 — `dso` branch, generic Zone (commit `4095edc`)

- Introduced the generic **`Ieee9Zone`** kind with a `spec.mode` enum
  — one ApplicationDefinition covers every external asset role.
- Built the new `dso/` service: its own Newton–Raphson over a
  user-supplied JSON model, aggregating the tie flow upstream to
  `grid-central` as a single asset.
- Added the **git-sync sidecar** hot-reloading the DSO model from an
  operator's public Git repo on mtime change — no pod restarts,
  WebSocket clients survive reload.

## Phase 4 — Topology correction (`c06864d`)

- Realised the right shape is **3 apps**: `Ieee9Grid` (grid-central +
  battery + datacenter bundled, since they're always on) +
  `Ieee9Zone mode=diesel` + `Ieee9Zone mode=dso`.
- Trimmed the `mode` enum to `{diesel, dso}`; battery and datacenter
  moved back inside the central chart.
- Added `"dso" → bus 6` to `ASSET_BUS_MAP` so DSO occupies its own
  outer-bus role rather than piggy-backing on datacenter.

## Phase 5 — Deploy, operability, polish

- `d55bdcf`: devcontainer config granting write access to `dso-model`
  from codespaces; documented operational gotchas (codespace token
  scope, stale `api-resources` cache, branch-switch-as-migration
  semantics). Verified DSO hot-reload end-to-end (40 → 80 MW within
  ~50 s, pod uptime preserved).
- `570f02e`: ingress knobs for battery and datacenter individually;
  replaced DSO's plain table UI with an SVG power-flow diagram
  (branch thickness ∝ |P|, arrows by sign, red at ≥80% rated, ★ on
  tie bus).
- `96eab16`: fixed stale `Ieee9Zone` dashboard description.
- `2f464b9`: **AUTO_MODE** — all assets self-drive gentle synthetic
  walks by default (diesel 40–90% sinusoid over 4 min, battery ±60%
  cycle over 5 min, DSO per-bus ±15% perturbation on 90 s period with
  staggered phase) so the system doesn't look frozen without manual
  setpoints.
- `73d2ca7`: **closed-loop upstream V feedback** — DSO polls
  `grid-central/api/grid-state` every 5 s (configurable via
  `spec.upstreamPollSeconds`) and uses V_pu at its outer bus
  (`spec.upstreamBusId`, default 6) as its internal slack setpoint.
  Isolated asyncio task with 2 s timeout and try/except around every
  fetch; graceful fallback to last-known value, then to the model's
  static `v_setpoint`. Observability via
  `last_solve.upstream_feedback` (value / age / last_error).

## Phase 6 — `dso-bus8` branch: step back, second manual DSO on bus 8

A previous attempt on branch `dso-auto` (now archived as
`dso-auto-archive`) tried to ship a click-deploy `Ieee9ZoneAuto`
kind that would auto-allocate a DSO slot from `{2,3,4}`, create a
child Cozystack `Tenant`, copy a GitHub PAT secret, and have an
init container auto-create the model repo on GitHub. After 12
commits (10 of them fixes for Cilium policy labels, child-tenant
naming, hook ordering, kubectl image choice, label-value rules,
post-renderer YAML fragility, and CI tag gating) the install
pipeline was still too brittle to ship as the first
auto-provisioning iteration.

This phase steps back: the durable platform-level lessons are
extracted into the new **Cozystack platform gotchas** section in
`CLAUDE.md`; the chart code itself stays on `dso-auto-archive` as
a future reference. In its place, a second `Ieee9Zone mode: dso`
is deployed manually into the existing `tenant-dsos` tenant on
outer bus 8 — the only remaining idle PQ load slot on IEEE-9
(buses 5 and 6 already host `datacenter` and the root DSO). The
`ieee9-zone` chart needs no changes; everything is wired on the
CR.

**Stage 1:** open-loop deploy. Confirmed the pod boots, git-sync
pulls the model, the DSO's internal Newton-Raphson converges, and
the dashboard renders at `dso-bus8.cozystack-demo.org`.
`gridCentralUrl` left at the in-namespace default and DNS-failed
harmlessly in `tenant-dsos`. `tenant-root` untouched.

**Stage 2 (commit `bc347e4`, same branch):** closed-loop wiring.
Added `"dso-8": 8` to `ASSET_BUS_MAP` in `grid-central/main.py` and
put `dso-bus8` on `.github/workflows/images.yaml` `push.branches`
(CI now builds `:dso-bus8` tag per push). On the cluster:
Ieee9Grid/demo `imageTag: dso-bus8` (rolled new grid-central with
the updated ASSET_BUS_MAP — pod restart, no client-visible
downtime); Ieee9Zone/dso-bus8 upgraded with `assetId: dso-8`,
`upstreamBusId: "8"`,
`gridCentralUrl: http://grid-central.tenant-root:8000`,
`imageTag: sha-73d2ca7` (forced immutable digest to bypass the
`IfNotPresent` cache — see behavioural gotcha added in this phase);
two hand-applied CiliumNetworkPolicies opened the cross-tenant
channel (templates in CLAUDE.md stage-2 section).

End-state verified: dso-bus8 tie injection 181 MW at bus 8 visible
on `ieee9.cozystack-demo.org`; bus-8 V ≈0.970 pu driving dso-bus8's
internal slack setpoint (upstream_feedback age ≈0.2 s); existing
bus-6 DSO unchanged (tie 171 MW, `asset_id=dso`). Both DSOs
operating in parallel, both reporting fresh to grid-central with
age <1 s.

**Gotchas surfaced during stage 2 recon (added to CLAUDE.md):**
- Cross-tenant L4 traffic is default-blocked despite stock
  Cozystack CNPs reading permissive; opt-in CNP pair required
  (platform gotcha #10).
- Cluster DNS domain is `cozy.local`, not `cluster.local` (platform
  gotcha #11).
- `imagePullPolicy: IfNotPresent` + mutable `:<branch>` tags =
  stale code on the node; override to `sha-<short>` when touching
  feature-critical behaviour (behavioural gotcha).

## Phase 7 — `dso-multi` branch: multiple DSOs per tenant via the dashboard

Generalises the single-DSO-on-bus-8 work of phase 6 into a
dashboard-driven "add another DSO" flow. Operator workflow reduces
to: pick an outer bus from a dropdown, paste a model-repo URL,
submit. No chart-authored tenant creation, no PAT plumbing, no repo
auto-creation (the things that killed `dso-auto` stay out).

Three blockers removed:

1. **Workload-name collision.** `ieee9-zone`'s `_helpers.tpl`
   derived the Deployment / Service name from `mode`, so two
   `mode=dso` instances in the same namespace would both try to
   become `dso`. Changed: for `mode=dso` the workload is named
   after the instance (release name minus Cozystack's `zone-`
   prefix). `Ieee9Zone/dso-bus8` → Deployment `dso-bus8`;
   `Ieee9Zone/dso-bus7` → Deployment `dso-bus7`; no collision.
   Diesel retains its canonical `diesel-gen` name (one diesel per
   tenant is by design).
2. **`upstreamBusId` opened up as an enum.** JSON-schema and the
   platform's openAPISchema mirror now declare
   `"enum": ["4","6","7","8","9"]` — IEEE-9 PQ buses minus bus 5
   (datacenter). The Cozystack dashboard renders it as a dropdown.
3. **`ASSET_BUS_MAP` pre-populated** with `dso-4/7/8/9` on top of
   the existing `dso→6` and `dso-8→8`. No grid-central rebuild per
   new DSO.

Migration impact at Flux switch: the live `tenant-dsos/dso-bus8`
release's workload renamed `dso → dso-bus8`. Helm upgrade deleted
the old Deployment/Service, created the new ones, ingress backend
updated — ~30 s blip. `tenant-root/dso` instance name was already
`dso`, so after trimPrefix the workload name is still `dso` — zero
churn there.

End-state verification (two DSOs live in `tenant-dsos`):
`dso-bus8` (bus 8) and `dso-bus7` (bus 7, transmission bus with no
baseline load). Both converge, both close the loop — each pulls
its outer-bus V from `grid-central.tenant-root` and drives its
internal slack. `grid-central`'s `asset_overrides` shows `dso-7`
and `dso-8` fresh with age < 2 s; buses 7 and 8 on
`ieee9.cozystack-demo.org` carry the respective injections.
`tenant-root` pods restart only for grid-central (expected: the
new ASSET_BUS_MAP needed the bump); battery / datacenter /
diesel-gen / dso — RESTARTS delta 0.

The cross-tenant CNP pair added in phase 6 was **not** changed:
its endpointSelector already matches *any* pod labelled
`app.kubernetes.io/name: dso` in `tenant-dsos`, which remains true
after the workload-rename refactor.
