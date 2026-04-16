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

## Prerequisites (bring with you to class)

- A **GitHub account** — any personal account. You will host your
  `model.json` in a public repo of yours. No link to any course
  system required.
- **Keycloak login** (`username` + temporary password) for the
  Cozystack dashboard — the instructor hands these out at the
  start of class. One login per team (teams A and B). Not tied to
  your email or GitHub account; it's a local account in
  Cozystack's own Keycloak.
- The current **image sha tag** for `imageTag` in the form
  (something like `sha-9ab221a`). The instructor gives you the
  current value at the start of class — it advances whenever the
  `classroom` branch gets a new commit.

---

## Time budget

Your team has **30–60 minutes** at the dashboard. Plan for:

- **Core (must do, ~30 min):** Steps 1–3 + Experiment A.
  Proves that your DSO deploys, converges internally, is visible
  from the transmission grid, and that model changes propagate
  live.
- **Stretch (~30 min more if available):** Experiment B
  (closed-loop V-feedback — requires your team only).
- **Joint experiment (instructor coordinates):** Experiment C.
  Both teams need an active DSO at the same time. Usually run as
  a short joint session at the end of the class when both teams
  are finished with their individual runs.

If you run out of time, keep your `Ieee9Zone` alive so the next
team can see something live on the shared transmission dashboard,
but hand the interactive dashboard session to them.

---

## What is already provisioned for you

- **Cozystack dashboard:** <https://dashboard.cozystack-demo.org>
  — Keycloak SSO, each team has its own login. You only see your
  own tenant.
- **Your tenant namespace:** `tenant-group1` (team A) or
  `tenant-dsos` (team B).
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
   | imageTag                 | `sha-xxxxxxx` (*the value the instructor gave you*) |
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

## Step 4. Experiment A — live model reload *(core, ~10 min)*

Goal: prove that the GitOps model-reload loop works. No pod
restart, no redeploy — just a `git push` and the whole chain
picks up the change.

**Open two browser windows side-by-side** before you start:

- **Window 1 — your DSO dashboard:** your `ingressHost` (e.g.
  `https://group1-dso.cozystack-demo.org`). Find the field
  labelled **`tie_injection.p_mw`** (how many MW your DSO
  draws from the transmission grid). **Write down the current
  number**, say *158 MW*.
- **Window 2 — transmission dashboard:**
  <https://ieee9.cozystack-demo.org>. Find **bus 4** (team A)
  or the bus you picked (team B). **Write down the current
  `Load` value shown on your bus**, say *162 MW*.

**Then push a small edit.** In your GitHub repo, open
`model.json`, change one PQ bus's `p_load_mw` by about 50%
(e.g. `80 → 120`). Commit + push.

**Watch the two windows for 30–60 seconds.** Both numbers
you wrote down should change:

- DSO window: `tie_injection.p_mw` rises from ~158 → ~200 MW.
- Transmission window: bus 4's `Load` rises from ~162 → ~205 MW.

Nothing restarted. That's the whole point — screenshot both
windows before & after.

> **Voltage numbers barely move with a small load bump.** The
> transmission grid has a 500 MW diesel on bus 2 that easily
> absorbs a 40 MW extra load — so bus voltages move by only
> 0.003–0.005 pu, which you will not see on the voltage
> graphs. The `Load` number on your bus is the primary visual
> signal for Experiment A. If you want to see voltage move,
> go to Experiment B.

---

## Step 5. Experiment B — closed-loop voltage feedback *(stretch, ~15 min)*

> Skip if your team is already at the 30-minute mark; the core
> deliverables (Steps 1–3 + Experiment A) are enough for a pass.

Your DSO does more than push data upstream — it also *listens*
for the voltage at your external bus in IEEE-9 and uses that
as the slack setpoint inside its own network (closed-loop V
feedback). A small Experiment-A bump does not move voltages
enough to see this — you need to push the grid harder.

**Open three browser windows side-by-side:**

