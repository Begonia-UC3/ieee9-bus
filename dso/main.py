"""
DSO (Distribution System Operator) Simulator
============================================
Runs an internal Newton-Raphson power flow over a user-supplied grid
model (JSON), then reports the net flow at its external tie as a
single asset to an upstream grid-central.

Model is loaded from MODEL_PATH and hot-reloaded when the file's
mtime changes (populated by a git-sync sidecar from the operator's
own Git repo).

Model JSON shape:

    {
      "s_base": 100.0,
      "buses": [
        {"id": 1, "type": "slack", "v_setpoint": 1.04,
         "p_gen_mw": 0, "q_gen_mvar": 0,
         "p_load_mw": 0, "q_load_mvar": 0, "base_kv": 16.5},
        ...
      ],
      "branches": [
        {"from": 1, "to": 4, "r_pu": 0.0, "x_pu": 0.0576,
         "b_pu": 0.0, "rate_mva": 250, "is_transformer": true},
        ...
      ],
      "external_tie": {"bus_id": 1, "asset_id": "datacenter"}
    }

external_tie.bus_id picks the DSO-internal slack bus whose net flow is
reported upstream. external_tie.asset_id is the identifier used in
POST /api/asset-update — it must match one of grid-central's
ASSET_BUS_MAP keys for the injection to land on the intended outer bus.
"""

import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

GRID_CENTRAL_URL = os.getenv("GRID_CENTRAL_URL", "http://localhost:8000")
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "1.0"))
MODEL_PATH = os.getenv("MODEL_PATH", "/model/model.json")
ASSET_ID_OVERRIDE = os.getenv("ASSET_ID", "")  # optional override of external_tie.asset_id


