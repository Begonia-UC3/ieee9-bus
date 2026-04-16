# IEEE 9-Bus Grid Simulator

Interactive simulation of the IEEE 9-bus transmission test system,
packaged as a set of **Cozystack external apps**. A central
Newton–Raphson power-flow engine talks to asset simulators that
iteratively push telemetry and receive setpoints; a distribution-level
DSO simulator runs its own nested Newton–Raphson over an
operator-supplied feeder and aggregates its tie flow upstream as a
single asset.

## Architecture

```text
TSO ring (IEEE-9 transmission, grid-central)
┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
│ diesel-gen │  │   battery  │  │ datacenter │  │    dso     │
│ Bus 2, PV  │  │ Bus 3, PV± │  │ Bus 5, PQ  │  │ Bus 6, PQ  │
│  163 MW    │  │   85 MW    │  │  125 MW    │  │  (tie) ↓   │
└─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
      │  POST /api/asset-update every tick            │
      └───────┬──────────┬────────┬───────────────────┘
              ▼          ▼        ▼
        ┌──────────────────────────┐
        │      grid-central         │  Newton–Raphson PF
        │   IEEE 9-bus topology     │  WebSocket UI
        └──────────────────────────┘
                      ▲
                      │ GET /api/grid-state (upstream V feedback)
                      │
             ┌────────┴────────┐
             │  dso internal   │  nested Newton–Raphson over
             │  4-bus feeder   │  operator Git model (git-sync)
             └─────────────────┘
```

A DSO instance aggregates its internal feeder flow and reports
`tie_injection` upstream as a single asset. A second DSO can be
deployed on a different outer bus (branch `dso-bus8`, bus 8) in its
own Cozystack tenant.

| Asset / role       | Bus | Type       | Rated      | Cozystack CR                           |
| ------------------ | --- | ---------- | ---------- | -------------------------------------- |
| slack generator    |  1  | Slack      | balancing  | inside `Ieee9Grid`                     |
| diesel-gen         |  2  | PV (gen)   |   163 MW   | `Ieee9Zone mode=diesel`                |
| battery            |  3  | PV (±gen)  |    85 MW   | inside `Ieee9Grid`                     |
| datacenter         |  5  | PQ (load)  |   125 MW   | inside `Ieee9Grid`                     |
| dso (primary)      |  6  | PQ (load)  | tie        | `Ieee9Zone mode=dso` (tenant-root)     |
| dso-8 (optional)   |  8  | PQ (load)  | tie        | `Ieee9Zone mode=dso` (tenant-dsos, branch `dso-bus8`) |

## Repository layout

```text
.
├── grid-central/        # FastAPI — IEEE-9 Newton–Raphson engine
├── diesel-gen/          # FastAPI — diesel generator physics
├── battery/             # FastAPI — BESS physics
├── datacenter/          # FastAPI — data-center load model
├── dso/                 # FastAPI — nested DSO NR + model hot-reload
├── packages/
│   ├── apps/
│   │   ├── ieee9-grid/  # chart: grid-central + battery + datacenter
│   │   └── ieee9-zone/  # chart: Ieee9Zone CR (mode: diesel | dso)
│   └── core/platform/   # ApplicationDefinitions + Flux HelmCharts
├── deploy/
│   ├── compose.yaml     # local dev (docker/podman compose)
│   └── stage2/          # hand-applied cluster manifests (dso-bus8)
├── init.yaml            # bootstrap GitRepository + HelmRelease
├── scripts/package.mk   # cozyhr apply/diff/delete helpers
├── CLAUDE.md            # in-depth repo guide (topology, CI, gotchas)
├── HISTORY.md           # phase-by-phase project arc
└── .github/workflows/   # image + chart CI
```

Each service ships a `Containerfile` and `requirements.txt`.

## Branch map

- **`main`** — legacy single-chart scaffold (pre-DSO). Not deployed
  anywhere; kept for history.
- **`dso`** — current canonical line. Introduces `Ieee9Zone` kind with
  `spec.mode ∈ {diesel, dso}`, DSO service with nested NR, git-sync
  sidecar for hot-reload, and closed-loop upstream V feedback.
- **`dso-bus8`** — live branch on the reference cluster. Docs +
  stage-2 extension (`ASSET_BUS_MAP["dso-8"] = 8`, CI for this
  branch). Flux `GitRepository` tracks `dso-bus8`.
- **`distributed`**, **`dso-auto-archive`** — reference-only
  experiments (one-kind-per-asset, and click-deploy auto-provisioning
  respectively). Neither is merged anywhere.

## Local dev — compose

```bash
cd deploy
docker compose up --build        # or podman compose
```

- <http://localhost:8000> — grid-central dashboard
- <http://localhost:8001> — diesel-gen
- <http://localhost:8002> — battery
- <http://localhost:8003> — datacenter
- <http://localhost:8004> — dso

`compose.yaml` mounts `dso/example-model.json` into the `dso`
container so the local stack works without a real Git repo.

## Cozystack deployment

Full guide: `CLAUDE.md` → *Deploying to a Cozystack cluster*.

```bash
kubectl apply --filename init.yaml
```

