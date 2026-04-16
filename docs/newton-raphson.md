# Newton–Raphson power-flow — what `grid-central` and `dso` actually solve

Both `grid-central` (IEEE-9 transmission ring) and `dso` (per-operator
distribution feeder) iterate the same algorithm at different scales.
This file documents the math, then ties each step back to the line of
Python that implements it.

## Problem setup

A power-flow problem takes:

- **Network topology** as a set of buses (nodes) and branches (edges
  with impedance). Encoded as `BUS_DATA` / `BRANCH_DATA` in
  `grid-central/main.py`, or the JSON model's `buses[]` / `branches[]`
  for a DSO.
- **Bus types**: `slack` (reference — fixes V, θ), `PV` (generator —
  fixes V, P, solves for Q and θ), `PQ` (load/junction — fixes P, Q,
  solves for V and θ).
- **Injections**: P_gen, Q_gen, P_load, Q_load per bus. Injected
  power `S_inj_i = (P_gen_i − P_load_i) + j(Q_gen_i − Q_load_i)`.

The solution is the bus voltage **V_i** (magnitude) and **θ_i**
(angle) such that, at every bus, the injected power matches the power
flowing out on incident branches. That "match" is the power-balance
equation:

```
P_i(V, θ) = Σ_k V_i · V_k · (G_ik·cos(θ_i − θ_k) + B_ik·sin(θ_i − θ_k))
Q_i(V, θ) = Σ_k V_i · V_k · (G_ik·sin(θ_i − θ_k) − B_ik·cos(θ_i − θ_k))
```

where **Y = G + jB** is the bus admittance matrix (aka the "Y-bus"),
built from branch impedances with Kirchhoff's rules. The equations
are nonlinear in V, θ (products of voltages and trig of angles), so
an iterative solver is needed.

## Y-bus construction

Done once per solve in `DSOEngine.build_Y()` (`dso/main.py:141`) and
`PowerFlowEngine._build_Y()` (`grid-central/main.py`, similar
structure). For each branch `(i, j, r, x, b, rate, is_transformer)`:

- Series admittance `y_series = 1 / (r + j·x)`. Special-case: if `r`
  is zero and `x` is nonzero (pure reactance, usually a transformer),
  `y_series = 1 / (j·x)` to avoid dividing a complex zero.
- Shunt admittance `y_shunt = j·b/2` at each end of the branch.
- Off-diagonal `Y[i, j] -= y_series`, `Y[j, i] -= y_series`.
- Diagonal `Y[i, i] += y_series + y_shunt`, same at `j`.

The resulting **Y is sparse but complex-valued** — Python uses
`numpy.complex128` throughout.

## Newton–Raphson iteration

Goal: zero the mismatch vector `[ΔP; ΔQ]`, where

```
ΔP_i = P_gen_i − P_load_i − P_i(V, θ)        for i in non-slack buses
ΔQ_i = Q_gen_i − Q_load_i − Q_i(V, θ)        for i in PQ buses only
```

The PV-bus Q mismatch is skipped because we're solving *for* Q there
(generator absorbs whatever reactive is needed to hold V_setpoint).
The slack bus is skipped entirely — both V and θ are fixed.

Each iteration:

1. **Compute current P, Q** at each bus from the power-balance
   equations above (with current estimates of V, θ).
2. **Assemble the mismatch vector** `mismatch = [ΔP_non_slack; ΔQ_pq]`.
3. **Check convergence**: `max(|mismatch|) < tol`. If so, done.
4. **Build the Jacobian** — partial derivatives of (P, Q) with respect
   to (θ, V/V). The four blocks J1/J2/J3/J4 capture ∂P/∂θ, ∂P/∂(V/V),
   ∂Q/∂θ, ∂Q/∂(V/V). All entries derived analytically from the
   balance equations. See `grid-central/main.py:191-241`.
5. **Solve** `J · dx = mismatch` with `numpy.linalg.solve`. If J is
   singular (happens near voltage collapse) we abort the loop with
   `converged = False`.
6. **Apply updates**: `θ_i += dx[...]`, `V_i += dx[...] * V_i` (the
   `V_i` factor is the V/V → V transformation — standard "dV/V"
   formulation).
7. **Clamp V to [0.80, 1.15]** to keep NR from wandering into
   physically nonsensical territory while still letting it find
   genuinely-depressed solutions under heavy load. This bound was
   widened from 0.85 in `1a93d28` because 3 concurrent DSOs naturally
   drive some buses below 0.85 pu.
