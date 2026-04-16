# Student assignment — build your own DSO inside a live IEEE-9 grid

## Context

You are an engineer at a **DSO** (Distribution System Operator).
Your distribution network is connected to the IEEE-9 transmission
grid through a single point (the *external tie* at one of buses
4, 7, 8, or 9).

The transmission side is already running on the cluster:

- **`grid-central`** — Newton–Raphson solver for the 9-bus
  IEEE-9 system, plus a web dashboard. Slack bus = bus 1.
- **`diesel-gen`** — diesel generator on bus 2 (PV, 500 MW).
- **`battery`** — BESS on bus 3 (PV±, 85 MW).
- **`datacenter`** — constant 125 MW load on bus 5.

Your job: deploy **your own DSO** — a microservice that runs its
own Newton–Raphson over *your* distribution network (4–5 buses,
60 kV, several feeders and loads), computes the aggregate flow at
the external tie every tick, and reports it upstream to
`grid-central` as a single aggregated value. From the
transmission side your DSO looks like one lumped injection at
your tie bus.

---

## What is already provisioned for you

- **Cozystack dashboard:** <https://dashboard.cozystack-demo.org>
  — Keycloak SSO, each team has its own login. You only see your
  own tenant.
- **Your tenant namespace:** `tenant-group1` (team A) or
  `tenant-dsos` (team B). Teams coordinate and take turns — at
  most one active DSO per team at a time.
- **Your tie bus (external tie to IEEE-9):**
  - Team A → bus **4**, `assetId: dso-4`.
  - Team B picks from the free ones: **7**, **8**, or **9**
    (`assetId: dso-7`, `dso-8`, `dso-9` respectively).
  - Buses 5 (datacenter) and 6 (reserved) are unavailable.
- **Transmission dashboard:** <https://ieee9.cozystack-demo.org>
  — open to everyone, watch the bus voltages on the IEEE-9 ring
  in real time.

---

## Step 1. Prepare your network model

The model is a single JSON file in your **public** GitHub
repository. Example:
<https://github.com/Begonia-UC3/dso-model/blob/main/model.json>

Minimum shape:

```json
{
  "s_base": 100,
  "buses": [
    { "id": 1, "type": "slack" },
    { "id": 2, "type": "pq", "p_mw": 15, "q_mvar": 4 },
    { "id": 3, "type": "pq", "p_mw": 12, "q_mvar": 3 },
    { "id": 4, "type": "pq", "p_mw": 8,  "q_mvar": 2 }
  ],
  "branches": [
    { "from": 1, "to": 2, "r": 0.02, "x": 0.08, "b": 0.0 },
    { "from": 2, "to": 3, "r": 0.03, "x": 0.10, "b": 0.0 },
    { "from": 2, "to": 4, "r": 0.04, "x": 0.12, "b": 0.0 }
  ],
  "external_tie": {
    "bus_id": 1,
    "asset_id": "dso-4"
  }
}
```

Requirements:

1. `external_tie.asset_id` **must exactly match** the `assetId`
   you were assigned (`dso-4`, `dso-7`, `dso-8`, or `dso-9`) —
   otherwise `grid-central` silently drops your data (there is
   no TTL on silent drops, so you won't get an error).
2. The slack bus (`type: "slack"`) is the tie point to the
   transmission grid. `external_tie.bus_id` must point at it.
3. At least 2 buses, at least 1 branch. Reasonable: 4–6 buses.
4. The repository must be **public** — this first iteration has
   no auth wiring for git-sync.

Actions:

1. Fork `Begonia-UC3/dso-model` or create a new public repo
   with a `model.json` file.
2. Edit the model for your scenario — invent feeders, loads,
   topology. The total load determines your baseline tie
   injection: that's what IEEE-9 will see from you.

---

## Step 2. Click-deploy your `Ieee9Zone` through the dashboard

1. Log into the Cozystack dashboard with your Keycloak account.
2. Select your namespace (`tenant-group1` or `tenant-dsos`).
3. **Simulation → IEEE 9-Bus Zone → Create.**
4. Fill the form:

   | Field                    | Value                                          |
   | ------------------------ | ---------------------------------------------- |
   | name                     | something meaningful, e.g. `mydso`             |
   | mode                     | `dso`                                          |
   | imageTag                 | `sha-9ab221a` (*current HEAD of `classroom`*)   |
   | assetId                  | `dso-4` / `dso-7` / `dso-8` / `dso-9`          |
   | upstreamBusId            | `4` / `7` / `8` / `9` (**same number**)        |
   | gridCentralUrl           | `http://grid-central.tenant-root:8000`         |
   | modelRepoUrl             | URL of your repo (ending in `.git`)            |
   | modelRepoBranch          | `main`                                         |
   | modelPath                | `model.json`                                   |
   | modelSyncIntervalSeconds | `30`                                           |
   | ingressEnabled           | `true`                                         |
   | ingressHost              | `<teamname>-dso.cozystack-demo.org`            |
   | ingressClassName         | `tenant-root`                                  |
   | tlsEnabled               | `true`                                         |
   | tlsClusterIssuer         | `letsencrypt-prod`                             |

5. **Submit.** Cozystack provisions a HelmRelease, Flux brings
   up a Deployment with two containers: the main DSO service
   plus a `git-sync` sidecar that clones your model repo.

---

## Step 3. Verify everything is alive

**3.1. Check the Pod and Ingress** (through the dashboard or
via `kubectl`):

```bash
kubectl get pods,ingress -n <your-namespace>
```

The Pod should be `Ready 2/2` (DSO + git-sync). The Ingress
should have a Let's Encrypt certificate.

**3.2. Open your DSO dashboard** at your `ingressHost`. You
should see:

- Your distribution network topology.
- Voltage and current on every bus.
- `converged: True`, `iterations: <...>` — internal NR solved.
- `tie_injection.p_mw` — how many MW flow from IEEE-9 to you.

**3.3. Confirm `grid-central` sees you:**

Open <https://ieee9.cozystack-demo.org>. Find your bus (4, 7,
8, or 9). The load on it should have risen by an amount close
to your `tie_injection.p_mw`. If it's still at the base value,
the transmission grid is ignoring you (see troubleshooting).

