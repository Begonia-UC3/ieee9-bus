"""
Diesel Generator Simulator
============================
Simulates a large industrial diesel generator (~160 MW).
Iteratively computes: RPM, fuel consumption, exhaust temp, power output.
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
ASSET_ID = "diesel-gen"
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "1.0"))
AUTO_MODE = os.getenv("AUTO_MODE", "true").lower() in ("1", "true", "yes")

# ── Engine Model Parameters ──
# Uprated from IEEE-9 textbook 163 MW to 500 MW: with 3-5 DSOs
# co-deployed each reporting ~170 MW tie load, IEEE-9 needs more
# generation headroom near the load buses to stay inside NR's stable
# region. AUTO_MODE walks 65-90% of rated → 325-450 MW local gen.
RATED_POWER_MW = 500.0
RATED_RPM = 900
CYLINDERS = 20
DISPLACEMENT_L = 580.0  # liters total
FUEL_RATE_BASE = 195.0  # g/kWh at rated
EXHAUST_TEMP_BASE = 480.0  # °C at rated
COOLANT_TEMP_BASE = 85.0  # °C


class DieselGenState:
    def __init__(self):
        self.running = True
        self.target_load_pct = 75.0   # % of rated
        self.actual_load_pct = 0.0
        self.p_gen_mw = 0.0
        self.rpm = 0.0
        self.fuel_rate_lph = 0.0      # liters/hour
        self.exhaust_temp_c = 25.0
        self.coolant_temp_c = 25.0
        self.oil_pressure_bar = 0.0
        self.frequency_hz = 0.0
        self.voltage_kv = 0.0
        self.runtime_hours = 0.0
        self.tick = 0
        self.ramp_rate = 2.0          # % per second
        self.fault = None

    def step(self, dt: float):
        """Advance simulation by dt seconds."""
        self.tick += 1
        self.runtime_hours += dt / 3600

        # Auto mode: smooth setpoint walk between 40% and 90% rated.
        if AUTO_MODE and self.running:
            cycle = math.sin(self.tick * TICK_INTERVAL * 2 * math.pi / 240)  # 4-min period
            self.target_load_pct = 65.0 + 25.0 * cycle

        if not self.running:
            # Cooldown
            self.actual_load_pct = max(0, self.actual_load_pct - self.ramp_rate * dt * 3)
            self.rpm = max(0, self.rpm - 50 * dt)
            self.exhaust_temp_c += (25 - self.exhaust_temp_c) * 0.02
            self.coolant_temp_c += (25 - self.coolant_temp_c) * 0.01
            self.p_gen_mw = 0
            self.fuel_rate_lph = 0
            self.oil_pressure_bar = max(0, self.oil_pressure_bar - 0.5 * dt)
            self.frequency_hz = self.rpm / 60 * 2  # 2-pole
            self.voltage_kv = 18.0 * (self.rpm / RATED_RPM) if self.rpm > 100 else 0
            return

        # Ramp load
        load_diff = self.target_load_pct - self.actual_load_pct
        max_change = self.ramp_rate * dt
        if abs(load_diff) < max_change:
            self.actual_load_pct = self.target_load_pct
        else:
            self.actual_load_pct += max_change * (1 if load_diff > 0 else -1)
        self.actual_load_pct = max(0, min(110, self.actual_load_pct))

        # Power output
        load_frac = self.actual_load_pct / 100
        self.p_gen_mw = RATED_POWER_MW * load_frac

        # RPM: governed to 900 ± small droop
        droop = 4.0  # % droop
        rpm_target = RATED_RPM * (1 - droop / 100 * (load_frac - 0.5))
        self.rpm += (rpm_target - self.rpm) * 0.3
        noise = math.sin(self.tick * 0.7) * 2 + math.sin(self.tick * 1.3) * 1
        self.rpm += noise

        # Frequency
        self.frequency_hz = self.rpm / 60 * 2

        # Voltage
        self.voltage_kv = 18.0 * (1 + (self.rpm - RATED_RPM) / RATED_RPM * 0.1)

        # Fuel consumption: SFC curve (g/kWh) — U-shaped, optimal ~75%
        sfc = FUEL_RATE_BASE * (1 + 0.15 * (load_frac - 0.75) ** 2)
        p_kw = self.p_gen_mw * 1000
        fuel_g_per_h = sfc * p_kw
        diesel_density = 835  # g/L
        self.fuel_rate_lph = fuel_g_per_h / diesel_density

        # Exhaust temperature
        exhaust_target = EXHAUST_TEMP_BASE * (0.5 + 0.6 * load_frac)
        self.exhaust_temp_c += (exhaust_target - self.exhaust_temp_c) * 0.1
        self.exhaust_temp_c += math.sin(self.tick * 0.4) * 3

        # Coolant temperature
        coolant_target = COOLANT_TEMP_BASE * (0.7 + 0.35 * load_frac)
        self.coolant_temp_c += (coolant_target - self.coolant_temp_c) * 0.05

        # Oil pressure
        oil_target = 4.5 - 0.8 * load_frac
        self.oil_pressure_bar += (oil_target - self.oil_pressure_bar) * 0.1

    def to_dict(self):
        return {
            "asset_id": ASSET_ID,
            "tick": self.tick,
            "running": self.running,
            "target_load_pct": round(self.target_load_pct, 1),
            "actual_load_pct": round(self.actual_load_pct, 1),
            "p_gen_mw": round(self.p_gen_mw, 2),
            "rpm": round(self.rpm, 1),
            "frequency_hz": round(self.frequency_hz, 2),
            "voltage_kv": round(self.voltage_kv, 2),
            "fuel_rate_lph": round(self.fuel_rate_lph, 1),
            "exhaust_temp_c": round(self.exhaust_temp_c, 1),
            "coolant_temp_c": round(self.coolant_temp_c, 1),
            "oil_pressure_bar": round(self.oil_pressure_bar, 2),
            "runtime_hours": round(self.runtime_hours, 2),
            "fault": self.fault,
            "timestamp": time.time(),
        }


state = DieselGenState()
ws_clients: list[WebSocket] = []


async def sim_loop():
    """Main simulation + push loop."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            state.step(TICK_INTERVAL)
            snapshot = state.to_dict()

            # Push to grid central
            try:
                await client.post(f"{GRID_CENTRAL_URL}/api/asset-update", json={
                    "asset_id": ASSET_ID,
                    "p_gen_mw": snapshot["p_gen_mw"],
                    "q_gen_mvar": snapshot["p_gen_mw"] * 0.1,  # assume ~0.95 pf
                    "p_load_mw": 0.0,
                    "q_load_mvar": 0.0,
                    "metadata": {
                        "rpm": snapshot["rpm"],
                        "exhaust_temp_c": snapshot["exhaust_temp_c"],
                        "fuel_rate_lph": snapshot["fuel_rate_lph"],
                    },
                })
            except Exception as e:
                print(f"[diesel-gen] grid push failed: {e}")

            # Broadcast to local WS clients
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


app = FastAPI(title="Diesel Generator Simulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SetpointCmd(BaseModel):
    target_load_pct: float | None = None
    running: bool | None = None


@app.post("/api/setpoint")
async def set_setpoint(cmd: SetpointCmd):
    if cmd.target_load_pct is not None:
        state.target_load_pct = max(0, min(110, cmd.target_load_pct))
    if cmd.running is not None:
        state.running = cmd.running
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
                if "target_load_pct" in cmd:
                    state.target_load_pct = max(0, min(110, cmd["target_load_pct"]))
                if "running" in cmd:
                    state.running = cmd["running"]
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)


@app.get("/")
async def serve_frontend():
    return FileResponse("frontend/index.html")
