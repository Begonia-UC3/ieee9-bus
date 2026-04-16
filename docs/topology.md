# IEEE 9-bus topology as used in this repo

The canonical "Anderson-Fouad 9-bus" benchmark — three generators,
three loads, three pure-transmission buses, connected by 6 lines and
3 generator step-up transformers. This file is a field-by-field
reference for what `grid-central/main.py` actually models and how
simulators plug in.

## The 9 buses

```
           ┌──────── Bus 1 (slack, 16.5 kV) ────────┐
           │                                         │
   Gen 1  [~] ← picks up the balance                 │
           │                                         │
       (T1: 1→4, 0.0576 pu X, 250 MVA)               │
           │                                         │
        Bus 4 ─── Line 4-5 ─── Bus 5 ─── Line 5-7 ─── Bus 7 ─┐
         230 kV      230 kV                230 kV            │
           │           │                      │              │
        Line 4-6       │                      │            Bus 2 ─── Gen 2 (diesel)
           │        (load 125+50)             │            (T2: 2→7)
           │                                  │
          Bus 6 ─── Line 6-9 ─── Bus 9 ─── Line 8-9 ─── Bus 8
         230 kV      230 kV                230 kV
         primary                (load 100+35)
         dso tie              click-deploy
                                dso-8 slot
                      │
                     Bus 3 ─── Gen 3 (battery)
                     (T3: 3→9)
```

(ASCII layout is indicative — actual branch list below.)

### Bus table (from `grid-central/main.py:28-38`)

| Bus | Type  | V_setpoint | Base kV | Base-case P_load / Q_load | Base-case P_gen / Q_gen | Role |
| --- | ----- | ---------- | ------- | --------------------------| ------------------------| --- |
| 1   | slack | 1.040 pu   |  16.5   | 0                         | balancing (post-NR)     | reference; picks up whatever imbalance |
| 2   | PV    | 1.025 pu   |  18.0   | 0                         | 163 MW *(rated 500)*    | `diesel-gen` asset. Uprated from 163 → 500 MW rated for multi-DSO headroom; P_gen walks 325–450 MW in AUTO mode |
| 3   | PV    | 1.025 pu   |  13.8   | 0                         | 85 MW                   | `battery` BESS — bidirectional (AUTO ±60%) |
| 4   | PQ    | 1.000 pu   | 230     | 0 / 0                     | 0                       | pure transmission — available as `dso-4` slot |
| 5   | PQ    | 1.000 pu   | 230     | 125 / 50                  | 0                       | `datacenter` — IT load |
| 6   | PQ    | 1.000 pu   | 230     | 90 / 30 *(replaced)*      | 0                       | **primary `dso` tie** (`tenant-root/dso`) — replaces the 90 MW anonymous load |
| 7   | PQ    | 1.000 pu   | 230     | 0 / 0                     | 0                       | pure transmission — `dso-7` slot (`tenant-dsos/dso-bus7`) |
| 8   | PQ    | 1.000 pu   | 230     | 100 / 35                  | 0                       | `dso-8` slot (`tenant-dsos/dso-bus8`) — was a 100 MW anonymous load |
| 9   | PQ    | 1.000 pu   | 230     | 0 / 0                     | 0                       | pure transmission — `dso-9` slot |

All PQ buses at 230 kV on the high-voltage transmission ring; the
three generators step up through transformers into the ring.

### Generation headroom picture

```
Generator | Rated MW | AUTO_MODE walk | Role
----------|----------|----------------|-----
Bus 1     | ∞ (slack)|  —             | Pinned V=1.04; absorbs any imbalance
Bus 2     | 500      |  325 – 450     | Primary dispatchable gen
Bus 3     | 85       |  −51 – +51     | BESS — charges or discharges
```

Total dispatchable: ~500 MW committed + 85 MW of ±battery + slack
reserve. Enough for 3 concurrent 170 MW DSOs (phase 7 validation).
Beyond 3 DSOs, bumping either the diesel rating or adding a second
PV generator is the natural next step.

## The 9 branches

From `grid-central/main.py:40-51`. All impedances in per-unit on
`S_BASE = 100 MVA`.

