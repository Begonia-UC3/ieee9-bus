"""
IEEE 9-Bus Central Power Flow Engine
=====================================
Receives generation/load updates from asset simulators (each in its own K8s namespace),
runs Newton-Raphson AC power flow, and broadcasts results via WebSocket.
"""

import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# ══════════════════════════════════════════════════════════════════════
#  IEEE 9-Bus System Data  (P.M. Anderson / WSCC)
# ══════════════════════════════════════════════════════════════════════

BUS_DATA = {
    #  id: (type, V_setpoint, P_gen_MW, Q_gen_MVAR, P_load_MW, Q_load_MVAR, base_kV)
    1: ("slack", 1.040, 0.0,   0.0,  0.0,   0.0,  16.5),
    2: ("pv",    1.025, 163.0, 0.0,  0.0,   0.0,  18.0),
    3: ("pv",    1.025, 85.0,  0.0,  0.0,   0.0,  13.8),
    4: ("pq",    1.0,   0.0,   0.0,  0.0,   0.0,  230.0),
    5: ("pq",    1.0,   0.0,   0.0,  125.0, 50.0,  230.0),
    6: ("pq",    1.0,   0.0,   0.0,  90.0,  30.0,  230.0),
    7: ("pq",    1.0,   0.0,   0.0,  0.0,   0.0,  230.0),
    8: ("pq",    1.0,   0.0,   0.0,  100.0, 35.0,  230.0),
    9: ("pq",    1.0,   0.0,   0.0,  0.0,   0.0,  230.0),
}

BRANCH_DATA = [
    # (from, to, r_pu, x_pu, b_pu, rate_MVA, is_transformer)
    (1, 4, 0.0,    0.0576, 0.0,   250, True),
    (2, 7, 0.0,    0.0625, 0.0,   250, True),
    (3, 9, 0.0,    0.0586, 0.0,   250, True),
    (4, 5, 0.0100, 0.0850, 0.176, 250, False),
    (4, 6, 0.0170, 0.0920, 0.158, 150, False),
    (5, 7, 0.0320, 0.1610, 0.306, 250, False),
    (6, 9, 0.0390, 0.1700, 0.358, 150, False),
    (7, 8, 0.0085, 0.0720, 0.149, 250, False),
    (8, 9, 0.0119, 0.1008, 0.209, 250, False),
]

# Map asset simulators to buses
ASSET_BUS_MAP = {
    "diesel-gen":  2,   # Generator on Bus 2
    "battery":     3,   # Generator/load on Bus 3
    "datacenter":  5,   # Load on Bus 5
    "dso":         6,   # DSO aggregator on Bus 6 (replaces the anonymous 90 MW load)
}

S_BASE = 100.0  # MVA base


# ══════════════════════════════════════════════════════════════════════
#  Newton-Raphson Power Flow Solver
# ══════════════════════════════════════════════════════════════════════

