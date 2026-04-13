import sqlite3
import os
from apscheduler.schedulers.background import BackgroundScheduler
from app.checks import run_flight_check, run_schema_check

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def check_all_routes():
    conn = _get_conn()
    try:
        run_flight_check(conn)
    finally:
        conn.close()


def check_all_schemas():
    conn = _get_conn()
    try:
        run_schema_check(conn)
    finally:
        conn.close()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_routes, "cron", hour=6, minute=0, id="check_morning")
    scheduler.add_job(check_all_routes, "cron", hour=14, minute=0, id="check_afternoon")
    scheduler.add_job(check_all_routes, "cron", hour=22, minute=0, id="check_evening")
    scheduler.add_job(check_all_schemas, "cron", hour=4, minute=0, id="check_schema")
    scheduler.start()