| # | From | To | r_pu  | x_pu   | b_pu  | Rated MVA | Kind        | Notes |
| - | ---- | -- | ----- | ------ | ----- | --------- | ----------- | ----- |
| 1 | 1    | 4  | 0     | 0.0576 | 0     | 250       | transformer | Gen 1 step-up |
| 2 | 2    | 7  | 0     | 0.0625 | 0     | 250       | transformer | Gen 2 (diesel) step-up |
| 3 | 3    | 9  | 0     | 0.0586 | 0     | 250       | transformer | Gen 3 (battery) step-up |
| 4 | 4    | 5  | 0.010 | 0.085  | 0.176 | 250       | line        | Connects gen 1 side to datacenter |
| 5 | 4    | 6  | 0.017 | 0.092  | 0.158 | **150**   | line        | Connects gen 1 side to primary DSO tie. Lowest-rated line — often the first to congest |
| 6 | 5    | 7  | 0.032 | 0.161  | 0.306 | 250       | line        | Longest electrical distance on the grid |
| 7 | 6    | 9  | 0.039 | 0.170  | 0.358 | **150**   | line        | Primary DSO ring connection. Second lowest-rated |
| 8 | 7    | 8  | 0.009 | 0.072  | 0.149 | 250       | line        | `dso-bus7` to `dso-bus8` path |
| 9 | 8    | 9  | 0.012 | 0.101  | 0.209 | 250       | line        | Closes the ring |

Two lines are rated 150 MVA (4-6 and 6-9) — they sit in the primary
DSO's electrical neighborhood and are the most likely to hit their
thermal rating first under heavy DSO load. If you see `status:
congested` on the dashboard, it's probably one of these.

## How assets attach

Each asset simulator POSTs to `grid-central/api/asset-update` every
tick with its own `p_gen_mw`, `q_gen_mw`, `p_load_mw`, `q_load_mvar`.
The service looks up the `asset_id` in `ASSET_BUS_MAP` to decide
which bus's injection to overwrite before the next NR solve:

```python
ASSET_BUS_MAP = {
    "diesel-gen":  2,
    "battery":     3,
    "datacenter":  5,
    "dso":         6,    # primary
    "dso-4":       4,    # click-deploy slot
    "dso-7":       7,    # click-deploy slot
    "dso-8":       8,    # click-deploy slot
    "dso-9":       9,    # click-deploy slot
}
```

Asset sends `asset_id` that isn't in the map → POST returns
`{"status":"ok","bus":null}` and the data is silently dropped. No
override applied, bus keeps its base-case numbers.

**No TTL.** If an asset stops posting, its last override persists in
grid-central's memory until the pod restarts. Dead asset looks like a
live one holding a steady setpoint. Documented in CLAUDE.md
behavioural gotchas.

## How DSO click-deploy extends the topology

From the operator's point of view, a DSO doesn't *add* a new bus to
IEEE-9 — it replaces whatever injection was at the outer bus with
its own tie_injection. E.g. a DSO on bus 8:

- Base case: bus 8 has 100 MW / 35 MVAR anonymous load.
- With `Ieee9Zone/dso-bus8`: every tick, the DSO's aggregated feeder
  flow (say ~170 MW / 40 MVAR) overrides that. Bus 8 is now "whatever
  the DSO says".

Buses 4, 7, 9 had **zero base-case load** — they were pure
transmission. Adding `dso-4`, `dso-7`, or `dso-9` turns them into
effective load buses, whatever the attached DSO's tie pulls.

Five slot DSOs max (buses 4, 6, 7, 8, 9). Bus 5 stays with datacenter;
buses 1, 2, 3 are reserved for the three generators. The dropdown in
the Cozystack dashboard (`values.schema.json:upstreamBusId.enum`)
enforces this.

## Further reading

- `docs/newton-raphson.md` — the solver that consumes this topology.
- `docs/multitenant.md` — how the topology splits across Cozystack
  tenants (DSOs in a separate namespace from the central plant).
- `grid-central/main.py:28-51` — the authoritative BUS_DATA and
  BRANCH_DATA arrays.