class PowerFlowEngine:
    def __init__(self):
        self.n = 9
        self.Y = np.zeros((self.n, self.n), dtype=complex)
        self._build_ybus()

        # Working copies of bus injections (pu)
        self.P_gen = np.zeros(self.n)
        self.Q_gen = np.zeros(self.n)
        self.P_load = np.zeros(self.n)
        self.Q_load = np.zeros(self.n)
        self.V = np.ones(self.n)
        self.theta = np.zeros(self.n)
        self.bus_types = []

        for i in range(1, self.n + 1):
            btype, v_sp, pg, qg, pl, ql, _ = BUS_DATA[i]
            self.bus_types.append(btype)
            self.V[i - 1] = v_sp
            self.P_gen[i - 1] = pg / S_BASE
            self.Q_gen[i - 1] = qg / S_BASE
            self.P_load[i - 1] = pl / S_BASE
            self.Q_load[i - 1] = ql / S_BASE

        # Asset overrides (updated by simulators)
        self.asset_overrides: Dict[str, dict] = {}
        self.last_results: Optional[dict] = None

    def _build_ybus(self):
        """Build admittance matrix from branch data."""
        self.Y[:] = 0
        for fr, to, r, x, b, rate, is_xfmr in BRANCH_DATA:
            i, j = fr - 1, to - 1
            if abs(r) + abs(x) < 1e-12:
                y_series = complex(0, -1 / x) if abs(x) > 1e-12 else complex(0, 0)
            else:
                y_series = 1 / complex(r, x)
            y_shunt = complex(0, b / 2)
            self.Y[i, i] += y_series + y_shunt
            self.Y[j, j] += y_series + y_shunt
            self.Y[i, j] -= y_series
            self.Y[j, i] -= y_series

    def apply_asset_overrides(self):
        """Apply latest data from asset simulators to bus injections."""
        # Reset to base values first
        for i in range(1, self.n + 1):
            _, _, pg, _, pl, ql, _ = BUS_DATA[i]
            self.P_gen[i - 1] = pg / S_BASE
            self.P_load[i - 1] = pl / S_BASE
            self.Q_load[i - 1] = ql / S_BASE

        for asset_id, data in self.asset_overrides.items():
            bus_idx = ASSET_BUS_MAP.get(asset_id)
            if bus_idx is None:
                continue
            i = bus_idx - 1
            if "p_gen_mw" in data:
                self.P_gen[i] = data["p_gen_mw"] / S_BASE
            if "p_load_mw" in data:
                self.P_load[i] = data["p_load_mw"] / S_BASE
            if "q_load_mvar" in data:
                self.Q_load[i] = data["q_load_mvar"] / S_BASE

    def solve(self, max_iter=20, tol=1e-6) -> dict:
        """Run Newton-Raphson power flow."""
        self.apply_asset_overrides()

        P_spec = self.P_gen - self.P_load
        Q_spec = self.Q_gen - self.Q_load

        V = self.V.copy()
        theta = self.theta.copy()

        pq_buses = [i for i in range(self.n) if self.bus_types[i] == "pq"]
        pv_buses = [i for i in range(self.n) if self.bus_types[i] == "pv"]
        non_slack = pq_buses + pv_buses
        non_slack.sort()

        converged = False
        iterations = 0

        for it in range(max_iter):
            iterations = it + 1
            # Calc injections
            P_calc = np.zeros(self.n)
            Q_calc = np.zeros(self.n)
            for i in range(self.n):
                for j in range(self.n):
                    G = self.Y[i, j].real
                    B = self.Y[i, j].imag
                    angle_diff = theta[i] - theta[j]
                    P_calc[i] += V[i] * V[j] * (G * math.cos(angle_diff) + B * math.sin(angle_diff))
                    Q_calc[i] += V[i] * V[j] * (G * math.sin(angle_diff) - B * math.cos(angle_diff))

            # Mismatches
            dP = P_spec - P_calc
            dQ = Q_spec - Q_calc

            # Build mismatch vector
            mismatch = []
            idx_map = []
            for i in non_slack:
                mismatch.append(dP[i])
                idx_map.append(("P", i))
            for i in pq_buses:
                mismatch.append(dQ[i])
                idx_map.append(("Q", i))

            mismatch = np.array(mismatch)
            if np.max(np.abs(mismatch)) < tol:
                converged = True
                break

            # Build Jacobian
            n_vars = len(mismatch)
            J = np.zeros((n_vars, n_vars))

            n_p = len(non_slack)
            n_q = len(pq_buses)

            # J1: dP/dTheta, J2: dP/dV, J3: dQ/dTheta, J4: dQ/dV
            for ii, i in enumerate(non_slack):
                for jj, j in enumerate(non_slack):
                    G = self.Y[i, j].real
                    B = self.Y[i, j].imag
                    angle_diff = theta[i] - theta[j]
                    if i == j:
                        J[ii, jj] = -Q_calc[i] - B[0] * V[i] ** 2 if isinstance(B, np.ndarray) else -Q_calc[i] - self.Y[i, i].imag * V[i] ** 2
                    else:
                        J[ii, jj] = V[i] * V[j] * (G * math.sin(angle_diff) - B * math.cos(angle_diff))

            for ii, i in enumerate(non_slack):
                for jj, j in enumerate(pq_buses):
                    col = n_p + jj
                    G = self.Y[i, j].real
                    B = self.Y[i, j].imag
                    angle_diff = theta[i] - theta[j]
                    if i == j:
                        J[ii, col] = P_calc[i] / V[i] + self.Y[i, i].real * V[i]
                    else:
                        J[ii, col] = V[i] * (G * math.cos(angle_diff) + B * math.sin(angle_diff))

            for ii, i in enumerate(pq_buses):
                row = n_p + ii
                for jj, j in enumerate(non_slack):
                    G = self.Y[i, j].real
                    B = self.Y[i, j].imag
                    angle_diff = theta[i] - theta[j]
                    if i == j:
                        J[row, jj] = P_calc[i] - self.Y[i, i].real * V[i] ** 2
                    else:
                        J[row, jj] = -V[i] * V[j] * (G * math.cos(angle_diff) + B * math.sin(angle_diff))

            for ii, i in enumerate(pq_buses):
                row = n_p + ii
                for jj, j in enumerate(pq_buses):
                    col = n_p + jj
                    G = self.Y[i, j].real
                    B = self.Y[i, j].imag
                    angle_diff = theta[i] - theta[j]
                    if i == j:
                        J[row, col] = Q_calc[i] / V[i] - self.Y[i, i].imag * V[i]
                    else:
                        J[row, col] = V[i] * (G * math.sin(angle_diff) - B * math.cos(angle_diff))

            try:
                dx = np.linalg.solve(J, mismatch)
            except np.linalg.LinAlgError:
                break

            # Update
            for ii, i in enumerate(non_slack):
                theta[i] += dx[ii]
            for ii, i in enumerate(pq_buses):
                V[i] += dx[n_p + ii] * V[i]  # dV/V correction
                V[i] = max(0.85, min(1.15, V[i]))

        self.V = V
        self.theta = theta

        # Compute final line flows
        branch_results = []
        for fr, to, r, x, b_shunt, rate, is_xfmr in BRANCH_DATA:
            i, j = fr - 1, to - 1
            if abs(r) + abs(x) < 1e-12:
                y = complex(0, -1 / x)
            else:
                y = 1 / complex(r, x)

            Vi = V[i] * (math.cos(theta[i]) + 1j * math.sin(theta[i]))
            Vj = V[j] * (math.cos(theta[j]) + 1j * math.sin(theta[j]))
            I_ij = (Vi - Vj) * y + Vi * complex(0, b_shunt / 2)
            S_ij = Vi * I_ij.conjugate() * S_BASE

            p_flow = S_ij.real
            q_flow = S_ij.imag
            loading = abs(S_ij.real) / rate * 100 if rate > 0 else 0

            if loading > 90:
                status = "congested"
            elif loading > 70:
                status = "warning"
            elif abs(p_flow) < 3:
                status = "idle"
            else:
                status = "nominal"

            branch_results.append({
                "from": fr, "to": to,
                "p_flow_mw": round(p_flow, 2),
                "q_flow_mvar": round(q_flow, 2),
                "loading_pct": round(loading, 1),
                "rate_mva": rate,
                "type": "transformer" if is_xfmr else "line",
                "status": status,
            })

        bus_results = []
        for i in range(self.n):
            bus_id = i + 1
            btype = self.bus_types[i]
            p_gen = self.P_gen[i] * S_BASE
            p_load = self.P_load[i] * S_BASE
            v = V[i]

            if btype == "slack" or btype == "pv":
                if p_gen < 1:
                    status = "offline"
                else:
                    status = "nominal"
            else:
                if v < 0.95:
                    status = "undervoltage"
                elif v > 1.06:
                    status = "overvoltage"
                elif p_load < 1 and p_gen < 1:
                    status = "idle"
                else:
                    status = "nominal"

            bus_results.append({
                "id": bus_id,
                "type": btype,
                "v_pu": round(v, 4),
                "angle_deg": round(math.degrees(theta[i]), 2),
                "p_gen_mw": round(p_gen, 2),
                "p_load_mw": round(p_load, 2),
                "q_load_mvar": round(self.Q_load[i] * S_BASE, 2),
                "base_kv": BUS_DATA[bus_id][6],
                "status": status,
            })

        self.last_results = {
            "timestamp": time.time(),
            "converged": converged,
            "iterations": iterations,
            "buses": bus_results,
            "branches": branch_results,
            "asset_overrides": {k: v for k, v in self.asset_overrides.items()},
        }
        return self.last_results


