import json
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.db import get_db
from app.auth import login_required, check_password
from app.airlabs import fetch_schedules

bp = Blueprint("main", __name__)

STATUS_DISPLAY = {
    "scheduled": "🕐 Scheduled",
    "active": "🛫 Departed",
    "landed": "✅ Arrived",
    "cancelled": "❌ Cancelled",
    "unknown": "❓ Unknown",
}


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_password(request.form.get("password", "")):
            session["authenticated"] = True
            return redirect(url_for("main.dashboard"))
        return render_template("login.html", error="Verkeerd wachtwoord")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    date_filter = request.args.get("date", date.today().isoformat())

    flights = db.execute("""
        SELECT f.*, r.origin, r.destination, r.airline
        FROM flights f
        JOIN routes r ON f.route_id = r.id
        WHERE f.date = ?
        ORDER BY f.dep_time ASC, r.origin ASC
    """, (date_filter,)).fetchall()

    routes = db.execute("SELECT * FROM routes ORDER BY created_at").fetchall()

    last_check = db.execute(
        "SELECT checked_at, routes_checked, flights_found, source FROM check_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Format flights for display
    now_utc = datetime.now(timezone.utc)
    flight_list = []
    for f in flights:
        status = f["status"]
        status_display = STATUS_DISPLAY.get(status, status)

        # If arrival time has passed and status is still scheduled/active,
        # show as "Waarschijnlijk geland"
        if status in ("scheduled", "active") and _is_past(f["arr_time_utc"], now_utc):
            status_display = "🟡 Waarschijnlijk geland"
            status = "probably_landed"

        flight_list.append({
            "date": f["date"],
            "origin": f["origin"],
            "destination": f["destination"],
            "airline": f["airline"],
            "flight_iata": f["flight_iata"],
            "dep_time": _format_time(f["dep_time"]),
            "arr_time": _format_time(f["arr_time"]),
            "status": status_display,
            "status_raw": status,
            "checked_at": _utc_to_nl(f["checked_at"]),
        })

    return render_template(
        "dashboard.html",
        flights=flight_list,
        routes=routes,
        date_filter=date_filter,
        today=date.today().isoformat(),
        last_check={
            "checked_at": _utc_to_nl(last_check["checked_at"]),
            "routes_checked": last_check["routes_checked"],
            "flights_found": last_check["flights_found"],
            "source": last_check["source"],
        } if last_check else None,
    )


@bp.route("/routes", methods=["GET", "POST"])
@login_required
def manage_routes():
    db = get_db()
    if request.method == "POST":
        origin = request.form.get("origin", "").strip().upper()
        destination = request.form.get("destination", "").strip().upper()
        airline = request.form.get("airline", "").strip().upper() or None

        if origin and destination:
            db.execute(
                "INSERT INTO routes (origin, destination, airline) VALUES (?, ?, ?)",
                (origin, destination, airline),
            )
            db.commit()
            return redirect(url_for("main.manage_routes"))

    routes = db.execute("SELECT * FROM routes ORDER BY created_at").fetchall()
    return render_template("routes.html", routes=routes)


@bp.route("/route/<int:route_id>/delete", methods=["POST"])
@login_required
def delete_route(route_id):
    db = get_db()
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    db.commit()
    return redirect(url_for("main.manage_routes"))


@bp.route("/check-all", methods=["POST"])
@login_required
def manual_check():
    """Trigger a manual check for all routes."""
    db = get_db()
    routes = db.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

    total_flights = 0
    for route in routes:
        flights = fetch_schedules(route["origin"], route["destination"], route["airline"])
        total_flights += len(flights)
        for f in flights:
            flight_date = today
            dep_time = f.get("dep_time", "")
            if dep_time and " " in dep_time:
                flight_date = dep_time.split(" ")[0]

            db.execute("""
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

    db.execute(
        "INSERT INTO check_log (routes_checked, flights_found, source) VALUES (?, ?, ?)",
        (len(routes), total_flights, "manual"),
    )
    db.commit()
    return redirect(url_for("main.dashboard"))


NL_TZ = ZoneInfo("Europe/Amsterdam")


def _format_time(time_str):
    """Extract HH:MM from datetime string like '2026-04-12 08:30'."""
    if not time_str:
        return "—"
    if " " in time_str:
        return time_str.split(" ")[1][:5]
    return time_str[:5]


def _is_past(arr_time_utc_str, now_utc):
    """Check if a flight's UTC arrival time is in the past."""
    if not arr_time_utc_str:
        return False
    try:
        arr_utc = datetime.fromisoformat(arr_time_utc_str).replace(tzinfo=timezone.utc)
        return now_utc > arr_utc
    except (ValueError, TypeError):
        return False


def _utc_to_nl(timestamp_str):
    """Convert UTC timestamp string to Dutch time string."""
    if not timestamp_str:
        return "—"
    try:
        dt = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(NL_TZ).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return timestamp_str
