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
