import os
import json
import hashlib
import colorsys
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import parser

import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

# Constants
API_KEY = os.environ["API_KEY_MIVB"]
API_URL = (
    "https://data.stib-mivb.be/api/explore/v2.1/catalog/"
    "datasets/waiting-time-rt-production/records"
)
CSV_PATH = "data/gtfs-stops-production.csv"
REFRESH_SECONDS = 30  # seconds between data fetches
TABLE_REFRESH_INTERVAL = 1000  # ms for Streamlit widget reruns


def get_current_time_str() -> str:
    """
    Return the current time in Europe/Brussels as HH:MM:SS.
    """
    now = datetime.now(ZoneInfo("Europe/Brussels"))
    return now.strftime("%H:%M:%S")


@st.cache_data
def load_stops() -> dict[str, dict[str, list]]:
    """
    Load stops from CSV and return a mapping:
    { stop_name: { "IDs": [...], "Coordinates": [...] } }
    """
    df = pd.read_csv(CSV_PATH, sep=";")
    df = df[["ID", "Name", "Coordinates"]].dropna()
    df = df[df["ID"].astype(str).str.isnumeric()]
    df["Coordinates"] = df["Coordinates"].apply(
        lambda x: tuple(map(float, x.split(",")))
    )

    stop_dict: dict[str, dict[str, list]] = {}
    for _, row in df.iterrows():
        name = row["Name"]
        stop_id = str(row["ID"])
        coords = row["Coordinates"]
        stop_dict.setdefault(name, {"IDs": [], "Coordinates": []})
        stop_dict[name]["IDs"].append(stop_id)
        stop_dict[name]["Coordinates"].append(coords)
    return stop_dict


def fetch_data(pointids: list[str]) -> list[dict]:
    """
    Fetch real-time arrival data for the given point IDs.
    """
    where_clause = "pointid IN (" + ",".join(f'"{pid}"' for pid in pointids) + ")"
    params = {"apikey": API_KEY, "where": where_clause, "limit": 100}
    try:
        resp = requests.get(API_URL, params=params)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        st.error(f"API error: {e}")
        return []


def line_color_soft(line_id: str) -> str:
    """
    Generate a consistent dark color per line ID for styling.
    """
    digest = hashlib.md5(line_id.encode()).hexdigest()
    seed = int(digest[:8], 16)
    h = (seed % 360) / 360.0
    s, v = 0.75, 0.40
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


# --- Streamlit App Setup ---
st.set_page_config(page_title="🚊 STIB Real-Time Tram/Bus Arrivals", layout="wide")
st_autorefresh(interval=TABLE_REFRESH_INTERVAL, key="table_refresh")

# Load stops once
stop_dict = load_stops()

# Sidebar: Time & Stop Controls
with st.sidebar:
    st.title(f"**Current time:** {get_current_time_str()}")
    st.caption(f"Arrival data refreshes every {REFRESH_SECONDS} seconds.")
    st.title("Controls")

    selected_stops = st.multiselect(
        "Choose stops",
        list(stop_dict.keys()),
        default=["LEVURE", "GERMOIR", "FLAGEY", "WERY"],
    )
    time_limit_minutes = st.slider(
        "Only show arrivals within the next X minutes",
        min_value=1,
        max_value=60,
        value=15,
    )

    if not selected_stops:
        st.warning("Select at least one stop.")
        st.stop()

# Compute point IDs
pointids = [pid for stop in selected_stops for pid in stop_dict[stop]["IDs"]]

# Initialize session state
if "raw_results" not in st.session_state:
    st.session_state.raw_results = []
    st.session_state.last_fetch_time = None

# Auto-refresh data if stale
now = datetime.now(ZoneInfo("Europe/Brussels"))
if (
    st.session_state.last_fetch_time is None
    or (now - st.session_state.last_fetch_time).total_seconds()
    > REFRESH_SECONDS
):
    new_data = fetch_data(pointids)
    if set(map(json.dumps, new_data)) != set(
        map(json.dumps, st.session_state.raw_results)
    ):
        st.session_state.raw_results = new_data
        st.session_state.last_fetch_time = now

