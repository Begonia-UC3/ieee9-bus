"""
Battery Energy Storage System (BESS) Simulator
================================================
Simulates a large grid-scale battery (~85 MW / 340 MWh).
Models: SoC, charge/discharge cycles, cell temperature, degradation.
Pushes telemetry to the central grid engine every tick.
"""

import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

GRID_CENTRAL_URL = os.getenv("GRID_CENTRAL_URL", "http://localhost:8000")
ASSET_ID = "battery"
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "1.0"))
AUTO_MODE = os.getenv("AUTO_MODE", "true").lower() in ("1", "true", "yes")

# ── Battery Parameters ──
RATED_POWER_MW = 85.0
CAPACITY_MWH = 340.0       # 4-hour battery
EFFICIENCY_RT = 0.88        # round-trip efficiency
CELL_NOMINAL_V = 3.7        # V per cell (NMC)
PACK_VOLTAGE_NOM = 1500.0   # V DC bus
CELL_COUNT_SERIES = 405
MODULES = 200


class BESSState:
    def __init__(self):
        self.mode = "idle"              # "charging", "discharging", "idle"
        self.target_power_mw = 0.0      # + = discharge, - = charge
        self.actual_power_mw = 0.0
        self.soc_pct = 65.0             # state of charge
        self.soc_mwh = CAPACITY_MWH * 0.65
        self.cell_temp_c = 25.0
        self.pack_voltage_v = PACK_VOLTAGE_NOM
        self.pack_current_a = 0.0
        self.cycle_count = 42.0
        self.soh_pct = 98.5             # state of health
        self.tick = 0
        self.ramp_rate_mw_s = 10.0      # MW/s — batteries ramp fast
        self.fault = None
        self.hvac_power_kw = 50.0       # cooling system

    def step(self, dt: float):
        self.tick += 1

        # Auto mode: smooth charge/discharge cycle ~5 min period.
        # Drive target_power_mw with a sinusoid of ±60% rated when SoC is in
        # a usable band; let SoC bounds clip extremes.
        if AUTO_MODE:
            cycle = math.sin(self.tick * TICK_INTERVAL * 2 * math.pi / 300)  # 5-min period
            self.target_power_mw = 0.6 * RATED_POWER_MW * cycle
            if self.target_power_mw > 1.0:
                self.mode = "discharging"
            elif self.target_power_mw < -1.0:
                self.mode = "charging"
            else:
                self.mode = "idle"

        # Ramp to target
        diff = self.target_power_mw - self.actual_power_mw
        max_ramp = self.ramp_rate_mw_s * dt
        if abs(diff) < max_ramp:
            self.actual_power_mw = self.target_power_mw
        else:
            self.actual_power_mw += max_ramp * (1 if diff > 0 else -1)

        # Enforce SoC limits
        if self.soc_pct <= 5 and self.actual_power_mw > 0:
            self.actual_power_mw = 0
        if self.soc_pct >= 98 and self.actual_power_mw < 0:
            self.actual_power_mw = 0

        # Clamp to rated
        self.actual_power_mw = max(-RATED_POWER_MW, min(RATED_POWER_MW, self.actual_power_mw))

        # Update SoC
        eff = math.sqrt(EFFICIENCY_RT)  # one-way eff
        dt_h = dt / 3600.0
        if self.actual_power_mw > 0:
            # Discharging
            energy_out = self.actual_power_mw * dt_h
            self.soc_mwh -= energy_out / eff
            self.mode = "discharging"
        elif self.actual_power_mw < 0:
            # Charging
            energy_in = abs(self.actual_power_mw) * dt_h
            self.soc_mwh += energy_in * eff
            self.mode = "charging"
        else:
            self.mode = "idle"

        self.soc_mwh = max(0, min(CAPACITY_MWH, self.soc_mwh))
        self.soc_pct = self.soc_mwh / CAPACITY_MWH * 100

        # OCV curve (simplified NMC): V = f(SoC)
        soc_frac = self.soc_pct / 100
        ocv_per_cell = 3.0 + 0.9 * soc_frac - 0.2 * soc_frac ** 2
        self.pack_voltage_v = ocv_per_cell * CELL_COUNT_SERIES

        # Current
        if abs(self.actual_power_mw) > 0.01:
            self.pack_current_a = self.actual_power_mw * 1e6 / self.pack_voltage_v
        else:
            self.pack_current_a = 0

        # Cell temperature model
        i_pu = abs(self.pack_current_a) / (RATED_POWER_MW * 1e6 / PACK_VOLTAGE_NOM)
        heat_gen = i_pu ** 2 * 15  # °C contribution from I²R
        ambient = 25.0
        hvac_cooling = self.hvac_power_kw / 200  # simplified cooling rate
        temp_target = ambient + heat_gen - hvac_cooling
        self.cell_temp_c += (temp_target - self.cell_temp_c) * 0.08
        self.cell_temp_c += math.sin(self.tick * 0.3) * 0.3

        # Degradation: cycle counting (very simplified)
        if abs(self.actual_power_mw) > 1:
            self.cycle_count += abs(self.actual_power_mw) * dt_h / CAPACITY_MWH
        self.soh_pct = max(70, 100 - self.cycle_count * 0.003)

    def to_dict(self):
        return {
            "asset_id": ASSET_ID,
            "tick": self.tick,
            "mode": self.mode,
            "target_power_mw": round(self.target_power_mw, 2),
            "actual_power_mw": round(self.actual_power_mw, 2),
            "soc_pct": round(self.soc_pct, 2),
            "soc_mwh": round(self.soc_mwh, 2),
            "capacity_mwh": CAPACITY_MWH,
            "cell_temp_c": round(self.cell_temp_c, 1),
            "pack_voltage_v": round(self.pack_voltage_v, 1),
            "pack_current_a": round(self.pack_current_a, 1),
            "cycle_count": round(self.cycle_count, 1),
            "soh_pct": round(self.soh_pct, 2),
            "hvac_power_kw": round(self.hvac_power_kw, 1),
            "fault": self.fault,
            "timestamp": time.time(),
        }


