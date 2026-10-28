"""
DROPSAT Mission Control Dashboard  |  SatForge UAE 2026
=========================================================
DROPSAT is a 1U CubeSat that finds water-pipeline leaks in the desert by spotting
sand that is cooler than its surroundings (infrared ground-temperature scan).

Run locally:   pip install -r requirements.txt
               streamlit run dropsat_dashboard.py
Live link:     deploy free on Streamlit Community Cloud (share.streamlit.io)

DATA SOURCES (sidebar)
  1. Simulator      - built-in mission simulation (Phase 1 / demos without hardware)
  2. Serial (LoRa)  - ESP32 ground station plugged in by USB (Qualifier / Finals)
  3. CSV replay     - replay a recorded run

GROUND-STATION SERIAL PROTOCOL (one ASCII line per packet, 115200 baud)
  Telemetry:  TLM,seq,ms,mode,servo,ir,amb,roll,pitch,yaw,lux,vbat,vsol,ima,tint,hum,pres,rssi,snr
  Alert:      LEAK,km42,-3.1C
  Units: servo deg | ir/amb/tint degC | roll/pitch/yaw deg | lux | vbat/vsol V |
         ima mA | hum %RH | pres hPa | rssi dBm | snr dB
"""

from __future__ import annotations

import math
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except Exception:  # pyserial is optional
    HAS_SERIAL = False

# ----------------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------------
APP_TITLE = "DROPSAT Mission Control"
MISSION_TAG = "SatForge UAE 2026  |  1U CubeSat  |  Desert Pipeline Leak Detection"
PIPE_START = (25.1288, 56.3265)   # Fujairah
PIPE_END = (24.2075, 55.7447)     # Al Ain
PIPE_NAME = "Fujairah to Al Ain water pipeline"
MAX_ROWS = 6000
SWEEP_SAMPLES = 90                # samples for one servo sweep (0 to 180 deg)
ORBIT_SAMPLES = 180               # compressed orbit used by the simulator
MU_EARTH = 398600.4418            # km^3/s^2
R_EARTH = 6371.0                  # km
C_LIGHT = 299792.458              # km/s

PACKET_FIELDS = ["seq", "ms", "mode", "servo", "ir", "amb", "roll", "pitch", "yaw",
                 "lux", "vbat", "vsol", "ima", "tint", "hum", "pres", "rssi", "snr"]

PALETTE = {
    "bg": "#0b1020", "panel": "#121a33", "line": "#26325c", "text": "#e8ecff",
    "muted": "#8f9bc7", "cyan": "#22d3ee", "blue": "#4f8cff", "green": "#34d399",
    "amber": "#fbbf24", "red": "#f87171", "violet": "#a78bfa", "orange": "#fb923c",
}

st.set_page_config(page_title=APP_TITLE, page_icon="🛰️", layout="wide",
                   initial_sidebar_state="expanded")






st.markdown("""
<style>

/* ALL TEXT WHITE */
html, body, p, span, label, h1, h2, h3, h4, h5, h6 {
    color: #FFFFFF !important;
}

/* STREAMLIT MAIN TEXT */
[data-testid="stAppViewContainer"] {
    color: #FFFFFF !important;
}

/* SIDEBAR TEXT */
[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}

/* METRIC NUMBERS */
[data-testid="stMetricValue"] * {
    color: #FFFFFF !important;
}

/* METRIC LABELS */
[data-testid="stMetricLabel"] * {
    color: #FFFFFF !important;
}

/* TABS */
button[data-baseweb="tab"] {
    color: #FFFFFF !important;
}

/* SLIDER LABELS */
[data-testid="stWidgetLabel"] * {
    color: #FFFFFF !important;
}

/* HEADINGS */
h1, h2, h3, h4 {
    color: #FFFFFF !important;
}

/* CUSTOM DASHBOARD CARDS */
.hero h1, .card h4, .card .big, .card .sub {
    color: #FFFFFF !important;
}

</style>
""", unsafe_allow_html=True)










