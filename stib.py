import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime
from dateutil import parser
from streamlit_autorefresh import st_autorefresh
import hashlib
import colorsys
import os
from zoneinfo import ZoneInfo  # For timezone handling
import plotly.express as px  # <-- Plotly import

# Constants
API_KEY = os.environ['API_KEY_MIVB']
API_URL = "https://data.stib-mivb.be/api/explore/v2.1/catalog/datasets/waiting-time-rt-production/records"
CSV_PATH = "data/gtfs-stops-production.csv"
REFRESH_SECONDS = 30
TABLE_REFRESH_INTERVAL = 1000

# Streamlit setup
st.set_page_config(page_title="STIB Real-Time Arrivals", layout="wide")
st.title("🚊 STIB Real-Time Tram/Bus Arrivals")
st.caption(f"Arrival data refreshes every {REFRESH_SECONDS} seconds.")
st_autorefresh(interval=TABLE_REFRESH_INTERVAL, key="table_refresh")

# Inject CSS for tighter table columns and minimal padding, plus nowrap
st.markdown(
    """
    <style>
    div.stDataFrame > div > div > div > table {
        table-layout: auto !important;
    }
    div.stDataFrame td, div.stDataFrame th {
        padding: 4px 8px !important;
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Dark, distinct color function for line IDs
def line_color_soft(line_id):
    """
    Generate a consistent, dark color for each line_id.
    Darker saturation and lower brightness for readability.
    """
    hash_digest = hashlib.md5(line_id.encode()).hexdigest()
    seed = int(hash_digest[:8], 16)

    h = (seed % 360) / 360.0  # hue
    s = 0.75  # higher saturation for rich color
    v = 0.40  # lower brightness for dark colors
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return '#{0:02x}{1:02x}{2:02x}'.format(int(r * 255), int(g * 255), int(b * 255))

st.markdown("## ⏱️ Select Stops & Arrival Time Window")

# Load stops CSV
@st.cache_data
def load_stops():
    df = pd.read_csv(CSV_PATH, sep=';')
    df = df[['ID', 'Name', 'Coordinates']].dropna()
    df = df[df['ID'].astype(str).str.isnumeric()]
    df['Coordinates'] = df['Coordinates'].apply(lambda x: tuple(map(float, x.split(','))))

    stop_dict = {}
    for _, row in df.iterrows():
        name = row['Name']
        stop_id = str(row['ID'])
        coords = row['Coordinates']
        if name not in stop_dict:
            stop_dict[name] = {'IDs': [], 'Coordinates': []}
        stop_dict[name]['IDs'].append(stop_id)
        stop_dict[name]['Coordinates'].append(coords)
    return stop_dict

stop_dict = load_stops()

# UI Elements
cola, colb = st.columns([2, 1])
selected_stops = cola.multiselect("Choose stops", list(stop_dict.keys()), default=["FLAGEY", "LEVURE", "GERMOIR", "WERY"])
time_limit_minutes = colb.slider("Only show arrivals within the next X minutes", min_value=1, max_value=30, value=5)

if not selected_stops:
    st.warning("Select at least one stop.")
    st.stop()

# Prepare API call
pointids = [pid for stop in selected_stops for pid in stop_dict[stop]['IDs']]
where_clause = "pointid IN (" + ",".join([f'"{pid}"' for pid in pointids]) + ")"
params = {"apikey": API_KEY, "where": where_clause, "limit": 100}

# Fetch API data
def fetch_data():
    try:
        response = requests.get(API_URL, params=params)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        st.error(f"API error: {e}")
        return []

# Session state for data
if "raw_results" not in st.session_state:
    st.session_state.raw_results = []
    st.session_state.last_fetch_time = None

if st.button("🔁 Refresh Now"):
    st.session_state.raw_results = fetch_data()
    st.session_state.last_fetch_time = datetime.now(ZoneInfo("Europe/Brussels"))

# Auto refresh API data every 30 seconds
now = datetime.now(ZoneInfo("Europe/Brussels"))

if (st.session_state.last_fetch_time is None or
    (now - st.session_state.last_fetch_time).total_seconds() > REFRESH_SECONDS):
    new_data = fetch_data()
    if set(map(json.dumps, new_data)) != set(map(json.dumps, st.session_state.raw_results)):
        st.session_state.raw_results = new_data
        st.session_state.last_fetch_time = now

# Display tables in two-column rows
st.markdown("## 🚌 Upcoming Arrivals by Stop")

# Process API response
grouped = {}
max_seconds = time_limit_minutes * 60

for record in st.session_state.raw_results:
    pointid = record["pointid"]
    line = record["lineid"]
    try:
        times = json.loads(record["passingtimes"])
    except:
        continue

    for pt in times:
        try:
            arrival = parser.isoparse(pt["expectedArrivalTime"])
            arrival_brussels = arrival.astimezone(ZoneInfo("Europe/Brussels"))
            wait = (arrival_brussels - now).total_seconds()
            if wait > max_seconds or wait <= 0:
                continue  # Filter out arrivals beyond time window or past

            wait_display = f"{int(wait // 60)}m {int(wait % 60)}s"
            destination = pt.get("destination", {}).get("fr", "Unknown")
            stop_name = next((name for name, data in stop_dict.items() if pointid in data['IDs']), pointid)

            if wait <= 0 and wait >= -30:
                wait_display = "⬇⬇"
            elif wait > max_seconds or wait <= -30:
                continue
            else:
                wait_display = f"{int(wait // 60)}m {int(wait % 60)}s"

            grouped.setdefault(stop_name, []).append({
                "Line": line,
                "Destination": destination,
                "Expected Arrival": arrival_brussels.strftime("%H:%M:%S"),
                "Time Left": wait_display,
                "Seconds Left": wait
            })
        except:
            continue

# === Line filter UI with 'Select All Lines' button next to it ===
all_lines = sorted(set(r["Line"] for arrivals in grouped.values() for r in arrivals))

if "selected_lines" not in st.session_state:
    st.session_state.selected_lines = all_lines
else:
    filtered_selected = [line for line in st.session_state.selected_lines if line in all_lines]
    if set(filtered_selected) != set(st.session_state.selected_lines):
        st.session_state.selected_lines = filtered_selected

col_line_filter, col_button = st.columns([5, 1])

with col_button:
    if st.button("Select All Lines"):
        st.session_state.selected_lines = all_lines

with col_line_filter:
    selected_lines = st.multiselect("Filter by line", all_lines, default=st.session_state.selected_lines)

if selected_lines != st.session_state.selected_lines:
    st.session_state.selected_lines = selected_lines
# ===============================================================

for i in range(0, len(selected_stops), 2):
    col1, col2 = st.columns(2)
    for j, col in enumerate([col1, col2]):
        if i + j >= len(selected_stops):
            continue
        stop_name = selected_stops[i + j]
        arrivals = grouped.get(stop_name, [])
        filtered = [a for a in arrivals if a["Line"] in st.session_state.selected_lines]

        with col:
            st.subheader(f"🛑 {stop_name}")
            if filtered:
                df = pd.DataFrame(filtered).sort_values("Seconds Left")
                df = df[["Line", "Destination", "Expected Arrival", "Time Left"]]

                def style_line(val):
                    color = line_color_soft(str(val))
                    return f"color: white; background-color: {color}"

                st.dataframe(df.style.map(style_line, subset=["Line"]), use_container_width=True)
            else:
                st.info("No arrivals in selected time range or line.")

# Render map at the bottom with Plotly instead of pydeck
if st.session_state.get("last_stops") != selected_stops:
    st.session_state.last_stops = selected_stops
    data = []
    for stop in selected_stops:
        for coords in stop_dict[stop]["Coordinates"]:
            data.append({"lat": coords[0], "lon": coords[1], "stop_name": stop})

    df_map = pd.DataFrame(data)

    center_lat = df_map["lat"].mean()
    center_lon = df_map["lon"].mean()

    fig = px.scatter_mapbox(
        df_map,
        lat="lat",
        lon="lon",
        size_max = 2000,
        hover_name="stop_name",
        text="stop_name",
        zoom=15 if df_map["lat"].max() - df_map["lat"].min() < 0.05 else 12,
        height=600,
    )

    fig.update_layout(
        mapbox_style="carto-darkmatter",
        margin={"r":0,"t":0,"l":0,"b":0},
    )
    fig.update_traces(marker=dict(size=20, color = 'red'))

    st.session_state.map_chart = fig

st.markdown("## 🗺️ Stop Locations Map")
if "map_chart" in st.session_state:
    st.plotly_chart(st.session_state.map_chart, use_container_width=True)