# ══════════════════════════════════════════════════════════════════════
#  FastAPI Application
# ══════════════════════════════════════════════════════════════════════

engine = PowerFlowEngine()
ws_clients: list[WebSocket] = []
sim_tick = 0


async def broadcast_loop():
    """Periodically run power flow and broadcast to all WS clients."""
    global sim_tick
    while True:
        result = engine.solve()
        sim_tick += 1
        result["tick"] = sim_tick
        msg = json.dumps(result)
        dead = []
        for ws in ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            ws_clients.remove(ws)
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broadcast_loop())
    yield
    task.cancel()


app = FastAPI(title="IEEE 9-Bus Grid Central Engine", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AssetUpdate(BaseModel):
    asset_id: str          # "diesel-gen", "battery", "datacenter"
    p_gen_mw: float = 0.0
    q_gen_mvar: float = 0.0
    p_load_mw: float = 0.0
    q_load_mvar: float = 0.0
    metadata: dict = {}


@app.post("/api/asset-update")
async def receive_asset_update(update: AssetUpdate):
    """Receive telemetry from an asset simulator and apply to grid model."""
    engine.asset_overrides[update.asset_id] = {
        "p_gen_mw": update.p_gen_mw,
        "q_gen_mvar": update.q_gen_mvar,
        "p_load_mw": update.p_load_mw,
        "q_load_mvar": update.q_load_mvar,
        "metadata": update.metadata,
        "last_update": time.time(),
    }
    return {"status": "ok", "bus": ASSET_BUS_MAP.get(update.asset_id)}


@app.get("/api/grid-state")
async def get_grid_state():
    """Return latest power flow results (polling fallback)."""
    if engine.last_results:
        return engine.last_results
    return engine.solve()


@app.get("/api/topology")
async def get_topology():
    """Return static grid topology for the frontend diagram."""
    return {
        "buses": [
            {"id": i, "type": d[0], "base_kv": d[6],
             "x": [250, 60, 440, 250, 90, 410, 60, 250, 440][i - 1],
             "y": [60, 440, 440, 160, 240, 240, 340, 340, 340][i - 1],
             "asset": next((k for k, v in ASSET_BUS_MAP.items() if v == i), None)}
            for i, d in BUS_DATA.items()
        ],
        "branches": [
            {"from": fr, "to": to, "type": "transformer" if xfmr else "line", "rate_mva": rate}
            for fr, to, r, x, b, rate, xfmr in BRANCH_DATA
        ],
    }


@app.websocket("/ws/grid")
async def websocket_grid(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    # Send current state immediately
    if engine.last_results:
        await ws.send_text(json.dumps(engine.last_results))
    try:
        while True:
            await ws.receive_text()  # Keep alive
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)


@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")