state = BESSState()
ws_clients: list[WebSocket] = []


async def sim_loop():
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            state.step(TICK_INTERVAL)
            snapshot = state.to_dict()

            # Determine grid injection: discharge = generation, charge = load
            p_gen = max(0, snapshot["actual_power_mw"])
            p_load = abs(min(0, snapshot["actual_power_mw"]))

            try:
                await client.post(f"{GRID_CENTRAL_URL}/api/asset-update", json={
                    "asset_id": ASSET_ID,
                    "p_gen_mw": p_gen,
                    "q_gen_mvar": 0,
                    "p_load_mw": p_load,
                    "q_load_mvar": 0,
                    "metadata": {
                        "soc_pct": snapshot["soc_pct"],
                        "mode": snapshot["mode"],
                        "cell_temp_c": snapshot["cell_temp_c"],
                    },
                })
            except Exception as e:
                print(f"[battery] grid push failed: {e}")

            msg = json.dumps(snapshot)
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
    task = asyncio.create_task(sim_loop())
    yield
    task.cancel()


app = FastAPI(title="BESS Simulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SetpointCmd(BaseModel):
    target_power_mw: float | None = None   # + discharge, - charge


@app.post("/api/setpoint")
async def set_setpoint(cmd: SetpointCmd):
    if cmd.target_power_mw is not None:
        state.target_power_mw = max(-RATED_POWER_MW, min(RATED_POWER_MW, cmd.target_power_mw))
    return {"status": "ok", "state": state.to_dict()}


@app.get("/api/status")
async def get_status():
    return state.to_dict()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    await ws.send_text(json.dumps(state.to_dict()))
    try:
        while True:
            data = await ws.receive_text()
            try:
                cmd = json.loads(data)
                if "target_power_mw" in cmd:
                    state.target_power_mw = max(-RATED_POWER_MW, min(RATED_POWER_MW, cmd["target_power_mw"]))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)


@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")
