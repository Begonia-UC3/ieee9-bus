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

**Stage 1 (this branch):** open-loop deploy. Confirms the pod
boots, git-sync pulls the model, the DSO's internal Newton-Raphson
converges, and the dashboard renders at
`dso-bus8.cozystack-demo.org`. `gridCentralUrl` is left at the
in-namespace default and DNS-fails harmlessly in `tenant-dsos`.
`tenant-root` is not touched.

**Stage 2 (separate PR):** closed-loop wiring. Will add
`"dso-8": 8` to `ASSET_BUS_MAP` in `grid-central/main.py`, set the
CR's `assetId: dso-8`, `upstreamBusId: "8"`, and
`gridCentralUrl: http://grid-central.tenant-root.svc.cluster.local:8000`,
and verify Cozystack's inter-tenant CiliumNetworkPolicy permits
the cross-namespace POST.