class DSOEngine:
    """Loads a model.json and solves Newton-Raphson on demand."""

    def __init__(self):
        self.model: Optional[dict] = None
        self.mtime: float = 0.0
        self.n = 0
        self.s_base = 100.0
        self.Y: Optional[np.ndarray] = None
        self.bus_ids: list[int] = []
        self.bus_index: dict[int, int] = {}
        self.bus_types: list[str] = []
        self.V = np.zeros(0)
        self.theta = np.zeros(0)
        self.P_gen = np.zeros(0)
        self.Q_gen = np.zeros(0)
        self.P_load = np.zeros(0)
        self.Q_load = np.zeros(0)
        self.external_tie: dict = {"bus_id": None, "asset_id": "datacenter"}
        self.last_solve: Optional[dict] = None
        self.last_error: Optional[str] = None

    def reload_if_changed(self) -> bool:
        """Returns True if the model was (re)loaded on this call."""
        try:
            st = os.stat(MODEL_PATH)
        except FileNotFoundError:
            if self.model is not None:
                self.last_error = f"model file disappeared: {MODEL_PATH}"
            return False
        if st.st_mtime == self.mtime and self.model is not None:
            return False
        try:
            with open(MODEL_PATH) as f:
                model = json.load(f)
            self._apply_model(model)
            self.mtime = st.st_mtime
            self.last_error = None
            print(f"[dso] loaded model: {len(self.bus_ids)} buses, "
                  f"{len(model.get('branches', []))} branches, "
                  f"tie=bus{self.external_tie['bus_id']}→{self.external_tie['asset_id']}")
            return True
        except Exception as e:
            self.last_error = f"model load failed: {e}"
            print(f"[dso] {self.last_error}")
            return False

    def _apply_model(self, model: dict):
        buses = model["buses"]
        branches = model.get("branches", [])
        self.s_base = float(model.get("s_base", 100.0))
        self.external_tie = model.get("external_tie", {"bus_id": buses[0]["id"], "asset_id": "datacenter"})
        if ASSET_ID_OVERRIDE:
            self.external_tie = {**self.external_tie, "asset_id": ASSET_ID_OVERRIDE}

        self.n = len(buses)
        self.bus_ids = [b["id"] for b in buses]
        self.bus_index = {bid: i for i, bid in enumerate(self.bus_ids)}
        self.bus_types = [b.get("type", "pq") for b in buses]

        self.V = np.array([float(b.get("v_setpoint", 1.0)) for b in buses])
        self.theta = np.zeros(self.n)
        self.P_gen = np.array([float(b.get("p_gen_mw", 0.0)) / self.s_base for b in buses])
        self.Q_gen = np.array([float(b.get("q_gen_mvar", 0.0)) / self.s_base for b in buses])
        self.P_load = np.array([float(b.get("p_load_mw", 0.0)) / self.s_base for b in buses])
        self.Q_load = np.array([float(b.get("q_load_mvar", 0.0)) / self.s_base for b in buses])

        self.model = model
        self._build_ybus(branches)

    def _build_ybus(self, branches: list):
        self.Y = np.zeros((self.n, self.n), dtype=complex)
        for br in branches:
            i = self.bus_index.get(br["from"])
            j = self.bus_index.get(br["to"])
            if i is None or j is None:
                continue
            r = float(br.get("r_pu", 0.0))
            x = float(br.get("x_pu", 0.0))
            b = float(br.get("b_pu", 0.0))
            if abs(r) + abs(x) < 1e-12:
                y_series = complex(0, -1 / x) if abs(x) > 1e-12 else complex(0, 0)
            else:
                y_series = 1 / complex(r, x)
            y_shunt = complex(0, b / 2)
            self.Y[i, i] += y_series + y_shunt
            self.Y[j, j] += y_series + y_shunt
            self.Y[i, j] -= y_series
            self.Y[j, i] -= y_series

    def solve(self, max_iter: int = 20, tol: float = 1e-6) -> dict:
        if self.model is None or self.Y is None or self.n == 0:
            return {"converged": False, "reason": "no model loaded", "error": self.last_error}

        P_spec = self.P_gen - self.P_load
        Q_spec = self.Q_gen - self.Q_load
        V = self.V.copy()
        theta = self.theta.copy()

        pq_buses = [i for i, t in enumerate(self.bus_types) if t == "pq"]
        pv_buses = [i for i, t in enumerate(self.bus_types) if t == "pv"]
        slack = next((i for i, t in enumerate(self.bus_types) if t == "slack"), 0)
        non_slack = [i for i in range(self.n) if i != slack]

        converged = False
        iters = 0
        for it in range(max_iter):
            iters = it + 1
            # Compute P, Q at each bus from current V, theta
            P_calc = np.zeros(self.n)
            Q_calc = np.zeros(self.n)
            for i in range(self.n):
                for k in range(self.n):
                    yik = self.Y[i, k]
                    g = yik.real
                    b = yik.imag
                    dtheta = theta[i] - theta[k]
                    P_calc[i] += V[i] * V[k] * (g * math.cos(dtheta) + b * math.sin(dtheta))
                    Q_calc[i] += V[i] * V[k] * (g * math.sin(dtheta) - b * math.cos(dtheta))

            dP = P_spec[non_slack] - P_calc[non_slack]
            dQ = Q_spec[pq_buses] - Q_calc[pq_buses]
            mismatch = np.concatenate([dP, dQ]) if len(pq_buses) else dP
            if np.max(np.abs(mismatch)) < tol:
                converged = True
                break

            # Build Jacobian
            n_ns = len(non_slack)
            n_pq = len(pq_buses)
            J = np.zeros((n_ns + n_pq, n_ns + n_pq))
            ns_idx = {b: i for i, b in enumerate(non_slack)}
            pq_idx = {b: i for i, b in enumerate(pq_buses)}

            for ii, i in enumerate(non_slack):
                for jj, j in enumerate(non_slack):
                    yij = self.Y[i, j]
                    g, b = yij.real, yij.imag
                    dtheta = theta[i] - theta[j]
                    if i == j:
                        J[ii, jj] = -Q_calc[i] - V[i] ** 2 * b  # dP/dtheta
                    else:
                        J[ii, jj] = V[i] * V[j] * (g * math.sin(dtheta) - b * math.cos(dtheta))
                for jj, j in enumerate(pq_buses):
                    yij = self.Y[i, j]
                    g, b = yij.real, yij.imag
                    dtheta = theta[i] - theta[j]
                    if i == j:
                        J[ii, n_ns + jj] = P_calc[i] / V[i] + V[i] * g  # dP/dV
                    else:
                        J[ii, n_ns + jj] = V[i] * (g * math.cos(dtheta) + b * math.sin(dtheta))
            for ii, i in enumerate(pq_buses):
                for jj, j in enumerate(non_slack):
                    yij = self.Y[i, j]
                    g, b = yij.real, yij.imag
                    dtheta = theta[i] - theta[j]
                    if i == j:
                        J[n_ns + ii, jj] = P_calc[i] - V[i] ** 2 * g  # dQ/dtheta
                    else:
                        J[n_ns + ii, jj] = -V[i] * V[j] * (g * math.cos(dtheta) + b * math.sin(dtheta))
                for jj, j in enumerate(pq_buses):
                    yij = self.Y[i, j]
                    g, b = yij.real, yij.imag
                    dtheta = theta[i] - theta[j]
                    if i == j:
                        J[n_ns + ii, n_ns + jj] = Q_calc[i] / V[i] - V[i] * b  # dQ/dV
                    else:
                        J[n_ns + ii, n_ns + jj] = V[i] * (g * math.sin(dtheta) - b * math.cos(dtheta))

            try:
                dx = np.linalg.solve(J, mismatch)
            except np.linalg.LinAlgError as e:
                return {"converged": False, "reason": f"singular Jacobian: {e}", "iterations": iters}

            for ii, i in enumerate(non_slack):
                theta[i] += dx[ii]
            for ii, i in enumerate(pq_buses):
                V[i] += dx[n_ns + ii]

        self.V = V
        self.theta = theta

        # Slack bus injection (net generation)
        slack_P = P_calc[slack] * self.s_base
        slack_Q = Q_calc[slack] * self.s_base

        # External tie: report whatever bus the model designates
        tie_bus_id = self.external_tie.get("bus_id")
        tie_idx = self.bus_index.get(tie_bus_id, slack)
        tie_P = P_calc[tie_idx] * self.s_base
        tie_Q = Q_calc[tie_idx] * self.s_base

        self.last_solve = {
            "converged": converged,
            "iterations": iters,
            "s_base": self.s_base,
            "external_tie": self.external_tie,
            "tie_injection": {
                "bus_id": tie_bus_id,
                "p_mw": round(tie_P, 3),
                "q_mvar": round(tie_Q, 3),
            },
            "buses": [
                {
                    "id": self.bus_ids[i],
                    "type": self.bus_types[i],
                    "v_pu": round(V[i], 4),
                    "theta_deg": round(math.degrees(theta[i]), 3),
                    "p_net_mw": round((self.P_gen[i] - self.P_load[i]) * self.s_base, 2),
                    "q_net_mvar": round((self.Q_gen[i] - self.Q_load[i]) * self.s_base, 2),
                }
                for i in range(self.n)
            ],
            "slack": {"bus_id": self.bus_ids[slack], "p_mw": round(slack_P, 3), "q_mvar": round(slack_Q, 3)},
            "timestamp": time.time(),
        }
        return self.last_solve


