# IEEE 9-Bus Grid Simulator

Microservices simulation of the IEEE 9-bus test system: a central
Newton–Raphson power-flow engine talking to three asset simulators
(diesel generator, battery BESS, data center) that iteratively push
telemetry and receive setpoints.

## Architecture

```text
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ diesel-gen  │   │   battery   │   │ datacenter  │
│ Bus 2, PV   │   │ Bus 3, PV±  │   │ Bus 5, PQ   │
│ 163 MW      │   │  85 MW      │   │ 125 MW      │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │   POST /api/asset-update           │
       └──────────────┬──┬──────────────────┘
                      ▼  ▼
                ┌──────────────┐
                │ grid-central │  Newton–Raphson PF
                │   IEEE 9-bus │  WebSocket UI
                └──────────────┘
```

| Asset            | Bus | Type       | Rated     |
| ---------------- | --- | ---------- | --------- |
| diesel-gen       | 2   | PV (gen)   | 163 MW    |
| battery          | 3   | PV (±gen)  |  85 MW    |
| datacenter       | 5   | PQ (load)  | 125 MW    |
| slack generator  | 1   | Slack      | balancing |

## Repository layout

```text
.
├── grid-central/       # FastAPI — Newton–Raphson power-flow engine
├── diesel-gen/         # FastAPI — diesel generator physics
├── battery/            # FastAPI — BESS physics
├── datacenter/         # FastAPI — data-center load model
├── init.yaml           # bootstrap GitRepository + HelmRelease for Cozystack
├── packages/
│   ├── core/platform/  # chart registering the ApplicationDefinition + HelmChart
│   └── apps/ieee9-grid/# chart rendering the four Deployments + Services
├── scripts/
│   └── package.mk      # cozyhr apply/diff/delete helpers
├── deploy/
│   └── compose.yaml    # local dev via docker/podman compose
└── .github/workflows/  # image + chart publishing to GHCR
```

Each service ships a `Containerfile` and a `requirements.txt`.

## Local dev — compose

```bash
cd deploy
docker compose up --build
# or: podman-compose up --build
```

- <http://localhost:8000> — grid dashboard
- <http://localhost:8001> — diesel generator
- <http://localhost:8002> — battery BESS
- <http://localhost:8003> — data center

## Cozystack

The app is packaged as a Cozystack external-app. Bootstrap once per
cluster:

```bash
kubectl apply --filename init.yaml
```

That creates a Flux `GitRepository` pointing at this repo and a
`HelmRelease` that deploys `packages/core/platform`, which in turn
registers the `ieee9-grid` `ApplicationDefinition` + the `HelmChart`
source. Afterwards the **IEEE 9-Bus Grid** entry appears in the
Cozystack dashboard (category: Simulation) and can be instantiated
from the UI or via:

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Grid
metadata:
  name: demo
  namespace: tenant-root
spec:
  replicas: 1
  ingressEnabled: false
```

The dashboard-exposed schema matches
[`packages/apps/ieee9-grid/values.schema.json`](packages/apps/ieee9-grid/values.schema.json).

## Images

CI publishes one image per service to GHCR on each push to `main` and on
every `v*` tag:

- `ghcr.io/begonia-uc3/ieee9-bus/grid-central`
- `ghcr.io/begonia-uc3/ieee9-bus/diesel-gen`
- `ghcr.io/begonia-uc3/ieee9-bus/battery`
- `ghcr.io/begonia-uc3/ieee9-bus/datacenter`

Tags: short SHA on main, semver (`v1.2.3` → `1.2.3`, `1.2`, `1`) on
release. Linux/amd64 only.

## API

### grid-central (`:8000`)

| Endpoint                 | Method | Description                         |
| ------------------------ | ------ | ----------------------------------- |
| `POST /api/asset-update` | POST   | Receive telemetry from asset sim    |
| `GET  /api/grid-state`   | GET    | Latest power-flow results (polling) |
| `GET  /api/topology`     | GET    | Static grid topology                |
| `WS   /ws/grid`          | WS     | Real-time power-flow broadcast      |

### Asset simulators (`:8001`–`:8003`)

| Endpoint             | Method | Description                    |
| -------------------- | ------ | ------------------------------ |
| `POST /api/setpoint` | POST   | Change target load/generation  |
| `GET  /api/status`   | GET    | Current simulator state        |
| `WS   /ws`           | WS     | Real-time telemetry stream     |

### Setpoint examples

```bash
# diesel gen at 90 % load
curl -X POST http://localhost:8001/api/setpoint \
  -H "Content-Type: application/json" \
  -d '{"target_load_pct": 90}'

# battery discharging 50 MW
curl -X POST http://localhost:8002/api/setpoint \
  -H "Content-Type: application/json" \
  -d '{"target_power_mw": 50}'

# battery charging 30 MW (negative = charge)
curl -X POST http://localhost:8002/api/setpoint \
  -H "Content-Type: application/json" \
  -d '{"target_power_mw": -30}'

# datacenter IT load at 95 %
curl -X POST http://localhost:8003/api/setpoint \
  -H "Content-Type: application/json" \
  -d '{"it_load_pct": 95}'
```

## Simulation models

### diesel-gen

- Governor droop control (4 % droop)
- U-shaped SFC curve, optimal around 75 % load
- Exhaust temperature, coolant and oil-pressure dynamics
- Ramp rate: 2 %/s

### battery

- NMC cell OCV curve
- Round-trip efficiency 88 %
- SoC limits 5 – 98 %
- Ramp rate 10 MW/s
- Thermal model with HVAC cooling
- Cycle counting and SoH degradation

### datacenter

- Server power = 40 % idle + 60 % × utilisation
- PUE model with ambient temperature dependency
- Diurnal load pattern (business hours)
- Cooling, UPS losses and misc loads broken out