8. **Repeat** up to `MAX_ITER = 20`.

Typical convergence: **3–7 iterations** for the IEEE-9 base case plus
a handful of DSO overrides. More than 10 iterations is a warning sign
— either the network is near its stability limit, or an override is
unrealistic.

## Post-solve: slack back-compute

PV and PQ buses know their P or Q from the problem setup, but the
slack bus's injection is whatever the rest of the network needs —
it has to be computed *after* NR converges (or gives up). Code:

```python
V_c = np.array([V[k] * complex(math.cos(theta[k]), math.sin(theta[k]))
                for k in range(self.n)])
for i, btype in enumerate(self.bus_types):
    if btype == "slack":
        S_inj_pu = V_c[i] * np.conj(self.Y[i, :] @ V_c)
        self.P_gen[i] = S_inj_pu.real + self.P_load[i]
        self.Q_gen[i] = S_inj_pu.imag + self.Q_load[i]
```

Runs on **both converged and non-converged exits** — on an overloaded
grid the operator needs to see the ballpark imbalance, not a
misleading zero.

## Base-case parameters (IEEE-9 in `grid-central`)

- `S_BASE = 100.0` MVA — per-unit base.
- `MAX_ITER = 20`, `tol = 1e-6` — typical NR defaults.
- V clamp `[0.80, 1.15]` pu.
- 9 buses, 9 branches (6 lines + 3 transformers). See
  `docs/topology.md` for the exact network.

## Nested NR — the DSO case

A DSO runs **its own NR** over a much smaller, operator-supplied
network (4 buses in the reference feeder). Its input JSON has
`buses`, `branches`, and crucially `external_tie`:

```json
{
  "buses":  [{"id": 1, "type": "slack", "v_setpoint": 1.02}, ...],
  "branches": [...],
  "external_tie": {"bus_id": 1, "asset_id": "dso-7"}
}
```

The `external_tie.bus_id` names the DSO-internal bus that acts as
its *slack* — this bus's net injection is the DSO's **tie** with
the outer transmission grid.

After each internal solve (`dso/main.py:sim_loop`):

1. Compute tie P, Q from the slack-bus back-compute (same as
   grid-central).
2. POST this as an asset update to `grid-central/api/asset-update`
   with `asset_id = external_tie.asset_id`.
3. Asynchronously poll `grid-central/api/grid-state`, read the V at
   `upstreamBusId`, use it as the DSO-internal slack's V_setpoint
   for the *next* tick. This is the **closed-loop upstream V
   feedback** — grid-central's line loading affects the voltage at
   the DSO tie, which the DSO then sees reflected in its own solve.

Each tick (`TICK_INTERVAL = 1.0s` by default) a new NR runs on both
sides. Convergence is independent — a non-converging transmission
grid doesn't stop DSOs from solving their own, just means they're
posting to an upstream that's in trouble.

## Failure modes and what they look like

| Symptom | Likely cause | Where to look |
| --- | --- | --- |
| `converged: false iter: 20` | Overload past voltage stability limit | Load totals vs generator capacity; bus voltages near 0.80 pu |
| `converged: false reason: singular Jacobian` | Network pathology (islanding, gen at 0 MW) | Recent `ASSET_BUS_MAP` / override edits |
| All PQ buses clamped at 0.80 pu exactly | V clamp binding — solver gave up | Same as overload — more local gen or relieve load |
| Slack `p_gen=0 status=offline` | Back-compute skipped (pre-`b5deae3` bug) | Already fixed; shouldn't recur |
| Oscillating V at 0.85 ↔ ~0.95 between ticks | AUTO_MODE perturbations crossing the stability boundary | Tighten AUTO_MODE amplitude or bump generator capacity |

## Further reading

- `grid-central/main.py:140-290` — IEEE-9 NR loop, Jacobian,
  back-compute.
- `dso/main.py:141-265` — DSO internal NR. Same structure, smaller
  network.
- `docs/topology.md` — IEEE-9 bus/branch parameters this solver
  operates on.
- Anderson, P. M., & Fouad, A. A. (1994). *Power System Control and
  Stability*. Source for the original IEEE-9 benchmark; our numbers
  follow that convention except for the diesel uprate (bus 2: 163 →
  500 MW for multi-DSO headroom).