engine = DSOEngine()
ws_clients: list[WebSocket] = []


async def sim_loop():
    """Tick: reload model if changed, solve, push upstream, broadcast locally."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            engine.reload_if_changed()
            result = engine.solve()

            if result.get("converged"):
                tie = result["tie_injection"]
                asset_id = engine.external_tie.get("asset_id", "datacenter")
                # Tie injection convention: P>0 at tie means DSO draws from outer grid.
                # Outer grid-central therefore sees this as a LOAD on its bus.
                p_mw = tie["p_mw"]
                q_mvar = tie["q_mvar"]
                payload = {
                    "asset_id": asset_id,
                    "p_gen_mw": max(0.0, -p_mw),
                    "q_gen_mvar": max(0.0, -q_mvar),
                    "p_load_mw": max(0.0, p_mw),
                    "q_load_mvar": max(0.0, q_mvar),
                    "metadata": {
                        "source": "dso",
                        "tie_bus_id": tie["bus_id"],
                        "iterations": result["iterations"],
                    },
                }
                try:
                    await client.post(f"{GRID_CENTRAL_URL}/api/asset-update", json=payload)
                except Exception as e:
                    print(f"[dso] upstream push failed: {e}")

            msg = json.dumps(result)
            dead = []
            for ws in ws_clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                ws_clients.remove(ws)

            await asyncio.sleep(TICK_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.reload_if_changed()
    task = asyncio.create_task(sim_loop())
    yield
    task.cancel()


app = FastAPI(title="DSO Simulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/status")
async def get_status():
    return {
        "model_path": MODEL_PATH,
        "model_loaded": engine.model is not None,
        "model_mtime": engine.mtime,
        "last_error": engine.last_error,
        "last_solve": engine.last_solve,
        "grid_central_url": GRID_CENTRAL_URL,
    }


@app.get("/api/model")
async def get_model():
    return {"model": engine.model, "mtime": engine.mtime, "path": MODEL_PATH}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    if engine.last_solve:
        await ws.send_text(json.dumps(engine.last_solve))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)


@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")
