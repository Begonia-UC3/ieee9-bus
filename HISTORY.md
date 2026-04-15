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
- `73d2ca7` (current HEAD): **closed-loop upstream V feedback** — DSO
  polls `grid-central/api/grid-state` every 5 s (configurable via
  `spec.upstreamPollSeconds`) and uses V_pu at its outer bus
  (`spec.upstreamBusId`, default 6) as its internal slack setpoint.
  Isolated asyncio task with 2 s timeout and try/except around every
  fetch; graceful fallback to last-known value, then to the model's
  static `v_setpoint`. Observability via
  `last_solve.upstream_feedback` (value / age / last_error).

## Phase 6 — `dso-auto` branch: third button, auto-provisioned DSOs

Forked from `dso` HEAD. Adds a **third** marketplace button
`Ieee9ZoneAuto` that click-deploys a full DSO with no user input
beyond a GitHub username.

- **Slack-display fix (precursor).** `grid-central/main.py`
  back-computes the slack injection from the solved NR state
  (`S_inj = V_1 · conj(Σ Y_1k · V_k)`) and writes it into
  `self.P_gen[0]` / `Q_gen[0]` before rendering `bus_results`, so
  bus 1 stops reading "offline". Matters because multi-DSO pushes
  non-trivial net power toward the slack.
- **`ASSET_BUS_MAP` extended** with `dso-2→9, dso-3→7, dso-4→4`
  (the three remaining unclaimed outer PQ buses on IEEE-9).
- **New chart `packages/apps/ieee9-zone-auto/`** — allocates a slot
  at render time via Helm `lookup` scanning Namespaces for label
  `ieee9-zone-auto/index`, picks the lowest free from `{2,3,4}`,
  fails fast on the fourth click. Creates `tenant-dso-N` namespace,
  pre-install Job copies PAT Secret from `cozy-system`, init
  container on the DSO pod auto-creates the public GitHub repo
  `<owner>/dso-N-model` and seeds `model.json` from a packaged 60 kV
  feeder template (buses 9/7/4 each get a 4-bus feeder with ~20 MW
  aggregate load). Git-sync sidecar takes over once the repo exists.
  Post-delete Job deletes the namespace; the repo is deliberately
  left for manual `gh repo delete` (no chart should hold repo-delete
  scope).
- **Platform chart** (`packages/core/platform/templates/cozyrds.yaml`
  + `helmcharts.yaml`) registers the new `ApplicationDefinition` and
  Flux `HelmChart` source. Prefix `zone-auto-`; dashboard form asks
  for `modelRepoOwner` and nothing else.
- **CI** (`.github/workflows/chart.yaml`) lints and templates all
  three charts on push to `main`/`dso`/`dso-auto` and all PRs.

## Net result

A 3-app Cozystack deployment where a TSO-level IEEE-9 flow and a
DSO's internal nested flow interact bidirectionally — DSO reports
aggregated injection up, TSO pushes its boundary voltage back down —
with model changes hot-reloading from a separate Git repo and all
assets moving on their own for demo purposes. On the `dso-auto`
branch, adding further DSOs is a single button click: no manual
namespace, no manual repo, no number to type — slot allocation,
namespace creation, repo provisioning, and cross-tenant wiring are
all automatic, up to the three-slot cap imposed by IEEE-9 topology.
