import sqlite3
import os
from flask import g

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    app.teardown_appcontext(close_db)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            airline TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            flight_iata TEXT,
            dep_time TEXT,
            arr_time TEXT,
            arr_time_utc TEXT,
            status TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(route_id, date, flight_iata)
        );
        CREATE INDEX IF NOT EXISTS idx_flights_route_date ON flights(route_id, date);
        CREATE INDEX IF NOT EXISTS idx_flights_date ON flights(date DESC);
        CREATE TABLE IF NOT EXISTS check_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            routes_checked INTEGER DEFAULT 0,
            flights_found INTEGER DEFAULT 0,
            source TEXT DEFAULT 'scheduler'
        );
        CREATE TABLE IF NOT EXISTS route_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            planned_count INTEGER DEFAULT 0,
            flight_numbers TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(route_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_route_date ON route_snapshots(route_id, date);
    """)
    # Migration: add arr_time_utc column if missing
    columns = [row[1] for row in conn.execute("PRAGMA table_info(flights)").fetchall()]
    if "arr_time_utc" not in columns:
        conn.execute("ALTER TABLE flights ADD COLUMN arr_time_utc TEXT")

    conn.commit()
    conn.close()