Bootstrap creates a Flux `GitRepository` and a `HelmRelease` for
`packages/core/platform`, which registers two
`ApplicationDefinition`s: `ieee9-grid` and `ieee9-zone`. Three
deployable CRs appear in the dashboard under **Simulation**:

- `Ieee9Grid` — central plant (`grid-central` + battery + datacenter).
- `Ieee9Zone mode=diesel` — diesel generator on outer bus 2.
- `Ieee9Zone mode=dso` — a distribution operator with its own NR and
  git-sync'd model repo.

A full simulation = one `Ieee9Grid` + one or two `Ieee9Zone`, all in
the same tenant namespace (in-namespace DNS resolves
`http://grid-central:8000` between pods). See `CLAUDE.md` for CR
examples, image-tag notes, and operational gotchas.

### Second DSO on bus 8

On the `dso-bus8` branch, a **second** `Ieee9Zone mode=dso` is deployed
into a separate `tenant-dsos` tenant, attaching to outer bus 8 through
a pair of CiliumNetworkPolicies (cross-tenant traffic is default-blocked).
Manifests and apply order: [`deploy/stage2/`](deploy/stage2/).

## Images

CI (`images.yaml`) publishes 5 images to GHCR, amd64 only, on push to
`main`, `dso`, and `dso-bus8` branches, on PRs (without push), and on
`v*` git tags:

- `ghcr.io/begonia-uc3/ieee9-bus/grid-central`
- `ghcr.io/begonia-uc3/ieee9-bus/diesel-gen`
- `ghcr.io/begonia-uc3/ieee9-bus/battery`
- `ghcr.io/begonia-uc3/ieee9-bus/datacenter`
- `ghcr.io/begonia-uc3/ieee9-bus/dso`

Tags follow `docker/metadata-action` defaults: `:<branch>`,
`:sha-<short>`, semver on `v*` tags, `:latest` on `main`.

## API

### grid-central (`:8000`)

| Endpoint                 | Method | Description                                         |
| ------------------------ | ------ | --------------------------------------------------- |
| `POST /api/asset-update` | POST   | Receive telemetry from an asset simulator           |
| `GET  /api/grid-state`   | GET    | Latest full PF result (buses, branches, overrides)  |
| `GET  /api/topology`     | GET    | Static IEEE-9 topology                              |
| `WS   /ws/grid`          | WS     | Real-time PF broadcast                              |

### Asset simulators (`:8001`–`:8003`)

| Endpoint             | Method | Description                 |
| -------------------- | ------ | --------------------------- |
| `POST /api/setpoint` | POST   | Change target load/gen      |
| `GET  /api/status`   | GET    | Current simulator state     |
| `WS   /ws`           | WS     | Real-time telemetry stream  |

### dso (`:8004`)

| Endpoint              | Method | Description                                   |
| --------------------- | ------ | --------------------------------------------- |
| `GET  /api/status`    | GET    | Last internal solve + upstream feedback state |
| `GET  /api/model`     | GET    | Current loaded model (from git-sync mount)    |
| `GET  /`              | GET    | SVG power-flow diagram UI                     |

Model JSON contract (`s_base`, `buses[]`, `branches[]`,
`external_tie`) is documented at the top of `dso/main.py`.

### Setpoint examples

```bash
curl -X POST http://localhost:8001/api/setpoint \
  -H "Content-Type: application/json" -d '{"target_load_pct": 90}'

curl -X POST http://localhost:8002/api/setpoint \
  -H "Content-Type: application/json" -d '{"target_power_mw": 50}'

curl -X POST http://localhost:8003/api/setpoint \
  -H "Content-Type: application/json" -d '{"it_load_pct": 95}'
```

Without manual setpoints, all assets self-drive via **AUTO_MODE**
(diesel 40–90 % sinusoid, battery ±60 % cycle, DSO per-bus ±15 %
perturbation) so the dashboard doesn't look frozen.

## Simulation models

### diesel-gen

Governor droop (4 %), U-shaped SFC curve optimal at 75 % load,
exhaust temperature / coolant / oil-pressure dynamics, ramp rate 2 %/s.

### battery

NMC cell OCV, round-trip efficiency 88 %, SoC limits 5–98 %, ramp
rate 10 MW/s, thermal model with HVAC cooling, cycle counting +
SoH degradation.

### datacenter

Server power = 40 % idle + 60 % × utilisation. PUE model with ambient
temperature dependency, diurnal load pattern (business hours),
cooling / UPS losses / misc broken out.

### dso

Nested Newton–Raphson over an operator-supplied JSON grid model
(4-bus example feeder included). Reports aggregate tie injection
upstream as a single asset (`asset_id` → outer bus via
`grid-central`'s `ASSET_BUS_MAP`). Model hot-reloads on mtime change
from a git-sync sidecar — no pod restart. Optional closed-loop
upstream V feedback: polls grid-central for V at the outer tie bus
and uses it as the DSO's internal slack setpoint.

## Further reading

- `CLAUDE.md` — detailed repo guide, deployment, CI, gotchas.
- `HISTORY.md` — phase-by-phase project arc.
- `deploy/stage2/README.md` — how to bring up the second DSO (bus 8).
- Upstream pattern: <https://github.com/cozystack/external-apps-example/pull/2>.
