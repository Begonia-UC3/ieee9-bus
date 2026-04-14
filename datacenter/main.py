"""
Data Center Load Simulator
============================
Simulates a small data center (~125 MW peak).
Models: IT load, cooling (PUE), UPS losses, server utilization.
Pushes load telemetry to the central grid engine every tick.
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
ASSET_ID = "datacenter"
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "1.0"))

# ── DC Parameters ──
IT_CAPACITY_MW = 80.0       # max IT load
TOTAL_CAPACITY_MW = 125.0   # including cooling, UPS, lighting
PUE_BASE = 1.45             # Power Usage Effectiveness
COOLING_TOWERS = 12
UPS_EFFICIENCY = 0.96
RACKS = 2000
SERVERS_PER_RACK = 40


class DataCenterState:
    def __init__(self):
        self.it_load_pct = 60.0          # IT utilization %
        self.it_load_mw = 0.0
        self.cooling_load_mw = 0.0
        self.ups_loss_mw = 0.0
        self.lighting_misc_mw = 2.5
        self.total_load_mw = 0.0
        self.pue = PUE_BASE
        self.ambient_temp_c = 30.0
        self.supply_temp_c = 18.0
        self.return_temp_c = 32.0
        self.server_util_pct = 60.0
        self.active_servers = int(RACKS * SERVERS_PER_RACK * 0.85)
        self.network_gbps = 0.0
        self.tick = 0
        self.fault = None
        # Diurnal pattern params
        self.diurnal_amplitude = 15.0    # ±% of IT load swing
        self.diurnal_phase = 0.0         # radians

    def step(self, dt: float):
        self.tick += 1

        # Diurnal IT load pattern: peaks during business hours
        hour_angle = (self.tick * dt / 3600 * 2 * math.pi / 24) + self.diurnal_phase
        diurnal_factor = 1 + (self.diurnal_amplitude / 100) * math.sin(hour_angle)

        # Random spikes (batch jobs, deployments)
        spike = 0
        if self.tick % 37 == 0:
            spike = 8 * math.sin(self.tick * 0.1) ** 2
        noise = math.sin(self.tick * 0.5) * 1.5 + math.sin(self.tick * 1.7) * 0.8

        self.server_util_pct = self.it_load_pct * diurnal_factor + spike + noise
        self.server_util_pct = max(10, min(100, self.server_util_pct))

        # IT power: non-linear with utilization (idle servers still draw ~40% power)
        util_frac = self.server_util_pct / 100
        power_frac = 0.4 + 0.6 * util_frac  # idle = 40% of full power
        self.it_load_mw = IT_CAPACITY_MW * power_frac

        # Ambient temperature affects PUE
        self.ambient_temp_c = 30 + 8 * math.sin(hour_angle - 0.5) + noise * 0.3
        pue_temp_factor = 1 + max(0, (self.ambient_temp_c - 25)) * 0.008
        self.pue = PUE_BASE * pue_temp_factor

        # Cooling load
        self.cooling_load_mw = self.it_load_mw * (self.pue - 1) - self.lighting_misc_mw
        self.cooling_load_mw = max(0, self.cooling_load_mw)

        # UPS losses
        self.ups_loss_mw = self.it_load_mw * (1 / UPS_EFFICIENCY - 1)

        # Total facility load
        self.total_load_mw = self.it_load_mw + self.cooling_load_mw + self.ups_loss_mw + self.lighting_misc_mw

        # Temps
        self.supply_temp_c = 18 + (self.ambient_temp_c - 25) * 0.1
        self.return_temp_c = self.supply_temp_c + 12 + util_frac * 4

        # Network
        self.network_gbps = 400 * util_frac * (1 + noise * 0.01)

        # Active servers
        self.active_servers = int(RACKS * SERVERS_PER_RACK * 0.85 * (0.9 + 0.1 * util_frac))

    def to_dict(self):
        return {
            "asset_id": ASSET_ID,
            "tick": self.tick,
            "it_load_pct": round(self.server_util_pct, 1),
            "it_load_mw": round(self.it_load_mw, 2),
            "cooling_load_mw": round(self.cooling_load_mw, 2),
            "ups_loss_mw": round(self.ups_loss_mw, 2),
            "lighting_misc_mw": round(self.lighting_misc_mw, 2),
            "total_load_mw": round(self.total_load_mw, 2),
            "pue": round(self.pue, 3),
            "ambient_temp_c": round(self.ambient_temp_c, 1),
            "supply_temp_c": round(self.supply_temp_c, 1),
            "return_temp_c": round(self.return_temp_c, 1),
            "active_servers": self.active_servers,
            "total_servers": RACKS * SERVERS_PER_RACK,
            "racks": RACKS,
            "network_gbps": round(self.network_gbps, 1),
            "fault": self.fault,
            "timestamp": time.time(),
        }


state = DataCenterState()
ws_clients: list[WebSocket] = []


async def sim_loop():
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            state.step(TICK_INTERVAL)
            snapshot = state.to_dict()

            try:
                await client.post(f"{GRID_CENTRAL_URL}/api/asset-update", json={
                    "asset_id": ASSET_ID,
                    "p_gen_mw": 0,
                    "q_gen_mvar": 0,
                    "p_load_mw": snapshot["total_load_mw"],
                    "q_load_mvar": snapshot["total_load_mw"] * 0.3,  # PF ~0.95
                    "metadata": {
                        "pue": snapshot["pue"],
                        "it_load_pct": snapshot["it_load_pct"],
                        "active_servers": snapshot["active_servers"],
                    },
                })
            except Exception as e:
                print(f"[datacenter] grid push failed: {e}")

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


app = FastAPI(title="Data Center Load Simulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SetpointCmd(BaseModel):
    it_load_pct: float | None = None


@app.post("/api/setpoint")
async def set_setpoint(cmd: SetpointCmd):
    if cmd.it_load_pct is not None:
        state.it_load_pct = max(10, min(100, cmd.it_load_pct))
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
                if "it_load_pct" in cmd:
                    state.it_load_pct = max(10, min(100, cmd["it_load_pct"]))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)


@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")