1. **Your DSO dashboard.** Find:
   - `upstream_feedback.v_pu` — the voltage the transmission
     grid reports back to you. Baseline ≈ **0.97 pu**.
   - Internal bus voltages on your distribution network
     (e.g. bus 2, 3, 4 inside your DSO). Baseline all
     near **1.00 pu**.
2. **Transmission dashboard** (`ieee9.cozystack-demo.org`).
   Find the **voltage** reading on **your bus** (bus 4 / 7 /
   8 / 9). Baseline ≈ **1.00 pu**.
3. Your GitHub repo editor window.

**Push a large load.** Set all three PQ buses in your model
to heavy values — total ≈ **310 MW** across the DSO. A good
starting point:

| Bus | p_load_mw | q_load_mvar |
|-----|-----------|-------------|
| 2   | 150       | 40          |
| 3   | 100       | 35          |
| 4   | 60        | 20          |

Commit + push. **Wait ~45 seconds**, then look:

- **Transmission dashboard:** voltage on your bus sags from
  ~1.00 → **~0.90 pu**. This is now obvious on the voltage
  graph — needle visibly drops.
- **DSO dashboard — `upstream_feedback.v_pu`:** drops from
  ~0.97 → **~0.90**. This is the new setpoint your DSO got
  from the transmission grid.
- **DSO dashboard — internal bus voltages:** all sag from
  ~1.00 → **~0.88–0.92 pu**, proportionally. This is the
  closed loop working — magistral said "you're stressing me,
  here is a lower voltage reference" and your internal grid
  recalculated accordingly.

Screenshot all three before + after. The three simultaneous
drops (magistral bus V, upstream_feedback V, internal V) are
the full teaching signal of this experiment.

---

## Step 6. Experiment C — the stability edge *(joint session, ~15 min)*

> This experiment is run jointly with the other team at the
> end of the class — both teams need an active `Ieee9Zone`
> with heavy load at the same time. The instructor will call
> everyone together.

One team alone **cannot** break the transmission grid — the
500 MW diesel on bus 2 easily absorbs a single DSO at 310 MW.
But with **both teams** pushing ~310 MW each, the total load
exceeds what slack + diesel can support while keeping every
bus voltage above the 0.80 pu clamp.

**Procedure:**

1. Both teams simultaneously push the "~310 MW" model from
   Experiment B.
2. Watch the **transmission dashboard**
   (`ieee9.cozystack-demo.org`):
   - Voltages on buses 4 and 7/8/9 drop further, heading
     toward 0.85 pu, then 0.80.
   - The **status field on top of the page flips from
     `converged: true` to `converged: false`**, and
     `iterations` hits **20** (the solver hit its iteration
     cap).

Expected:

- `converged: false, iterations: 20` on the transmission
  dashboard — Newton–Raphson gave up. At least one bus has
  hit the lower V-clamp bound.
- This is **not a bug** — it's the physical stability limit
  of the simplified model. Discuss: what does this mean for
  a real grid? What stabilisation measures could help
  (reactive compensation, load shedding, additional
  generation)?

---

## Deliverables

Minimum to pass (from the core 30-minute slot):

1. **URL of your repo** with `model.json`.
2. **Screenshot of Experiment A** — `tie_injection` before and
   after the `git push`.
3. **Short report (1–2 pages)**:
   - Topology you modelled (how many buses, feeders, loads,
     typical line parameters).
   - Baseline load your DSO imposes on the transmission grid.
   - How fast a model change actually propagated from `git
     push` to the transmission dashboard. (Measure the lag.)

Extra credit if time allowed:

4. **Experiment B screenshots** — IEEE-9 bus voltage and
   internal DSO voltage before/after the load bump, and a
   short comment on how much of the upstream voltage change
   propagated into your distribution network.
5. **Experiment C (joint)** — screenshot of the transmission
   dashboard in `converged: false` state, and a sentence on
   what total combined load (team A + team B) was required to
   push NR over the edge.

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
