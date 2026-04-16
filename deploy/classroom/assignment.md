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
- The instructor will hand out three things at the start of class:
  1. **Keycloak username + temporary password** — one login per
     team (teams A and B). Not tied to your email or GitHub yet;
     it's a local account in Cozystack's Keycloak that you will
     personalise in Step 1.
  2. **Image sha tag** like `sha-9ab221a` — the value you will
     paste into the `imageTag` field when deploying your DSO. It
     advances whenever the `classroom` branch gets a new commit,
     so the instructor reads the current value off the running
     cluster and writes it on the whiteboard.
  3. **Your assigned bus:** team A gets bus 4 / `assetId dso-4`;
     team B picks one free bus out of 7 / 8 / 9.

---

## Time budget

Your team has **30–60 minutes** at the dashboard. Plan for:

- **Core (must do, ~30 min):** Steps 1–4 + Experiment A.
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

## Step 1. Claim your Keycloak account *(5 min)*

The instructor has pre-created a team account (`team-a` or
`team-b`) with a temporary password. Your first task is to
claim it — set your own email and a real password.

1. Open <https://dashboard.cozystack-demo.org> in a browser.
   You'll be redirected to a Keycloak login page.
2. Log in with the username the instructor gave you (e.g.
   `team-a`) and the temporary password.
3. Keycloak will redirect you to the dashboard. You should
   see **one namespace** — `tenant-group1` (team A) or
   `tenant-dsos` (team B). If you see "forbidden" or an
   empty namespace list, tell the instructor — the RBAC
   binding didn't land.
