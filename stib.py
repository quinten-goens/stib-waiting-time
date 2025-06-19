import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime, timezone
from dateutil import parser
from streamlit_autorefresh import st_autorefresh
import pydeck as pdk
import os

# Constants
API_KEY = os.environ['API_KEY_MIVB']
API_URL = "https://data.stib-mivb.be/api/explore/v2.1/catalog/datasets/waiting-time-rt-production/records"
CSV_PATH = "data/gtfs-stops-production.csv"
REFRESH_SECONDS = 30
TABLE_REFRESH_INTERVAL = 1000  # Refresh table every second

# Streamlit setup
st.set_page_config(page_title="STIB Real-Time Arrivals", layout="wide")
st.title("🚊 STIB Real-Time Tram/Bus Arrivals")
st.caption(f"Expected arrival data refreshes every {REFRESH_SECONDS} seconds (countdown updates every second).")

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

# UI for stop selection and time filter
cola, colb = st.columns([2, 1])
selected_stops = cola.multiselect("Choose stops", list(stop_dict.keys()), default=["FLAGEY", "LEVURE", "GERMOIR", "WERY"])
time_limit_minutes = colb.slider("Only show arrivals within the next X minutes", min_value=1, max_value=30, value=5)

if not selected_stops:
    st.warning("Select at least one stop to display arrival info.")
    st.stop()

# Prepare API call
pointids = [pid for stop in selected_stops for pid in stop_dict[stop]['IDs']]
where_clause = "pointid IN (" + ",".join([f'"{pid}"' for pid in pointids]) + ")"
params = {
    "apikey": API_KEY,
    "where": where_clause,
    "limit": 100
}

# Auto refresh table every second
st_autorefresh(interval=TABLE_REFRESH_INTERVAL, key="table_refresh")

# Fetch API data
def fetch_data():
    try:
        response = requests.get(API_URL, params=params)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception as e:
        st.error(f"API error: {e}")
        return []

# Session state for storing data
if "data" not in st.session_state:
    st.session_state.data = []
    st.session_state.last_fetch_time = None
    st.session_state.raw_results = []

# Manual refresh
if st.button("🔁 Refresh Now"):
    st.session_state.raw_results = fetch_data()
    st.session_state.last_fetch_time = datetime.now(timezone.utc)

# Auto refresh API data every 30 seconds
now = datetime.now(timezone.utc)
if (st.session_state.last_fetch_time is None or
    (now - st.session_state.last_fetch_time).total_seconds() > REFRESH_SECONDS):
    new_data = fetch_data()
    if set(map(json.dumps, new_data)) != set(map(json.dumps, st.session_state.raw_results)):
        st.session_state.raw_results = new_data
        st.session_state.last_fetch_time = now

# Process API response
grouped = {}
now = datetime.now(timezone.utc)
max_seconds = time_limit_minutes * 60

for record in st.session_state.raw_results:
    pointid = record["pointid"]
    line = record["lineid"]
    try:
        times = json.loads(record["passingtimes"])
    except Exception:
        continue

    for pt in times:
        try:
            arrival = parser.isoparse(pt["expectedArrivalTime"])
            wait = (arrival - now).total_seconds()
            if wait > max_seconds or wait <= 0:
                continue  # Filter out arrivals beyond time window or past

            wait_display = f"{int(wait // 60)}m {int(wait % 60)}s"
            destination = pt.get("destination", {}).get("fr", "Unknown")
            stop_name = next((name for name, data in stop_dict.items() if pointid in data['IDs']), pointid)

            grouped.setdefault(stop_name, []).append({
                "Line": line,
                "Destination": destination,
                "Expected Arrival": arrival.astimezone().strftime("%H:%M:%S"),
                "Time Left": wait_display,
                "Seconds Left": wait
            })
        except Exception:
            continue

# Layout with columns: Table on the left, Map on the right
col1, col2 = st.columns([2, 1])

with col1:
    for stop_name in selected_stops:
        st.subheader(f"🛑 {stop_name}")
        arrivals = grouped.get(stop_name, [])
        if arrivals:
            df = pd.DataFrame(arrivals).sort_values("Seconds Left")
            df = df[["Line", "Destination", "Expected Arrival", "Time Left"]]
            st.table(df)
        else:
            st.info("No upcoming vehicles at this stop within the selected time range.")

# Only regenerate and store the map when the selected stops change
if st.session_state.get("last_stops") != selected_stops:
    st.session_state.last_stops = selected_stops

    # Build the data
    data, latitudes, longitudes = [], [], []
    for stop_name in selected_stops:
        for coords in stop_dict[stop_name]['Coordinates']:
            latitudes.append(coords[0])
            longitudes.append(coords[1])
            data.append({'lat': coords[0], 'lon': coords[1], 'stop_name': stop_name})

    center_lat = sum(latitudes) / len(latitudes)
    center_lon = sum(longitudes) / len(longitudes)
    lat_diff = max(latitudes) - min(latitudes)
    lon_diff = max(longitudes) - min(longitudes)

    zoom_level = 14
    if lat_diff > 0.05 or lon_diff > 0.05:
        zoom_level = 11
    if lat_diff > 0.1 or lon_diff > 0.1:
        zoom_level = 10

    st.session_state.map_chart = pdk.Deck(
        initial_view_state=pdk.ViewState(
            latitude=center_lat,
            longitude=center_lon,
            zoom=zoom_level,
            pitch=0
        ),
        layers=[
            pdk.Layer(
                "ScatterplotLayer",
                data,
                get_position=["lon", "lat"],
                get_radius=10,
                get_fill_color=[255, 0, 0],
                opacity=0.8
            )
        ],
        height=600
    )

# Only render the map if it exists in session state
if "map_chart" in st.session_state:
    with col2:
        st.pydeck_chart(st.session_state.map_chart)