---

## Step 4. Experiment A — live model reload

Goal: demonstrate that the GitOps approach to the model works
without any restart.

1. Open your repo → edit `model.json`: bump the load on one PQ
   bus, e.g. `p_mw: 15 → 30`.
2. `git commit` + `git push`.
3. Within **~30–50 seconds** (the git-sync interval):
   - Your DSO dashboard shows `tie_injection.p_mw` changing.
   - <https://ieee9.cozystack-demo.org> — the IEEE-9 bus you
     are connected to shows the new value.
   - **Important:** your Pod does not restart — the model is
     reloaded by mtime. Check the Pod logs:
     `model reloaded`.

---

## Step 5. Experiment B — closed-loop voltage feedback

Your DSO does more than push data upstream — it also *listens*
for the voltage at its external bus in IEEE-9 and uses that as
the slack setpoint inside its own network (closed-loop V
feedback).

1. In a second window, open the transmission dashboard and
   note the voltage at your tie bus (say, 1.00 pu).
2. Increase the load in your model to a large value (total
   ~200 MW across all buses). Push.
3. After 30–50 seconds, look at both dashboards:
   - Transmission: the voltage at your bus has sagged (e.g.
     to 0.92 pu) — IEEE-9 is trying to keep the balance.
   - Your DSO: internal voltages have also sagged
     proportionally, because the slack received the new
     setpoint from the transmission grid.
4. The field `/api/status.last_solve.upstream_feedback` on
   your DSO shows the last V received from upstream.

---

## Step 6. Experiment C — the stability edge

The cluster can host up to 2 active DSOs simultaneously
(team A and team B). Coordinate with the other team and
simultaneously raise the loads in your models until the
voltage at your IEEE-9 bus falls below `0.80 pu`.

Expected:

- The transmission dashboard reports `converged: false,
  iterations: 20` — Newton–Raphson cannot find a solution,
  at least one bus has hit the lower V-clamp bound.
- This is **not a bug** — it's the physical stability limit
  of the simplified model. Discuss: what does this mean for
  a real grid? What stabilisation measures could help
  (reactive compensation, load shedding, additional
  generation)?

---

## Deliverables

1. **URL of your repo** with `model.json`.
2. **Screenshots** from all three experiments:
   - A — `tie_injection` before/after the push.
   - B — IEEE-9 bus voltage and internal DSO voltage
     before/after the load bump.
   - C — transmission in `converged: false` after
     coordination with the neighbouring team.
3. **Short report (1–2 pages):**
   - What topology did you model (how many buses, feeders,
     loads, typical line parameters)?
   - What baseline load does your DSO impose on the
     transmission grid?
   - How fast does a model change actually propagate from
     `git push` to the transmission dashboard? (Measure it.)
   - What did you observe in Experiment C — at what total
     load did the grid collapse, and what was the voltage at
     your tie bus at that moment?

---

## Troubleshooting

- **Ingress has no certificate.**
  Wait 1–2 minutes (ACME HTTP-01 challenge). If it takes
  longer — `kubectl describe certificate` in your namespace.
- **Pod `0/2` or `CrashLoopBackOff`.**
  `kubectl logs <pod> -c dso` and
  `kubectl logs <pod> -c git-sync` — typical cause: typo in
  `modelRepoUrl` (forgot `.git`, or repo is private).
- **DSO converged, but IEEE-9 doesn't reflect your load.**
  Check that `external_tie.asset_id` in your JSON exactly
  matches `spec.assetId` on your `Ieee9Zone`, and that it is
  one of `dso-4 / dso-7 / dso-8 / dso-9` (not `dso-2` or
  `diesel-gen` — that's a safeguard; otherwise you'd overwrite
  somebody else's data).
- **Model doesn't update after `git push`.**
  `kubectl logs <pod> -c git-sync` — git-sync atomically
  swaps a symlink on mtime change. Make sure your commit
  actually modified `model.json` (not just, say, `README.md`).
- **Pod stuck in `Pending`.**
  `kubectl describe pod` → if `Insufficient cpu`, both teams
  are trying to run DSOs at the same time — coordinate with
  the other team, one of you takes their `Ieee9Zone` down for
  the duration of the experiment.
- **Transmission shows a "frozen" stale load at your bus after
  you deleted your Ieee9Zone.**
  `grid-central` has no TTL on `asset_overrides` — ask the
  instructor to restart it
  (`kubectl rollout restart deploy/grid-central -n tenant-root`).

---

## References

- Example model:
  <https://github.com/Begonia-UC3/dso-model>
- DSO service documentation and JSON model contract: see the
  module docstring at the top of `dso/main.py` in the project
  repo.
- End-to-end system description (transmission + DSO):
  [`docs/multitenant.md`](../../docs/multitenant.md) and
  [`docs/newton-raphson.md`](../../docs/newton-raphson.md) in
  the root of the `ieee9-bus` repo.