CSS = f"""
<style>
.stApp {{ background: #123B7A; }}
section[data-testid="stSidebar"] {{ background: #0d1430; border-right: 1px solid {PALETTE['line']}; }}
h1, h2, h3, h4 {{ color: {PALETTE['text']}; letter-spacing: .3px; }}
.hero {{ padding: 18px 24px; border: 1px solid {PALETTE['line']}; border-radius: 16px;
         background: linear-gradient(120deg, #16224d 0%, #0f1733 60%, #0b1020 100%); margin-bottom: 14px; }}
.hero h1 {{ margin: 0; font-size: 2.0rem; }}
.hero p {{ margin: 4px 0 0 0; color: {PALETTE['muted']}; font-size: .95rem; }}
.pill {{ display:inline-block; padding: 3px 12px; border-radius: 999px; font-size: .78rem;
         font-weight: 700; letter-spacing: .8px; margin-right: 6px; }}
.pill.ok {{ background:#06402b; color:{PALETTE['green']}; border:1px solid {PALETTE['green']}; }}
.pill.warn {{ background:#4a3606; color:{PALETTE['amber']}; border:1px solid {PALETTE['amber']}; }}
.pill.bad {{ background:#4a1010; color:{PALETTE['red']}; border:1px solid {PALETTE['red']}; }}
.pill.info {{ background:#0d2f45; color:{PALETTE['cyan']}; border:1px solid {PALETTE['cyan']}; }}
.card {{ border:1px solid {PALETTE['line']}; border-radius: 14px; padding: 14px 16px;
         background: {PALETTE['panel']}; height: 100%; }}
.card h4 {{ margin: 0 0 6px 0; font-size: .9rem; color: {PALETTE['muted']}; text-transform: uppercase;
            letter-spacing: 1px; }}
.card .big {{ font-size: 1.7rem; font-weight: 700; color: {PALETTE['text']}; }}
.card .sub {{ color: {PALETTE['muted']}; font-size: .8rem; }}
div[data-testid="stMetric"] {{ background: {PALETTE['panel']}; border: 1px solid {PALETTE['line']};
         padding: 10px 14px; border-radius: 12px; }}
div[data-testid="stMetricLabel"] p {{ color: {PALETTE['muted']}; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
.stTabs [data-baseweb="tab"] {{ background: {PALETTE['panel']}; border-radius: 10px 10px 0 0;
         padding: 8px 14px; border: 1px solid {PALETTE['line']}; }}
.stTabs [aria-selected="true"] {{ background: #1b2a5e; }}
.leakbanner {{ border:1px solid {PALETTE['red']}; background: linear-gradient(90deg,#3b0d0d,#1a0a12);
         border-radius: 14px; padding: 14px 18px; margin-bottom: 12px; color:#ffd9d9; }}
.okbanner {{ border:1px solid {PALETTE['green']}; background: linear-gradient(90deg,#06301f,#0b1a18);
         border-radius: 14px; padding: 14px 18px; margin-bottom: 12px; color:#d2ffe9; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def style_fig(fig: go.Figure, height: int = 340, title: str | None = None) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", height=height, title=title,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(18,26,51,0.6)",
        margin=dict(l=40, r=20, t=50 if title else 20, b=40),
        font=dict(color=PALETTE["text"], size=12),
        legend=dict(orientation="h", y=1.12, x=0),
    )
    fig.update_xaxes(gridcolor=PALETTE["line"], zeroline=False)
    fig.update_yaxes(gridcolor=PALETTE["line"], zeroline=False)
    return fig


# ----------------------------------------------------------------------------
# DATA SOURCES
# ----------------------------------------------------------------------------
def format_packet(r: dict) -> str:
    return "TLM," + ",".join(
        str(r[k]) if k in ("seq", "ms", "mode") else
        (f"{r[k]:.2f}" if isinstance(r[k], float) else str(r[k])) for k in PACKET_FIELDS)


def parse_line(line: str, now: float | None = None) -> dict | None:
    """Parse one ground-station line into a telemetry row or a leak alert."""
    now = now or time.time()
    line = line.strip()
    if not line:
        return None
    if line.startswith("TLM,"):
        parts = line.split(",")[1:]
        if len(parts) < len(PACKET_FIELDS):
            return None
        try:
            row = {"t": now}
            for k, v in zip(PACKET_FIELDS, parts):
                row[k] = v.strip() if k == "mode" else float(v)
            row["seq"] = int(row["seq"])
            return {"kind": "tlm", "row": row, "raw": line}
        except ValueError:
            return None
    m = re.match(r"LEAK,km\s*([\d.]+),\s*(-?[\d.]+)\s*C?", line)
    if m:
        return {"kind": "alert", "km": float(m.group(1)), "delta": float(m.group(2)),
                "t": now, "raw": line}
    return {"kind": "raw", "raw": line}


class Simulator:
    """Physics-flavoured simulator that mimics what the satellite would radio down."""

    def __init__(self, pipe_km: float = 185.0, threshold: float = 2.0):
        self.n = 0
        self.seq = 0
        self.soc = 0.85
        self.pipe_km = pipe_km
        self.threshold = threshold
        self.leaks: list[tuple[float, float, float]] = []   # (km, depth C, sigma km)
        self.buf: deque = deque(maxlen=45)
        self.in_exc = False
        self.min_d = 0.0
        self.min_km = 0.0
        self.hold = 0
        self.rng = np.random.default_rng(7)

    def step(self) -> list[dict]:
        out = []
        self.n += 1
        self.seq += 1
        n = self.n
        phase = (n % ORBIT_SAMPLES) / ORBIT_SAMPLES
        sun = phase < 0.62
        p = (n % (2 * SWEEP_SAMPLES)) / SWEEP_SAMPLES
        frac = p if p <= 1 else 2 - p
        servo = 180.0 * frac
        km = frac * self.pipe_km

        base = 47.0 + 3.0 * math.sin(2 * math.pi * n / 700) + 0.004 * km
        dip = sum(d * math.exp(-0.5 * ((km - k) / s) ** 2) for k, d, s in self.leaks)
        ir = base - dip + self.rng.normal(0, 0.35)
        amb = 38.0 + 2.0 * math.sin(2 * math.pi * n / 700) + self.rng.normal(0, 0.2)

        # onboard detector
        base_est = float(np.median(self.buf)) if len(self.buf) >= 10 else None
        self.buf.append(ir)
        delta = 0.0 if base_est is None else ir - base_est
        flagged = base_est is not None and delta <= -self.threshold
        alert = None
        if flagged:
            if not self.in_exc:
                self.in_exc, self.min_d, self.min_km = True, delta, km
            if delta < self.min_d:
                self.min_d, self.min_km = delta, km
        elif self.in_exc:
            self.in_exc = False
            alert = {"kind": "alert", "km": round(self.min_km), "delta": round(self.min_d, 1),
                     "t": time.time(), "raw": f"LEAK,km{round(self.min_km)},{self.min_d:.1f}C"}
            self.hold = 8

        # power
        self.soc = float(np.clip(self.soc + (0.0011 if sun else -0.0009), 0.05, 1.0))
        current = 95 + 45 * (1 if self.hold == 0 else 0) + (110 if alert else 0) + self.rng.normal(0, 4)
        vbat = 3.3 + 0.9 * self.soc + self.rng.normal(0, 0.01)
        vsol = (5.0 + self.rng.normal(0, 0.08)) if sun else 0.0
        if self.soc < 0.15:
            mode = "SAFE"
        elif self.hold > 0 or flagged:
            mode = "LEAK_ALERT"
        else:
            mode = "SCAN"
        self.hold = max(0, self.hold - 1)

        row = {
            "t": time.time(), "seq": self.seq, "ms": int(n * 1000), "mode": mode,
            "servo": round(servo, 1), "ir": round(ir, 2), "amb": round(amb, 2),
            "roll": round(float(2.5 * math.sin(n / 17) + self.rng.normal(0, 0.4)), 2),
            "pitch": round(float(2.0 * math.cos(n / 23) + self.rng.normal(0, 0.4)), 2),
            "yaw": round(float((n * 0.6) % 360), 2),
            "lux": round(float(max(0, 52000 * (math.sin(math.pi * phase / 0.62) if sun else 0)
                                   + self.rng.normal(0, 150))), 0),
            "vbat": round(vbat, 3), "vsol": round(vsol, 2), "ima": round(current, 1),
            "tint": round(float(31 + 6 * (1 if sun else 0) * math.sin(math.pi * phase / 0.62 + 1e-6)
                                + 0.01 * n % 3 + self.rng.normal(0, 0.15)), 2),
            "hum": round(float(28 + self.rng.normal(0, 0.8)), 1),
            "pres": round(float(1008 + self.rng.normal(0, 0.4)), 1),
            "rssi": round(float(-92 + 5 * math.sin(n / 40) + self.rng.normal(0, 1.5)), 1),
            "snr": round(float(7.5 + 1.5 * math.sin(n / 40) + self.rng.normal(0, 0.5)), 1),
        }
        if self.rng.random() > 0.02:   # ~2% simulated packet loss
            out.append({"kind": "tlm", "row": row, "raw": format_packet(row)})
        if alert:
            out.append(alert)
        return out


class SerialSource:
    """Background thread that reads the ESP32 ground station."""

    def __init__(self, port: str, baud: int):
        self.q: queue.Queue = queue.Queue()
        self.error: str | None = None
        self.alive = True
        try:
            self.ser = serial.Serial(port, baud, timeout=1)
        except Exception as exc:
            self.ser, self.error = None, str(exc)
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.alive and self.ser:
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore")
                item = parse_line(line)
                if item:
                    self.q.put(item)
            except Exception as exc:
                self.error = str(exc)
                break

    def poll(self) -> list[dict]:
        items = []
        while not self.q.empty():
            items.append(self.q.get_nowait())
        return items

    def close(self):
        self.alive = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass


class ReplaySource:
    def __init__(self, df: pd.DataFrame):
        self.df, self.i = df.reset_index(drop=True), 0

    def poll(self) -> list[dict]:
        out = []
        for _ in range(3):
            if self.i < len(self.df):
                r = self.df.iloc[self.i].to_dict()
                r["t"] = time.time()
                r["seq"] = int(r["seq"])
                out.append({"kind": "tlm", "row": r, "raw": format_packet(r)})
                self.i += 1
        return out


# ----------------------------------------------------------------------------
# ANALYTICS
# ----------------------------------------------------------------------------
def prepare_df(rows: list[dict], pipe_km: float) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows[-MAX_ROWS:])
    df["km"] = df["servo"] / 180.0 * pipe_km
    df["soc"] = ((df["vbat"] - 3.3) / 0.9).clip(0, 1) * 100
    df["sun"] = df["vsol"] > 1.0
    df["time"] = (pd.to_datetime(df["t"], unit="s", utc=True)
                  .dt.tz_convert("Asia/Dubai").dt.tz_localize(None))
    df["point_err"] = np.sqrt(df["roll"] ** 2 + df["pitch"] ** 2)
    return df


def scan_profile(df: pd.DataFrame, pipe_km: float, bin_km: float, sweeps: int = 2) -> pd.DataFrame:
    d = df.tail(SWEEP_SAMPLES * sweeps).copy()
    if d.empty:
        return pd.DataFrame(columns=["km", "ir", "delta"])
    d["bin"] = (d["km"] // bin_km) * bin_km + bin_km / 2
    prof = d.groupby("bin", as_index=False)["ir"].mean().rename(columns={"bin": "km"})
    prof["baseline"] = prof["ir"].rolling(15, center=True, min_periods=5).median()
    prof["delta"] = prof["ir"] - prof["baseline"]
    return prof


def detect_events(prof: pd.DataFrame, thr: float) -> pd.DataFrame:
    if prof.empty:
        return pd.DataFrame()
    flag = (prof["delta"] <= -thr).fillna(False).to_numpy()
    events, start = [], None
    for i, f in enumerate(flag):
        if f and start is None:
            start = i
        if (not f or i == len(flag) - 1) and start is not None:
            end = i if f else i - 1
            seg = prof.iloc[start:end + 1]
            worst = seg.loc[seg["delta"].idxmin()]
            depth = -worst["delta"]
            sev = "HIGH" if depth >= 2.5 * thr else "MEDIUM" if depth >= 1.5 * thr else "LOW"
            events.append({"km": round(float(worst["km"]), 1), "from_km": round(float(seg["km"].min()), 1),
                           "to_km": round(float(seg["km"].max()), 1),
                           "delta_C": round(float(worst["delta"]), 2),
                           "confidence_%": int(min(99, 50 + 25 * depth / thr)), "severity": sev})
            start = None
    return pd.DataFrame(events)


def level(value: float, amber: float, red: float, high_bad: bool = True) -> str:
    if high_bad:
        return "bad" if value >= red else "warn" if value >= amber else "ok"
    return "bad" if value <= red else "warn" if value <= amber else "ok"


def pill(text: str, kind: str = "info") -> str:
    return f'<span class="pill {kind}">{text}</span>'


def interp_pos(km: float, pipe_km: float) -> tuple[float, float]:
    f = max(0.0, min(1.0, km / pipe_km))
    return (PIPE_START[0] + f * (PIPE_END[0] - PIPE_START[0]),
            PIPE_START[1] + f * (PIPE_END[1] - PIPE_START[1]))


def lora_airtime(sf: int, bw_khz: float, cr: int, payload: int, preamble: int = 8) -> float:
    tsym = (2 ** sf) / (bw_khz * 1000)
    de = 1 if tsym > 0.016 else 0
    n = 8 + max(math.ceil((8 * payload - 4 * sf + 28 + 16 - 0) / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return ((preamble + 4.25) * tsym + n * tsym) * 1000.0   # ms


# ----------------------------------------------------------------------------
# SESSION STATE + SIDEBAR
# ----------------------------------------------------------------------------
def init_state():
    ss = st.session_state
    ss.setdefault("rows", [])
    ss.setdefault("events", [])
    ss.setdefault("console", deque(maxlen=200))
    ss.setdefault("source", None)
    ss.setdefault("source_key", None)
    ss.setdefault("start", time.time())


def reset_data():
    st.session_state.rows, st.session_state.events = [], []
    st.session_state.console.clear()
    st.session_state.start = time.time()


def build_sidebar() -> dict:
    sb = st.sidebar
    sb.markdown("## 🛰️ DROPSAT")
    sb.caption("Ground Segment Control")
    ctx: dict = {}
    mode = sb.radio("Data source", ["Simulator", "Serial (LoRa ground station)", "CSV replay"])
    ctx["mode"] = mode
    port, baud, up = None, 115200, None
    if mode.startswith("Serial"):
        if HAS_SERIAL:
            ports = [p.device for p in serial.tools.list_ports.comports()] or ["(none found)"]
            port = sb.selectbox("Serial port", ports)
            baud = sb.selectbox("Baud rate", [115200, 57600, 9600], index=0)
        else:
            sb.error("Install pyserial:  pip install pyserial")
    if mode == "CSV replay":
        up = sb.file_uploader("Telemetry CSV", type="csv")
    ctx.update(port=port, baud=baud)

    sb.markdown("---")
    ctx["run"] = sb.toggle("Live stream", value=True)
    ctx["refresh"] = sb.slider("Refresh interval (s)", 0.5, 5.0, 1.0, 0.5)
    ctx["ticks"] = sb.slider("Samples per refresh (sim)", 1, 10, 3)

    sb.markdown("---")
    sb.markdown("#### Mission parameters")
    ctx["pipe_km"] = sb.number_input("Pipeline length shown (km)", 1.0, 1000.0, 185.0, 5.0)
    ctx["thr"] = sb.slider("Leak threshold (°C below baseline)", 0.5, 6.0, 2.0, 0.1)
    ctx["bin_km"] = sb.select_slider("Map resolution (km per bin)", [1, 2, 3, 5, 10], value=2)
    ctx["alt"] = sb.slider("Orbit altitude (km)", 400, 700, 500, 10)

    if mode == "Simulator":
        sb.markdown("---")
        sb.markdown("#### Leak injection (simulator)")
        ctx["leak_on"] = sb.toggle("Leak #1 active", True)
        ctx["leak_km"] = sb.slider("Leak #1 position (km)", 0.0, float(ctx["pipe_km"]), 42.0, 1.0)
        ctx["leak_depth"] = sb.slider("Leak #1 cooling (°C)", 0.5, 8.0, 3.4, 0.1)
        ctx["leak2_on"] = sb.toggle("Leak #2 active", False)
        ctx["leak2_km"] = sb.slider("Leak #2 position (km)", 0.0, float(ctx["pipe_km"]), 131.0, 1.0)

    sb.markdown("---")
    if sb.button("Reset mission data", use_container_width=True):
        reset_data()

    key = (mode, port, baud, getattr(up, "name", None))
    if st.session_state.source_key != key:
        old = st.session_state.source
        if hasattr(old, "close"):
            old.close()
        reset_data()
        if mode == "Simulator":
            src = Simulator(ctx["pipe_km"], ctx["thr"])
        elif mode.startswith("Serial") and HAS_SERIAL and port and port != "(none found)":
            src = SerialSource(port, baud)
        elif mode == "CSV replay" and up is not None:
            src = ReplaySource(pd.read_csv(up))
        else:
            src = None
        st.session_state.source, st.session_state.source_key = src, key

    src = st.session_state.source
    if isinstance(src, Simulator):
        src.pipe_km, src.threshold = ctx["pipe_km"], ctx["thr"]
        src.leaks = []
        if ctx.get("leak_on"):
            src.leaks.append((ctx["leak_km"], ctx["leak_depth"], 2.2))
        if ctx.get("leak2_on"):
            src.leaks.append((ctx["leak2_km"], ctx["leak_depth"] * 0.8, 2.0))
    if isinstance(src, SerialSource) and src.error:
        sb.error(f"Serial error: {src.error}")
    ctx["source"] = src
    return ctx


def ingest(ctx: dict):
    src = st.session_state.source
    if src is None:
        return
    ss = st.session_state
    if isinstance(src, Simulator):
        items = []
        for _ in range(ctx["ticks"]):
            items += src.step()
    else:
        items = src.poll()
    for it in items:
        if it["kind"] == "tlm":
            ss.rows.append(it["row"])
        elif it["kind"] == "alert":
            ss.events.append({"time": datetime.fromtimestamp(it["t"]).strftime("%H:%M:%S"),
                              "type": "ONBOARD LEAK ALERT", "km": it["km"], "delta_C": it["delta"]})
        ss.console.append(it.get("raw", ""))
    if len(ss.rows) > MAX_ROWS:
        ss.rows = ss.rows[-MAX_ROWS:]


# ----------------------------------------------------------------------------
# TABS
# ----------------------------------------------------------------------------
def tab_mission(df, prof, ev, ctx):
    pk = ctx["pipe_km"]
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        lat = np.linspace(PIPE_START[0], PIPE_END[0], 50)
        lon = np.linspace(PIPE_START[1], PIPE_END[1], 50)
        MapTrace = getattr(go, "Scattermap", None) or go.Scattermapbox
        fig.add_trace(MapTrace(lat=lat, lon=lon, mode="lines",
                               line=dict(width=4, color=PALETTE["cyan"]), name=PIPE_NAME))
        for nm, (la, lo) in {"Fujairah": PIPE_START, "Al Ain": PIPE_END}.items():
            fig.add_trace(MapTrace(lat=[la], lon=[lo], mode="markers+text", text=[nm],
                                   textposition="top right", marker=dict(size=11, color=PALETTE["blue"]),
                                   name=nm, showlegend=False))
        if not df.empty:
            la, lo = interp_pos(float(df["km"].iloc[-1]), pk)
            fig.add_trace(MapTrace(lat=[la], lon=[lo], mode="markers",
                                   marker=dict(size=13, color=PALETTE["amber"]), name="Sensor footprint"))
        if not ev.empty:
            pts = [interp_pos(k, pk) for k in ev["km"]]
            fig.add_trace(MapTrace(lat=[p[0] for p in pts], lon=[p[1] for p in pts], mode="markers+text",
                                   text=[f"LEAK km {k:.0f}" for k in ev["km"]], textposition="bottom right",
                                   marker=dict(size=18, color=PALETTE["red"]), name="Suspected leak"))
        layout_key = "map" if hasattr(go, "Scattermap") else "mapbox"
        fig.update_layout(**{layout_key: dict(style="open-street-map", zoom=8.2,
                                              center=dict(lat=24.68, lon=56.03))})
        style_fig(fig, 430, f"Pipeline map  |  {PIPE_NAME}")
        st.plotly_chart(fig, use_container_width=True, key="m_map")
    with c2:
        cur = float(df["ir"].iloc[-1]) if not df.empty else 0.0
        base = float(prof["baseline"].median()) if not prof.empty else cur
        g = go.Figure(go.Indicator(
            mode="gauge+number+delta", value=cur, delta=dict(reference=base, valueformat=".1f"),
            number=dict(suffix=" °C"), title=dict(text="Ground temperature (IR payload)"),
            gauge=dict(axis=dict(range=[20, 70]), bar=dict(color=PALETTE["cyan"]),
                       steps=[dict(range=[20, base - ctx["thr"]], color="#0d3a52"),
                              dict(range=[base - ctx["thr"], 70], color="#1b2450")],
                       threshold=dict(line=dict(color=PALETTE["red"], width=4), value=base - ctx["thr"]))))
        st.plotly_chart(style_fig(g, 280), use_container_width=True, key="m_gauge")
        st.markdown("##### Event feed")
        if st.session_state.events:
            st.dataframe(pd.DataFrame(st.session_state.events[-8:][::-1]), use_container_width=True,
                         hide_index=True)
        else:
            st.info("No onboard alerts yet.")
    if not df.empty:
        d = df.tail(400)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=d["time"], y=d["ir"], name="Ground temp (IR)",
                                 line=dict(color=PALETTE["orange"], width=2)))
        fig.add_trace(go.Scatter(x=d["time"], y=d["amb"], name="Ambient air",
                                 line=dict(color=PALETTE["blue"], width=1.5, dash="dot")))
        st.plotly_chart(style_fig(fig, 300, "Live payload stream"), use_container_width=True, key="m_ts")


def tab_leak(df, prof, ev, ctx):
    st.markdown("#### Leak detection algorithm")
    st.caption("A leak is flagged when a patch of sand is cooler than the local rolling-median baseline "
               f"by at least **{ctx['thr']:.1f} °C**. Wet sand cools by evaporation and higher thermal "
               "inertia, so it stands out against hot dry desert.")
    if prof.empty:
        st.info("Waiting for scan data...")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.22, 0.78], vertical_spacing=0.04)
    fig.add_trace(go.Heatmap(z=[prof["ir"]], x=prof["km"], colorscale="Turbo", showscale=True,
                             colorbar=dict(title="°C", len=0.3, y=0.9)), row=1, col=1)
    fig.add_trace(go.Scatter(x=prof["km"], y=prof["ir"], name="Measured", mode="lines+markers",
                             line=dict(color=PALETTE["orange"])), row=2, col=1)
    fig.add_trace(go.Scatter(x=prof["km"], y=prof["baseline"], name="Baseline (median)",
                             line=dict(color=PALETTE["green"], dash="dash")), row=2, col=1)
    fig.add_trace(go.Scatter(x=prof["km"], y=prof["baseline"] - ctx["thr"], name="Alert threshold",
                             line=dict(color=PALETTE["red"], dash="dot")), row=2, col=1)
    if not ev.empty:
        yy = [float(prof.loc[(prof["km"] - k).abs().idxmin(), "ir"]) for k in ev["km"]]
        fig.add_trace(go.Scatter(x=ev["km"], y=yy, mode="markers+text", name="Leak",
                                 text=[f"km {k:.0f}" for k in ev["km"]], textposition="bottom center",
                                 marker=dict(size=16, color=PALETTE["red"], symbol="x")), row=2, col=1)
    fig.update_xaxes(title_text="Distance along pipeline (km)", row=2, col=1)
    fig.update_yaxes(title_text="°C", row=2, col=1)
    st.plotly_chart(style_fig(fig, 520, "Thermal profile along the pipeline"),
                    use_container_width=True, key="l_prof")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("##### Detected leak candidates")
        if ev.empty:
            st.success("ALL CLEAR: no thermal anomaly beyond threshold.")
        else:
            st.dataframe(ev, use_container_width=True, hide_index=True)
    with c2:
        fig = go.Figure(go.Bar(x=prof["km"], y=prof["delta"],
                               marker_color=np.where(prof["delta"] <= -ctx["thr"], PALETTE["red"],
                                                     PALETTE["blue"])))
        fig.add_hline(y=-ctx["thr"], line_dash="dot", line_color=PALETTE["red"])
        fig.update_yaxes(title_text="Δ vs baseline (°C)")
        st.plotly_chart(style_fig(fig, 280, "Anomaly score"), use_container_width=True, key="l_delta")


def tab_payload(df, ctx):
    if df.empty:
        st.info("Waiting for data...")
        return
    st.markdown("#### Payload: MLX90614 infrared thermometer")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mean ground temp", f"{df['ir'].mean():.2f} °C")
    c2.metric("Std deviation (noise)", f"{df['ir'].std():.2f} °C")
    c3.metric("Min / Max", f"{df['ir'].min():.1f} / {df['ir'].max():.1f} °C")
    c4.metric("Ground minus air", f"{(df['ir'] - df['amb']).mean():+.1f} °C")
    a, b = st.columns(2)
    with a:
        fig = go.Figure(go.Histogram(x=df["ir"], nbinsx=40, marker_color=PALETTE["violet"]))
        fig.update_xaxes(title_text="°C")
        st.plotly_chart(style_fig(fig, 320, "Temperature distribution"), use_container_width=True, key="p_hist")
    with b:
        fig = go.Figure(go.Scatter(x=df["servo"], y=df["ir"], mode="markers",
                                   marker=dict(size=5, color=df["ir"], colorscale="Turbo", showscale=True)))
        fig.update_xaxes(title_text="Servo angle (°)")
        fig.update_yaxes(title_text="IR temp (°C)")
        st.plotly_chart(style_fig(fig, 320, "Servo angle vs temperature"), use_container_width=True, key="p_scatter")
    st.markdown("##### Scale and realism: sensor footprint from orbit")
    st.caption("The MLX90614 is a single-pixel sensor, which is right for a desk prototype. This calculator "
               "shows why a real flight unit would need optics or a thermal imager.")
    f1, f2 = st.columns(2)
    fov = f1.slider("Sensor field of view (°)", 5, 90, 35, key="fov")
    res = f2.slider("Thermal imager pixels across (flight concept)", 32, 640, 160, key="res")
    foot = 2 * ctx["alt"] * math.tan(math.radians(fov / 2))
    narrow = 2 * ctx["alt"] * math.tan(math.radians(1.0 / 2))
    k1, k2, k3 = st.columns(3)
    k1.metric("Footprint (single pixel, this FOV)", f"{foot:.0f} km")
    k2.metric("Footprint with 1° optics", f"{narrow:.1f} km")
    k3.metric("Ground sample size (1° optics / pixels)", f"{narrow * 1000 / res:.0f} m")
    st.info("Honest design note: the desktop demo scales the satellite pass down to a sand tray. A flight "
            "version would use a narrow-FOV thermal imager so each pixel covers tens of metres.")


def tab_health(df, ctx):
    if df.empty:
        st.info("Waiting for data...")
        return
    last = df.iloc[-1]
    subs = {
        "EPS (Power)": (f"{last['vbat']:.2f} V  |  {last['soc']:.0f}%", level(last["soc"], 40, 20, False)),
        "OBC (ESP32)": (f"Mode: {last['mode']}", "bad" if last["mode"] == "SAFE" else
                        "warn" if last["mode"] == "LEAK_ALERT" else "ok"),
        "COMMS (LoRa)": (f"{last['rssi']:.0f} dBm  |  {last['snr']:.1f} dB", level(last["rssi"], -105, -115, False)),
        "ADCS": (f"Point err {last['point_err']:.1f}°", level(last["point_err"], 8, 15)),
        "THERMAL": (f"{last['tint']:.1f} °C  |  {last['hum']:.0f}% RH", level(last["tint"], 45, 60)),
        "PAYLOAD": (f"{last['ir']:.1f} °C ground", "ok"),
    }
    cols = st.columns(6)
    for col, (name, (val, lv)) in zip(cols, subs.items()):
        col.markdown(f'<div class="card"><h4>{name}</h4><div class="big" style="font-size:1.0rem">{val}</div>'
                     f'<div class="sub">{pill({"ok": "NOMINAL", "warn": "CAUTION", "bad": "ALARM"}[lv], lv)}</div></div>',
                     unsafe_allow_html=True)
    d = df.tail(500)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("Battery voltage and solar input (V)", "Bus current (mA)",
                                        "Internal temperature (°C) and humidity (%RH)"))
    fig.add_trace(go.Scatter(x=d["time"], y=d["vbat"], name="Battery V", line=dict(color=PALETTE["green"])), 1, 1)
    fig.add_trace(go.Scatter(x=d["time"], y=d["vsol"], name="Solar V", line=dict(color=PALETTE["amber"])), 1, 1)
    fig.add_trace(go.Scatter(x=d["time"], y=d["ima"], name="Current", line=dict(color=PALETTE["red"])), 2, 1)
    fig.add_trace(go.Scatter(x=d["time"], y=d["tint"], name="Internal T", line=dict(color=PALETTE["orange"])), 3, 1)
    fig.add_trace(go.Scatter(x=d["time"], y=d["hum"], name="Humidity", line=dict(color=PALETTE["cyan"])), 3, 1)
    st.plotly_chart(style_fig(fig, 640), use_container_width=True, key="h_ts")
    a, b = st.columns(2)
    with a:
        g = go.Figure(go.Indicator(mode="gauge+number", value=float(last["soc"]),
                                   number=dict(suffix=" %"), title=dict(text="Battery state of charge"),
                                   gauge=dict(axis=dict(range=[0, 100]), bar=dict(color=PALETTE["green"]),
                                              steps=[dict(range=[0, 20], color="#4a1010"),
                                                     dict(range=[20, 40], color="#4a3606")])))
        st.plotly_chart(style_fig(g, 260), use_container_width=True, key="h_soc")
    with b:
        fig = go.Figure(go.Scatter(x=d["time"], y=d["pres"], line=dict(color=PALETTE["violet"])))
        fig.update_yaxes(title_text="hPa")
        st.plotly_chart(style_fig(fig, 260, "Cabin pressure (BMP180)"), use_container_width=True, key="h_pres")


def rot(roll, pitch, yaw):
    r, p, y = map(math.radians, (roll, pitch, yaw))
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def tab_adcs(df, ctx):
    if df.empty:
        st.info("Waiting for data...")
        return
    last = df.iloc[-1]
    V = np.array([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)]) @ \
        rot(last["roll"], last["pitch"], 0).T
    tris = [(0, 2, 6), (0, 6, 4), (1, 3, 7), (1, 7, 5), (0, 1, 3), (0, 3, 2),
            (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6)]
    i, j, k = zip(*tris)
    fig = go.Figure()
    fig.add_trace(go.Mesh3d(x=V[:, 0], y=V[:, 1], z=V[:, 2], i=i, j=j, k=k, color="#4f8cff",
                            opacity=0.55, flatshading=True, name="1U CubeSat"))
    fig.add_trace(go.Mesh3d(x=V[:, 0], y=V[:, 1], z=V[:, 2], i=[0, 0], j=[2, 6], k=[6, 4],
                            color=PALETTE["amber"], opacity=1, name="Payload window (nadir face)"))
    c = V.mean(axis=0)
    nadir = c + (V[[0, 2, 4, 6]].mean(axis=0) - c) * 3
    fig.add_trace(go.Scatter3d(x=[c[0], nadir[0]], y=[c[1], nadir[1]], z=[c[2], nadir[2]], mode="lines",
                               line=dict(color=PALETTE["red"], width=6), name="Payload boresight"))
    fig.add_trace(go.Scatter3d(x=[0, 0], y=[0, 0], z=[0, -2], mode="lines",
                               line=dict(color=PALETTE["green"], width=4, dash="dash"), name="True nadir"))
    fig.update_layout(scene=dict(aspectmode="cube", xaxis=dict(range=[-2, 2]), yaxis=dict(range=[-2, 2]),
                                 zaxis=dict(range=[-2.2, 1.5]), bgcolor="rgba(0,0,0,0)"))
    a, b = st.columns([2, 3])
    with a:
        st.plotly_chart(style_fig(fig, 460, "Live attitude (MPU6050)"), use_container_width=True, key="a_3d")
        st.metric("Nadir pointing error", f"{last['point_err']:.1f}°",
                  help="sqrt(roll² + pitch²). Under 10° keeps the payload window on target.")
    with b:
        d = df.tail(400)
        fig = go.Figure()
        for col, clr in (("roll", PALETTE["cyan"]), ("pitch", PALETTE["amber"])):
            fig.add_trace(go.Scatter(x=d["time"], y=d[col], name=col.title(), line=dict(color=clr)))
        fig.add_hline(y=10, line_dash="dot", line_color=PALETTE["red"])
        fig.add_hline(y=-10, line_dash="dot", line_color=PALETTE["red"])
        st.plotly_chart(style_fig(fig, 220, "Roll and pitch (°)"), use_container_width=True, key="a_rp")
        fig = go.Figure(go.Scatter(x=d["time"], y=d["lux"], fill="tozeroy", line=dict(color=PALETTE["amber"])))
        fig.update_yaxes(title_text="lux")
        st.plotly_chart(style_fig(fig, 220, "Coarse sun sensor (APDS9960)"), use_container_width=True, key="a_lux")


def tab_comms(df, ctx):
    st.markdown("#### LoRa downlink (868 MHz) and link budget")
    st.caption("Verify that 868 MHz is permitted for your use in the UAE before ordering modules.")
    if not df.empty:
        n_exp = int(df["seq"].max() - df["seq"].min() + 1)
        n_rx = df["seq"].nunique()
        loss = 100 * (1 - n_rx / max(n_exp, 1))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Packets received", n_rx)
        c2.metric("Packet loss", f"{loss:.1f}%")
        c3.metric("Mean RSSI", f"{df['rssi'].mean():.1f} dBm")
        c4.metric("Mean SNR", f"{df['snr'].mean():.1f} dB")
        d = df.tail(400)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=d["time"], y=d["rssi"], name="RSSI (dBm)", line=dict(color=PALETTE["cyan"])))
        fig.add_trace(go.Scatter(x=d["time"], y=d["snr"], name="SNR (dB)", line=dict(color=PALETTE["violet"])),
                      secondary_y=True)
        st.plotly_chart(style_fig(fig, 280, "Link quality"), use_container_width=True, key="c_q")
    st.markdown("##### Link budget calculator (flight concept)")
    a, b, c = st.columns(3)
    sf = a.select_slider("Spreading factor", [7, 8, 9, 10, 11, 12], value=10)
    bw = b.select_slider("Bandwidth (kHz)", [62.5, 125.0, 250.0, 500.0], value=125.0)
    cr = c.select_slider("Coding rate (4/(4+n))", [1, 2, 3, 4], value=1)
    a, b, c = st.columns(3)
    ptx = a.slider("TX power (dBm)", 2, 20, 17)
    gt = b.slider("Satellite antenna gain (dBi)", -3.0, 6.0, 0.0)
    gr = c.slider("Ground antenna gain (dBi)", 0.0, 15.0, 5.0)
    a, b, c = st.columns(3)
    freq = a.number_input("Frequency (MHz)", 433.0, 928.0, 868.0)
    elev = b.slider("Minimum elevation (°)", 5, 60, 20)
    pay = c.slider("Packet payload (bytes)", 10, 100, 40)
    h = ctx["alt"]
    slant = math.sqrt((R_EARTH + h) ** 2 - (R_EARTH * math.cos(math.radians(elev))) ** 2) \
        - R_EARTH * math.sin(math.radians(elev))
    fspl = 20 * math.log10(slant) + 20 * math.log10(freq) + 32.44
    snr_req = {7: -7.5, 8: -10, 9: -12.5, 10: -15, 11: -17.5, 12: -20}[sf]
    sens = -174 + 10 * math.log10(bw * 1000) + 6 + snr_req
    rx = ptx + gt + gr - fspl - 3.0
    margin = rx - sens
    v = math.sqrt(MU_EARTH / (R_EARTH + h))
    dop = freq * 1e6 * v / C_LIGHT / 1000.0
    air = lora_airtime(sf, bw, cr, pay)
    bitrate = sf * (bw * 1000 / 2 ** sf) * (4 / (4 + cr))
    k = st.columns(5)
    k[0].metric("Slant range", f"{slant:.0f} km")
    k[1].metric("Free-space loss", f"{fspl:.1f} dB")
    k[2].metric("Link margin", f"{margin:.1f} dB", delta="CLOSES" if margin > 6 else "MARGINAL" if margin > 0 else "FAILS",
                delta_color="normal" if margin > 6 else "off" if margin > 0 else "inverse")
    k[3].metric("Max Doppler", f"±{dop:.1f} kHz")
    k[4].metric("Packet airtime", f"{air:.0f} ms", help=f"Raw bitrate ≈ {bitrate:.0f} bit/s")
    st.caption(f"Receiver sensitivity ≈ {sens:.1f} dBm; received power ≈ {rx:.1f} dBm. A leak alert is only "
               f"~{pay} bytes, which is why LoRa fits (low rate, long range).")


def tab_budgets(ctx):
    st.markdown("#### Power, mass and cost budgets (editable: replace with your measured values)")
    pw = pd.DataFrame([
        ["ESP32 (OBC)", "OBC", 3.3, 80, 100], ["LoRa module (TX/RX avg)", "COMMS", 3.3, 25, 100],
        ["SG90 servo (moving)", "ADCS/Payload", 5.0, 150, 40], ["MLX90614", "Payload", 3.3, 2, 100],
        ["MPU6050", "ADCS", 3.3, 4, 100], ["APDS9960", "ADCS", 3.3, 1, 100],
        ["BMP180 + Temp/Hum board", "Thermal", 3.3, 2, 100], ["OLED display", "Display", 3.3, 20, 100],
        ["Status LED", "Display", 3.3, 10, 20]],
        columns=["Component", "Subsystem", "Voltage (V)", "Current (mA)", "Duty (%)"])
    ms = pd.DataFrame([
        ["Structure (PLA frame + panels)", 60], ["ESP32 + OLED + LED", 18], ["LoRa module + antenna", 8],
        ["MLX90614 + SG90 servo", 14], ["MPU6050 + APDS9960 + BMP180 + Temp/Hum", 8],
        ["18650 cell", 46], ["TP4056 + regulator", 8], ["Solar cells (x4)", 60], ["Wiring and fasteners", 20]],
        columns=["Item", "Mass (g)"])
    cs = pd.DataFrame([
        ["MYOSA kit (ESP32, OLED, LED, IMU, light, baro, temp/hum)", 0], ["MLX90614 IR thermometer", 60],
        ["SG90 servo", 12], ["2x LoRa modules (868 MHz)", 70], ["Second ESP32 (ground station)", 35],
        ["Sand tray, tube, syringe", 20], ["Solar cells x4 (est.)", 32], ["18650 + holder (est.)", 15],
        ["TP4056 + regulator (est.)", 12], ["Structure print/panels (est.)", 10]],
        columns=["Item", "Cost (AED)"])
    t1, t2, t3 = st.tabs(["⚡ Power", "⚖️ Mass", "💰 Cost"])
    with t1:
        pw = st.data_editor(pw, num_rows="dynamic", use_container_width=True, key="ed_pw")
        pw["Power (mW)"] = pw["Voltage (V)"] * pw["Current (mA)"] * pw["Duty (%)"] / 100
        avg = pw["Power (mW)"].sum() / 0.85 / 1000   # 85% regulator efficiency
        a, b, c = st.columns(3)
        cells = a.number_input("Solar cells", 1, 8, 4)
        cell_w = b.number_input("Power per cell (W)", 0.1, 3.0, 0.3)
        sunf = c.slider("Sunlit fraction of orbit", 0.4, 0.8, 0.62)
        gen = cells * cell_w * sunf
        orbit_h = 90 / 60
        net = (gen - avg) * orbit_h
        k = st.columns(4)
        k[0].metric("Average load", f"{avg:.2f} W")
        k[1].metric("Orbit-average generation", f"{gen:.2f} W")
        k[2].metric("Energy margin / orbit", f"{net:.2f} Wh")
        k[3].metric("Verdict", "POSITIVE" if net > 0 else "DEFICIT")
        fig = go.Figure(go.Bar(x=pw["Component"], y=pw["Power (mW)"], marker_color=PALETTE["cyan"]))
        st.plotly_chart(style_fig(fig, 300, "Average power by component (mW)"),
                        use_container_width=True, key="b_pw")
    with t2:
        ms = st.data_editor(ms, num_rows="dynamic", use_container_width=True, key="ed_ms")
        tot = ms["Mass (g)"].sum()
        st.metric("Total mass", f"{tot:.0f} g", delta=f"{1330 - tot:.0f} g margin vs 1U limit (1330 g)")
        st.progress(min(1.0, tot / 1330))
    with t3:
        cs = st.data_editor(cs, num_rows="dynamic", use_container_width=True, key="ed_cs")
        tot = cs["Cost (AED)"].sum()
        st.metric("Total hardware cost", f"AED {tot:.0f}", delta=f"AED {300 - tot:.0f} left of 300",
                  delta_color="normal" if tot <= 300 else "inverse")
        st.progress(min(1.0, tot / 300))
        fig = go.Figure(go.Pie(labels=cs["Item"], values=cs["Cost (AED)"], hole=0.55))
        st.plotly_chart(style_fig(fig, 340, "Cost breakdown"), use_container_width=True, key="b_cs")


def tab_conops():
    st.markdown("#### Concept of operations (one 90-minute LEO orbit)")
    modes = pd.DataFrame({"Mode": ["Sun pointing (charge)", "Nadir / payload scan", "Ground station downlink",
                                   "Safe / eclipse"], "Minutes": [30, 20, 20, 20]})
    comp = pd.DataFrame({
        "Component": ["ESP32 OBC", "MLX90614 payload", "SG90 servo", "LoRa radio", "MPU6050 / APDS9960",
                      "BMP180 / Temp-Hum", "OLED / LED"],
        "Sun pointing": ["ON", "OFF", "OFF", "OFF", "ON", "ON", "OFF"],
        "Nadir scan": ["ON", "ON", "ON", "OFF", "ON", "ON", "ON"],
        "Downlink": ["ON", "OFF", "OFF", "ON", "ON", "ON", "ON"],
        "Safe / eclipse": ["ON (low power)", "OFF", "OFF", "OFF", "ON", "ON", "OFF"]})
    a, b = st.columns([2, 3])
    with a:
        fig = go.Figure(go.Pie(labels=modes["Mode"], values=modes["Minutes"], hole=0.55,
                               marker=dict(colors=[PALETTE["amber"], PALETTE["cyan"], PALETTE["violet"],
                                                   PALETTE["muted"]])))
        st.plotly_chart(style_fig(fig, 340, "Time per mode"), use_container_width=True, key="o_pie")
    with b:
        st.dataframe(comp, use_container_width=True, hide_index=True)
    st.caption("Mode durations match the MADAR CONOPS inputs (30 / 20 / 20 / 20 min = 90 min orbit). "
               "Components that are off in eclipse save battery energy.")


def tab_logs(df, ctx):
    st.markdown("#### Data logging and export")
    if df.empty:
        st.info("No data yet.")
        return
    c1, c2 = st.columns([1, 1])
    c1.download_button("⬇️ Download telemetry (CSV)", df.drop(columns=["time"]).to_csv(index=False),
                       file_name=f"dropsat_telemetry_{datetime.now():%Y%m%d_%H%M%S}.csv", mime="text/csv",
                       use_container_width=True)
    c2.download_button("⬇️ Download leak events (CSV)", pd.DataFrame(st.session_state.events).to_csv(index=False),
                       file_name="dropsat_events.csv", mime="text/csv", use_container_width=True)
    st.dataframe(df.tail(200).iloc[::-1], use_container_width=True, hide_index=True, height=300)
    st.markdown("##### Raw ground-station console")
    st.code("\n".join(list(st.session_state.console)[-15:]) or "(empty)", language="text")


def tab_rubric():
    st.markdown("#### How this dashboard maps to the judging rubric")
    st.dataframe(pd.DataFrame([
        ["Dashboard Functionality & Organization (5)", "Live, organised tabs fed by the prototype over serial/LoRa", "All tabs"],
        ["Data Interpretation & Mission Relevance (5)", "Baseline, threshold, Δ-score, confidence, severity", "Leak Detection, Payload"],
        ["Mission Clarity & Objectives (5)", "Measurable objective: flag ≥ threshold °C cool spots with km position", "Mission Control"],
        ["Payload Selection & Justification (5)", "IR payload analytics and footprint trade-off", "Payload"],
        ["Subsystem Understanding (5)", "EPS, OBC, COMMS, ADCS, thermal status cards", "Spacecraft Health"],
        ["Component Selection & Integration (5)", "Power/mass/cost budgets from real parts", "Budgets"],
        ["Testing & Technical Reliability (5)", "Packet loss, noise, CSV logs as test evidence", "Comms, Data & Logs"],
        ["Creativity bonus (up to +5)", "Link budget, Doppler, 3D attitude, footprint calculator", "Comms, ADCS"],
    ], columns=["Rubric criterion", "Evidence in dashboard", "Where"]), use_container_width=True, hide_index=True)
    st.caption("Tip: for the Qualifier, switch to Serial mode and show live data from the real hardware.")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def render(ctx: dict):
    if ctx["run"]:
        ingest(ctx)
    df = prepare_df(st.session_state.rows, ctx["pipe_km"])
    prof = scan_profile(df, ctx["pipe_km"], ctx["bin_km"]) if not df.empty else pd.DataFrame()
    ev = detect_events(prof, ctx["thr"]) if not prof.empty else pd.DataFrame()

    online = (not df.empty) and (time.time() - float(df["t"].iloc[-1]) < 10)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    mode_txt = df["mode"].iloc[-1] if not df.empty else "NO DATA"
    elapsed = int(df["t"].iloc[-1] - df["t"].iloc[0]) if len(df) > 1 else 0
    st.markdown(
        f'<div class="hero"><h1>🛰️ {APP_TITLE}</h1><p>{MISSION_TAG}</p><div style="margin-top:10px">'
        f'{pill("LINK ONLINE" if online else "LINK IDLE", "ok" if online else "warn")}'
        f'{pill("MODE: " + str(mode_txt), "bad" if mode_txt == "LEAK_ALERT" else "info")}'
        f'{pill("SOURCE: " + ctx["mode"].split(" (")[0].upper(), "info")}'
        f'{pill(now, "info")}{pill(f"MET {elapsed // 60:02d}:{elapsed % 60:02d}", "info")}</div></div>',
        unsafe_allow_html=True)

    if not ev.empty:
        w = ev.loc[ev["delta_C"].idxmin()]
        st.markdown(f'<div class="leakbanner"><b>🚨 LEAK DETECTED</b>: {len(ev)} suspected leak(s). '
                    f'Strongest at <b>km {w["km"]:.0f}</b> ({w["delta_C"]:+.1f} °C, {w["severity"]}, '
                    f'confidence {w["confidence_%"]}%). Dispatch inspection crew.</div>', unsafe_allow_html=True)
    elif not df.empty:
        st.markdown('<div class="okbanner"><b>✅ ALL CLEAR</b>: no thermal anomalies on the monitored route.</div>',
                    unsafe_allow_html=True)

    if not df.empty:
        last = df.iloc[-1]
        k = st.columns(7)
        k[0].metric("Ground temp", f"{last['ir']:.1f} °C")
        k[1].metric("Scan position", f"km {last['km']:.0f}")
        k[2].metric("Active leaks", len(ev))
        k[3].metric("Battery", f"{last['soc']:.0f} %")
        k[4].metric("Bus current", f"{last['ima']:.0f} mA")
        k[5].metric("RSSI", f"{last['rssi']:.0f} dBm")
        k[6].metric("Packets", len(df))
    else:
        st.info("Waiting for telemetry. Choose a data source in the sidebar.")

    tabs = st.tabs(["🌍 Mission Control", "🔥 Leak Detection", "📡 Payload", "🩺 Spacecraft Health",
                    "🧭 ADCS", "📶 Comms", "⚙️ Budgets", "🗓️ CONOPS", "🗂️ Data & Logs", "🏆 Rubric Map"])
    with tabs[0]:
        tab_mission(df, prof, ev, ctx)
    with tabs[1]:
        tab_leak(df, prof, ev, ctx)
    with tabs[2]:
        tab_payload(df, ctx)
    with tabs[3]:
        tab_health(df, ctx)
    with tabs[4]:
        tab_adcs(df, ctx)
    with tabs[5]:
        tab_comms(df, ctx)
    with tabs[6]:
        tab_budgets(ctx)
    with tabs[7]:
        tab_conops()
    with tabs[8]:
        tab_logs(df, ctx)
    with tabs[9]:
        tab_rubric()


def main():
    init_state()
    ctx = build_sidebar()
    frag = getattr(st, "fragment", None) or getattr(st, "experimental_fragment")
    run_every = f"{ctx['refresh']}s" if ctx["run"] else None
    frag(run_every=run_every)(lambda: render(ctx))()


main()