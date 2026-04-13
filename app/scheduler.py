import json
import sqlite3
import os
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from app.airlabs import fetch_schedules, fetch_routes

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def check_all_routes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    routes = conn.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

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

    # Mark flights no longer in feed as probably landed
    stale = conn.execute("""
        SELECT id, route_id, date, flight_iata FROM flights
        WHERE date = ? AND status IN ('scheduled', 'active')
    """, (today,)).fetchall()

    for row in stale:
        key = (row["route_id"], row["date"], row["flight_iata"])
        if key not in seen_flight_ids:
            conn.execute(
                "UPDATE flights SET status = 'probably_landed' WHERE id = ?",
                (row["id"],),
            )

    conn.execute(
        "INSERT INTO check_log (routes_checked, flights_found, source) VALUES (?, ?, ?)",
        (len(routes), total_flights, "scheduler"),
    )
    conn.commit()
    conn.close()


def check_all_schemas():
    """Daily schema check: fetch planned flights per route via /routes endpoint."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    conn.close()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_routes, "cron", hour=6, minute=0, id="check_morning")
    scheduler.add_job(check_all_routes, "cron", hour=14, minute=0, id="check_afternoon")
    scheduler.add_job(check_all_routes, "cron", hour=22, minute=0, id="check_evening")
    scheduler.add_job(check_all_schemas, "cron", hour=4, minute=0, id="check_schema")
    scheduler.start()