# Process fetched data into grouped by stop
grouped: dict[str, list[dict]] = {}
max_seconds = time_limit_minutes * 60
for record in st.session_state.raw_results:
    pid = record.get("pointid")
    line = record.get("lineid")
    try:
        times = json.loads(record.get("passingtimes", "[]"))
    except json.JSONDecodeError:
        continue

    for pt in times:
        iso = pt.get("expectedArrivalTime")
        if not iso:
            continue
        try:
            arrival = (
                parser.isoparse(iso)
                .astimezone(ZoneInfo("Europe/Brussels"))
            )
            wait = (arrival - now).total_seconds()
        except Exception:
            continue

        if not (0 < wait <= max_seconds):
            continue

        stop_name = next(
            (n for n, d in stop_dict.items() if pid in d["IDs"]), pid
        )
        grouped.setdefault(stop_name, []).append(
            {
                "Line": line,
                "Destination": pt.get("destination", {}).get("fr", "Unknown"),
                "Expected Arrival": arrival.strftime("%H:%M:%S"),
                "Time Left": f"{int(wait//60)}m {int(wait%60)}s",
                "Seconds Left": wait,
            }
        )

# Sidebar: Line filter + buttons at bottom
with st.sidebar:
    all_lines = sorted(
        {item["Line"] for arr in grouped.values() for item in arr}
    )

    # On first real data load, select all by default once
    if not st.session_state.get("lines_initialized", False) and all_lines:
        st.session_state.selected_lines = all_lines.copy()
        st.session_state.lines_initialized = True
    else:
        # Filter out any no-longer-available lines
        st.session_state.selected_lines = [
            l
            for l in st.session_state.get("selected_lines", [])
            if l in all_lines
        ]

    # Multiselect with default = whatever is in session_state
    selected_lines = st.multiselect(
        "Filter by line",
        all_lines,
        default=st.session_state.selected_lines,
    )
    st.session_state.selected_lines = selected_lines

    st.markdown("---")
    col1, col2 = st.columns(2)
    if col1.button("🔁 Refresh Now"):
        st.session_state.raw_results = fetch_data(pointids)
        st.session_state.last_fetch_time = datetime.now(
            ZoneInfo("Europe/Brussels")
        )
    if col2.button("Select All Lines"):
        st.session_state.selected_lines = all_lines.copy()

# Main: display arrival tables
st.title("🚌 Upcoming Arrivals by Stop")
for i in range(0, len(selected_stops), 2):
    c1, c2 = st.columns(2)
    for offset, col in enumerate((c1, c2)):
        idx = i + offset
        if idx >= len(selected_stops):
            continue
        stop_name = selected_stops[idx]
        arrivals = grouped.get(stop_name, [])
        filtered = [
            a
            for a in arrivals
            if a["Line"] in st.session_state.selected_lines
        ]

        with col:
            st.subheader(f"🛑 {stop_name}")
            if filtered:
                df = (
                    pd.DataFrame(filtered)
                    .sort_values("Seconds Left")
                    .loc[
                        :, 
                        [
                            "Line",
                            "Destination",
                            "Expected Arrival",
                            "Time Left",
                        ]
                    ]
                )
                styled = df.style.applymap(
                    lambda v: (
                        f"color: white; background-color: "
                        f"{line_color_soft(str(v))}"
                    ),
                    subset=["Line"],
                )
                st.dataframe(styled, use_container_width=True)
            else:
                st.info("No arrivals in selected time range or line.")

# Map at bottom with Plotly
if st.session_state.get("last_stops") != selected_stops:
    st.session_state.last_stops = selected_stops
    rows: list[dict] = []
    for stop in selected_stops:
        for lat, lon in stop_dict[stop]["Coordinates"]:
            rows.append({"lat": lat, "lon": lon, "stop_name": stop})

    df_map = pd.DataFrame(rows)
    zoom = 15 if df_map["lat"].max() - df_map["lat"].min() < 0.05 else 12
    fig = px.scatter_mapbox(
        df_map,
        lat="lat",
        lon="lon",
        hover_name="stop_name",
        text="stop_name",
        zoom=zoom,
        height=600,
    )
    fig.update_layout(
        mapbox_style="carto-darkmatter",
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
    )
    fig.update_traces(marker=dict(size=20, color="red"))
    st.session_state.map_chart = fig

st.markdown("## 🗺️ Stop Locations Map")
if "map_chart" in st.session_state:
    st.plotly_chart(st.session_state.map_chart, use_container_width=True)
