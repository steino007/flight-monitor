import os
import requests

API_KEY = os.environ.get("AIRLABS_API_KEY", "")
BASE_URL = "https://airlabs.co/api/v9"


def fetch_schedules(origin, destination, airline=None):
    """Fetch live flight schedules from AirLabs /schedules endpoint.
    Returns list of flight dicts with status, or empty list on error.
    """
    params = {
        "api_key": API_KEY,
        "dep_iata": origin,
        "arr_iata": destination,
    }
    if airline:
        params["airline_iata"] = airline

    try:
        resp = requests.get(f"{BASE_URL}/schedules", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", []) or []
    except Exception:
        return []


def fetch_routes(origin, destination):
    """Fetch planned route schedule from AirLabs /routes endpoint.
    Returns list of planned flights with days, times, flight numbers.
    """
    params = {
        "api_key": API_KEY,
        "dep_iata": origin,
        "arr_iata": destination,
    }
    try:
        resp = requests.get(f"{BASE_URL}/routes", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        flights = data.get("response", []) or []
        # Filter out codeshares and flights without code
        seen = set()
        unique = []
        for f in flights:
            iata = f.get("flight_iata") or ""
            if not iata:
                continue
            # Skip codeshares (cs_flight_iata means this is a codeshare)
            if f.get("cs_airline_iata"):
                continue
            if iata not in seen:
                seen.add(iata)
                unique.append(f)
        return unique
    except Exception:
        return []
