"""Shared check logic used by both scheduler and manual endpoints."""

import json
import sqlite3
from datetime import date, timedelta
from app.airlabs import fetch_schedules, fetch_routes


def run_flight_check(conn):
    """Check all routes for current flights via /schedules endpoint."""
    routes = conn.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    total_flights = 0
    seen_flight_ids = set()

    for route in routes:
        flights = fetch_schedules(route["origin"], route["destination"], route["airline"])
        total_flights += len(flights)
        for f in flights:
            flight_date = today
            dep_time = f.get("dep_time", "")
            if dep_time and " " in dep_time:
                flight_date = dep_time.split(" ")[0]

            flight_iata = f.get("flight_iata", "")
            seen_flight_ids.add((route["id"], flight_date, flight_iata))

            conn.execute("""
                INSERT INTO flights (route_id, date, flight_iata, dep_time, arr_time, arr_time_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id, date, flight_iata)
                DO UPDATE SET status=excluded.status, dep_time=excluded.dep_time,
                             arr_time=excluded.arr_time, arr_time_utc=excluded.arr_time_utc,
                             checked_at=CURRENT_TIMESTAMP
            """, (
                route["id"], flight_date,
                flight_iata,
                dep_time,
                f.get("arr_time", ""),
                f.get("arr_time_utc", ""),
                f.get("status", "unknown"),
            ))

    # Mark flights no longer in feed as probably landed (today + yesterday)
    stale = conn.execute("""
        SELECT id, route_id, date, flight_iata FROM flights
        WHERE date IN (?, ?) AND status IN ('scheduled', 'active')
    """, (today, yesterday)).fetchall()

    for row in stale:
        key = (row["route_id"], row["date"], row["flight_iata"])
        if key not in seen_flight_ids:
            conn.execute(
                "UPDATE flights SET status = 'probably_landed' WHERE id = ?",
                (row["id"],),
            )

    conn.execute(
        "INSERT INTO check_log (routes_checked, flights_found, source) VALUES (?, ?, ?)",
        (len(routes), total_flights, "manual"),
    )
    conn.commit()
    return total_flights


def run_schema_check(conn):
    """Check all routes for planned flights via /routes endpoint."""
    routes = conn.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

    for route in routes:
        planned = fetch_routes(route["origin"], route["destination"], route["airline"])
        flight_data = [{
            "flight_iata": f.get("flight_iata", ""),
            "dep_time": f.get("dep_time", ""),
            "arr_time": f.get("arr_time", ""),
            "days": f.get("days", []),
        } for f in planned]

        conn.execute("""
            INSERT INTO route_snapshots (route_id, date, planned_count, flight_numbers)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(route_id, date)
            DO UPDATE SET planned_count=excluded.planned_count,
                         flight_numbers=excluded.flight_numbers,
                         checked_at=CURRENT_TIMESTAMP
        """, (
            route["id"], today,
            len(planned),
            json.dumps(flight_data),
        ))

    conn.commit()