4. Top-right corner of the dashboard → your username →
   **Account** (or go directly to
   <https://keycloak.cozystack-demo.org/realms/cozy/account>).
5. On the Account page, fill in **Email** with your real
   email address, **First name** and **Last name** with
   your name, click **Save**.
6. On the **Signing In** tab → **Password** → **Update** →
   set a new password you will remember. **Write it down on
   paper** — if you lose it, the instructor has to reset it
   via a CR and it takes ~5 minutes.

From this point on, use your own password to log in. The
temporary password no longer works.

> **If the "Save" button on the Account page returns an HTTP
> 500 error**, ignore the email/name update and just change
> the password. The rest of the class works fine without a
> real email — it's a local Keycloak account, not a
> mail-backed identity.

---

## Step 2. Fork the starter `model.json` and put it in your repo *(5 min)*

The model is a single JSON file in your **public** GitHub
repository.

1. Open <https://github.com/Begonia-UC3/dso-model> in a new
   tab, click **Fork** (top-right). This creates
   `<your-username>/dso-model` under your own account.
2. In your fork, open `model.json` — this is the file
   students will edit throughout the experiments. It already
   has a working 4-bus template:

```json
{
  "s_base": 100.0,
  "buses": [
    {"id": 1, "type": "slack", "v_setpoint": 1.02, "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 0.0,  "q_load_mvar": 0.0,  "base_kv": 138.0},
    {"id": 2, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 80.0, "q_load_mvar": 15.0, "base_kv": 13.8},
    {"id": 3, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 55.0, "q_load_mvar": 20.0, "base_kv": 13.8},
    {"id": 4, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 30.0, "q_load_mvar": 12.0, "base_kv": 13.8}
  ],
  "branches": [
    {"from": 1, "to": 2, "r_pu": 0.01, "x_pu": 0.08, "b_pu": 0.10, "rate_mva": 150, "is_transformer": true},
    {"from": 2, "to": 3, "r_pu": 0.02, "x_pu": 0.10, "b_pu": 0.15, "rate_mva": 100, "is_transformer": false},
    {"from": 2, "to": 4, "r_pu": 0.02, "x_pu": 0.12, "b_pu": 0.12, "rate_mva": 100, "is_transformer": false}
  ],
  "external_tie": {"bus_id": 1, "asset_id": "dso"}
}
```

Total load: 80 + 55 + 30 = **165 MW** — that's your baseline
draw from the transmission grid. Leave everything as-is for
now; you'll edit the `p_load_mw` values in Experiments A/B.

> **Your `external_tie.asset_id` in the JSON is ignored** —
> the Ieee9Zone CR's `spec.assetId` (e.g. `dso-4`) overrides
> it at runtime. So it doesn't matter whether the JSON says
> `"dso"` or `"dso-4"` — just leave it at the starter value.

Copy the HTTPS URL of your fork (e.g.
`https://github.com/yourname/dso-model.git`) — you will
paste it in the next step.

---

## Step 3. Deploy your `Ieee9Zone` *(5–10 min)*

There are two ways: the dashboard form (nicer) and the CLI
fallback (always works).

### 3.1. Try the dashboard form first

1. In the Cozystack dashboard, select your tenant namespace.
2. **Simulation → IEEE 9-Bus Zone → Create.**
3. Fill the form:

   | Field                    | Value                                          |
   | ------------------------ | ---------------------------------------------- |
   | name                     | `mydso`                                        |
   | mode                     | `dso`                                          |
   | imageTag                 | `sha-xxxxxxx` (*the value the instructor gave you*) |
   | assetId                  | `dso-4` (team A) / `dso-7`, `dso-8` or `dso-9` (team B) |
   | upstreamBusId            | `4` (team A) / `7`, `8` or `9` (team B) — **same number** |
   | gridCentralUrl           | `http://grid-central.tenant-root:8000`         |
   | modelRepoUrl             | the HTTPS URL of your fork (ends in `.git`)    |
   | modelRepoBranch          | `main`                                         |
   | modelPath                | `model.json`                                   |
   | modelSyncIntervalSeconds | `30`                                           |
   | ingressEnabled           | `true`                                         |
   | ingressHost              | `group1-dso.cozystack-demo.org` (team A) or `dsos-dso.cozystack-demo.org` (team B) |
   | ingressClassName         | `tenant-root`                                  |
   | tlsEnabled               | `true`                                         |
   | tlsClusterIssuer         | `letsencrypt-prod`                             |

4. **Click Submit.** If Cozystack accepts, skip to Step 4.

### 3.2. If the Submit button is greyed out or doesn't respond

This is a known Cozystack dashboard glitch — the form
pre-validation sometimes refuses to enable Submit even when
the data is valid. Fallback path: apply the same CR via
`kubectl` on the cluster host.

Ask the instructor to open a cluster shell (or get
kubeconfig), then paste this whole block (replace the 4
highlighted values with **yours**) and press Enter:

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps.cozystack.io/v1alpha1
kind: Ieee9Zone
metadata:
  name: mydso
  namespace: tenant-group1             #  <-- CHANGE to tenant-dsos for team B
spec:
  mode: dso
  replicas: 1
  imageTag: sha-xxxxxxx                #  <-- CHANGE to instructor's sha
  gridCentralUrl: http://grid-central.tenant-root:8000
  tickInterval: "1.0"
  modelRepoUrl: https://github.com/YOUR-GITHUB/dso-model.git   # <-- CHANGE
  modelRepoBranch: main
  modelPath: model.json
  modelSyncIntervalSeconds: 30
  assetId: dso-4                       #  <-- CHANGE for team B (dso-7/8/9)
  upstreamBusId: "4"                   #  <-- CHANGE for team B ("7"/"8"/"9")
  upstreamPollSeconds: 5
  ingressEnabled: true
  ingressHost: group1-dso.cozystack-demo.org   #  <-- CHANGE for team B
  ingressClassName: tenant-root
  tlsEnabled: true
  tlsClusterIssuer: letsencrypt-prod
EOF
```

If the paste renders with extra tab characters or mangled
indentation, save the block to a file via `nano /tmp/dso.yaml`,
then fix indentation so that `apiVersion`, `kind`, `metadata`,
`spec` all start at the **first column** (zero leading spaces),
and their children are indented by 2 spaces, not 4. Then
`kubectl apply -f /tmp/dso.yaml`.

Either path ends with Cozystack provisioning a HelmRelease
named `zone-mydso`, which Flux installs as a Deployment + Service
+ Ingress.

---

## Step 4. Verify the DSO is alive and the grid sees it *(5 min)*

Everything you check here is on the **web dashboards**, no
terminal needed.

**4.1. Your DSO dashboard.** Open
`https://group1-dso.cozystack-demo.org` (team A) or the host
you picked (team B). The page may take 1–2 minutes after
deploy for the Let's Encrypt certificate to issue.

You should see:

- A topology diagram of your 4-bus distribution network.
- Each bus labelled with `V` (voltage, around 0.97–1.02 pu)
  and load.
- A **status panel** with:
  - `converged: True`
  - `iterations:` a small number (usually 3–5)
  - `tie_injection.p_mw:` roughly **160 MW** with the starter
    `model.json` — this is the MW your DSO draws from the
    transmission grid.
  - `upstream_feedback.v_pu:` roughly **0.97** — the voltage
    the transmission grid reports back to your slack bus.

**4.2. Transmission dashboard.** Open
<https://ieee9.cozystack-demo.org>. Find **your bus** on the
IEEE-9 ring diagram:

- Team A → **bus 4**.
- Team B → whichever bus you picked (7 / 8 / 9).

On your bus, the **Load** value should have risen from zero
(before your DSO was deployed) to roughly **162 MW** — very
close to the `tie_injection.p_mw` you see on your own DSO.
That match confirms the two sides are talking.

**If you see `tie_injection` on your DSO but the transmission
dashboard shows zero load on your bus:** your `assetId` in
the CR does not match an entry that `grid-central` knows
about. Double-check the `assetId` value (`dso-4` / `dso-7` /
`dso-8` / `dso-9`). See troubleshooting.

---

## Step 5. Experiment A — live model reload *(core, ~10 min)*

**Goal.** Prove that the GitOps model-reload loop works. No
pod restart, no redeploy — a `git push` and the whole chain
picks up the change within a minute.

**Before you change anything, open two browser windows
side-by-side and write down the baseline numbers:**

- **Window 1 — your DSO dashboard** (`https://group1-dso…` or
  `dsos-dso…`). On the status panel, find and write down:
  - `tie_injection.p_mw` ≈ **160 MW**
- **Window 2 — transmission dashboard**
  (<https://ieee9.cozystack-demo.org>). Find **your bus** on
  the ring diagram and write down:
  - `Load` ≈ **162 MW**

**Now change exactly one line in `model.json`.** On GitHub,
in your fork, click on `model.json` → pencil icon (edit).
Replace the `p_load_mw` value on bus 2 from `80.0` to
`120.0`. Concretely, the **old** line is:

```json
    {"id": 2, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 80.0, "q_load_mvar": 15.0, "base_kv": 13.8},
```

Replace it with:

```json
    {"id": 2, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 120.0, "q_load_mvar": 30.0, "base_kv": 13.8},
```

Scroll down → **Commit changes** → leave the default commit
message → **Commit changes** again.

**Wait 30–60 seconds**, then refresh both windows. The same
two numbers you wrote down should move to:

- DSO dashboard: `tie_injection.p_mw` ≈ **200 MW** (was 160).
- Transmission dashboard: your bus `Load` ≈ **205 MW** (was
  162).

Nothing restarted. That is the whole teaching point —
screenshot both windows before and after.

> **Voltage numbers barely move with a small load bump.** The
> transmission grid has a 500 MW diesel on bus 2 that easily
> absorbs a 40 MW extra load — so bus voltages move by only
> 0.003–0.005 pu, which you will not see on the voltage
> graphs. The `Load` number on your bus is the primary
> visual signal for Experiment A. If you want to see voltages
> move visibly, continue to Experiment B.

---

## Step 6. Experiment B — closed-loop voltage feedback *(stretch, ~15 min)*

> Skip if your team is already at the 30-minute mark; the
> core deliverables (Steps 1–4 + Experiment A) are enough for
> a pass.

**Goal.** Show that your DSO doesn't just push data up — it
also *listens* for the voltage at its tie bus in IEEE-9 and
uses that as the slack setpoint inside its own network
(closed-loop V feedback). Experiment A's small bump didn't
move voltages enough to see this; you need to push harder.

**Open three windows side-by-side, write down baselines:**

1. **Your DSO dashboard.**
   - `upstream_feedback.v_pu` ≈ **0.97**
   - Internal bus voltages (bus 2, 3, 4 inside your DSO) all
     near **1.00 pu**
2. **Transmission dashboard.** Voltage on your tie bus
   (4 / 7 / 8 / 9) ≈ **1.00 pu**
3. Your GitHub repo editor window.

**Replace the entire `model.json`** in your fork with this
exact content (total load now 310 MW):

```json
{
  "s_base": 100.0,
  "buses": [
    {"id": 1, "type": "slack", "v_setpoint": 1.02, "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 0.0,   "q_load_mvar": 0.0,  "base_kv": 138.0},
    {"id": 2, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 150.0, "q_load_mvar": 40.0, "base_kv": 13.8},
    {"id": 3, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 100.0, "q_load_mvar": 35.0, "base_kv": 13.8},
    {"id": 4, "type": "pq",    "v_setpoint": 1.0,  "p_gen_mw": 0.0, "q_gen_mvar": 0.0, "p_load_mw": 60.0,  "q_load_mvar": 20.0, "base_kv": 13.8}
  ],
  "branches": [
    {"from": 1, "to": 2, "r_pu": 0.01, "x_pu": 0.08, "b_pu": 0.10, "rate_mva": 150, "is_transformer": true},
    {"from": 2, "to": 3, "r_pu": 0.02, "x_pu": 0.10, "b_pu": 0.15, "rate_mva": 100, "is_transformer": false},
    {"from": 2, "to": 4, "r_pu": 0.02, "x_pu": 0.12, "b_pu": 0.12, "rate_mva": 100, "is_transformer": false}
  ],
  "external_tie": {"bus_id": 1, "asset_id": "dso"}
}
```

Commit. **Wait ~45 seconds**, then look at all three windows:

- **Transmission dashboard.** Voltage on your tie bus sags
  **from ~1.00 to ~0.90 pu** — clearly visible on the
  voltage graph, not just a number.
- **DSO dashboard — `upstream_feedback.v_pu`.** Drops
  **from ~0.97 to ~0.90** — this is the new setpoint your
  DSO just received from the transmission grid.
- **DSO dashboard — internal bus voltages.** All sag
  proportionally **from ~1.00 to 0.88–0.92 pu**. The
  transmission grid effectively said *"you're stressing me;
  here is a lower reference"*, and your internal NR
  recalculated with the new reference.

Three simultaneous voltage drops in three different places
— that is the full teaching signal. Screenshot all three
before and after.

---

## Step 7. Experiment C — the stability edge *(joint session, ~15 min)*

> This experiment is run jointly with the other team at the
> end of the class — both teams need an active `Ieee9Zone`
> with heavy load at the same time. The instructor will call
> everyone together.

**Goal.** Show that one team's heavy load alone cannot break
the IEEE-9 grid (the 500 MW diesel on bus 2 easily absorbs
a single DSO at 310 MW), but **two teams together can** —
and Newton–Raphson then gives up with `converged: false`.

**Procedure:**

1. Both teams keep their Experiment-B model applied (310 MW
   per team). If you already pushed that model, no change
   needed. If you rolled back, paste the same JSON again
   and commit.
2. Once both teams confirm `tie_injection.p_mw ≈ 300 MW`
   each on their own DSO dashboards, switch attention to
   the **transmission dashboard**
   (<https://ieee9.cozystack-demo.org>).
3. Over ~30–60 seconds, watch the status panel at the top
   of the page:
   - Voltages on buses 4 and 7 / 8 / 9 drop further, heading
     toward 0.85 pu, then 0.80.
   - **The `converged` status flips from `true` to `false`**
     and **`iterations` hits 20** (the solver hit its cap
     and gave up).

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
