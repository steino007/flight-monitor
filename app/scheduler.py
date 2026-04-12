import sqlite3
import os
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from app.airlabs import fetch_schedules

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def check_all_routes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    routes = conn.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

    total_flights = 0
    for route in routes:
        flights = fetch_schedules(route["origin"], route["destination"], route["airline"])
        total_flights += len(flights)
        for f in flights:
            # Use flight's departure date, fallback to today
            flight_date = today
            dep_time = f.get("dep_time", "")
            if dep_time and " " in dep_time:
                flight_date = dep_time.split(" ")[0]

            conn.execute("""
                INSERT INTO flights (route_id, date, flight_iata, dep_time, arr_time, arr_time_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id, date, flight_iata)
                DO UPDATE SET status=excluded.status, dep_time=excluded.dep_time,
                             arr_time=excluded.arr_time, arr_time_utc=excluded.arr_time_utc,
                             checked_at=CURRENT_TIMESTAMP
            """, (
                route["id"], flight_date,
                f.get("flight_iata", ""),
                dep_time,
                f.get("arr_time", ""),
                f.get("arr_time_utc", ""),
                f.get("status", "unknown"),
            ))

    conn.execute(
        "INSERT INTO check_log (routes_checked, flights_found, source) VALUES (?, ?, ?)",
        (len(routes), total_flights, "scheduler"),
    )
    conn.commit()
    conn.close()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_routes, "cron", hour=6, minute=0, id="check_morning")
    scheduler.add_job(check_all_routes, "cron", hour=14, minute=0, id="check_afternoon")
    scheduler.add_job(check_all_routes, "cron", hour=22, minute=0, id="check_evening")
    scheduler.start()
