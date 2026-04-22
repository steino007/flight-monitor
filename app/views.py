import json
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from app.db import get_db
from app.auth import login_required, check_password
from app.checks import run_flight_check, run_schema_check

bp = Blueprint("main", __name__)

STATUS_DISPLAY = {
    "scheduled": "Scheduled",
    "active": "Departed",
    "landed": "Arrived",
    "cancelled": "Cancelled",
    "unknown": "Unknown",
    "probably_landed": "Wsrl. geland",
}

STATUS_ICON = {
    "scheduled": "🕐",
    "active": "🛫",
    "landed": "✅",
    "cancelled": "❌",
    "unknown": "❓",
    "probably_landed": "🟡",
}

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
NL_TZ = ZoneInfo("Europe/Amsterdam")


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
    today_str = date.today().isoformat()
    date_filter = request.args.get("date", today_str)
    filter_date = date.fromisoformat(date_filter)
    filter_day = DAY_NAMES[filter_date.weekday()]

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()

    last_check = db.execute(
        "SELECT checked_at, routes_checked, flights_found, source FROM check_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Route summary cards
    route_cards = []
    for r in routes:
        route_name = f"{r['origin']} → {r['destination']}"
        airline = r["airline"] or ""

        # Count actual flights (Bug 4 fix: use date_filter as anchor for sparkline)
        today_count = _count_flights(db, r["id"], date_filter, airline)

        spark_rows = _query_flights_by_date(
            db, r["id"], date_filter, 7, airline
        )
        sparkline = [row["cnt"] for row in spark_rows]

        trend = _calc_trend(db, r["id"], date_filter, airline)

        # Planned count filtered by weekday (Bug 1 fix)
        planned_count = _planned_for_day(db, r["id"], filter_day)

        route_cards.append({
            "id": r["id"],
            "name": route_name,
            "origin": r["origin"],
            "destination": r["destination"],
            "airline": airline,
            "today_count": today_count,
            "planned_count": planned_count,
            "sparkline": sparkline,
            "trend": trend,
        })

    # Build schema lookup
    schema_current = {}
    schema_first = {}
    for r in routes:
        latest = db.execute(
            "SELECT flight_numbers FROM route_snapshots WHERE route_id = ? ORDER BY date DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        schema_current[r["id"]] = _parse_snapshot(latest["flight_numbers"]) if latest else {}

        first = db.execute(
            "SELECT flight_numbers FROM route_snapshots WHERE route_id = ? ORDER BY date ASC LIMIT 1",
            (r["id"],),
        ).fetchone()
        schema_first[r["id"]] = _parse_snapshot(first["flight_numbers"]) if first else {}

    # Flights grouped by route
    flights = db.execute("""
        SELECT f.*, r.origin, r.destination, r.airline
        FROM flights f
        JOIN routes r ON f.route_id = r.id
        WHERE f.date = ?
        ORDER BY r.origin ASC, r.destination ASC, f.dep_time ASC
    """, (date_filter,)).fetchall()

    flight_groups = {}
    seen_per_route = {}
    actual_count_per_route = {}  # Bug 3 fix: track actual count separately

    for f in flights:
        if not f["flight_iata"]:
            continue
        if f["airline"] and not f["flight_iata"].startswith(f["airline"]):
            continue

        route_name = f"{f['origin']} → {f['destination']}"
        route_id = f["route_id"]

        if route_name not in flight_groups:
            flight_groups[route_name] = []
        if route_id not in seen_per_route:
            seen_per_route[route_id] = set()
            actual_count_per_route[route_name] = 0

        seen_per_route[route_id].add(f["flight_iata"])
        actual_count_per_route[route_name] = actual_count_per_route.get(route_name, 0) + 1

        status_raw = f["status"]
        in_schema = f["flight_iata"] in schema_current.get(route_id, {})
        was_planned = f["flight_iata"] in schema_first.get(route_id, {})

        if in_schema:
            schema_status, schema_label, schema_icon = "planned", "Gepland", "✅"
        elif was_planned:
            schema_status, schema_label, schema_icon = "scrapped", "Geschrapt", "❌"
        else:
            schema_status, schema_label, schema_icon = "unknown", "—", ""

        flight_groups[route_name].append({
            "flight_iata": f["flight_iata"],
            "dep_time": _format_time(f["dep_time"]),
            "arr_time": _format_time(f["arr_time"]),
            "status": STATUS_DISPLAY.get(status_raw, status_raw),
            "status_icon": STATUS_ICON.get(status_raw, ""),
            "status_raw": status_raw,
            "schema_status": schema_status,
            "schema_label": schema_label,
            "schema_icon": schema_icon,
        })

    # Add missing flights (expected today but not seen)
    for r in routes:
        route_name = f"{r['origin']} → {r['destination']}"
        route_id = r["id"]
        current = schema_current.get(route_id, {})
        seen = seen_per_route.get(route_id, set())
        first = schema_first.get(route_id, {})
        missing = set(first.keys()) - seen

        for flight_iata in sorted(missing):
            info = current.get(flight_iata) or first.get(flight_iata) or {}

            # Only show if expected on this weekday
            flight_days = info.get("days", [])
            if flight_days and filter_day not in flight_days:
                continue

            if route_name not in flight_groups:
                flight_groups[route_name] = []

            in_current = flight_iata in current
            flight_groups[route_name].append({
                "flight_iata": flight_iata,
                "dep_time": info.get("dep_time", "—"),
                "arr_time": info.get("arr_time", "—"),
                "status": "Niet gezien" if in_current else "Geschrapt",
                "status_icon": "⚫" if in_current else "❌",
                "status_raw": "missing" if in_current else "scrapped_missing",
                "schema_status": "planned" if in_current else "scrapped",
                "schema_label": "Gepland" if in_current else "Geschrapt",
                "schema_icon": "✅" if in_current else "❌",
            })

    return render_template(
        "dashboard.html",
        route_cards=route_cards,
        flight_groups=flight_groups,
        actual_counts=actual_count_per_route,
        date_filter=date_filter,
        today=today_str,
        last_check={
            "checked_at": _utc_to_nl(last_check["checked_at"]),
            "routes_checked": last_check["routes_checked"],
            "flights_found": last_check["flights_found"],
            "source": last_check["source"],
        } if last_check else None,
    )


@bp.route("/api/trend")
@login_required
def trend_api():
    """Stacked bar data per route: planned vs flown vs cancelled."""
    db = get_db()
    days = min(int(request.args.get("days", 7)), 90)
    today_str = date.today().isoformat()

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()

    all_dates = []
    for i in range(days - 1, -1, -1):
        all_dates.append((date.today() - timedelta(days=i)).isoformat())

    result = {}
    for r in routes:
        route_name = f"{r['origin']} → {r['destination']}"
        airline = r["airline"] or ""

        # Get snapshot data (raw JSON) to compute per-day planned count (Bug 1 fix)
        snapshots = db.execute("""
            SELECT date, flight_numbers FROM route_snapshots
            WHERE route_id = ? AND date >= date(?, ?) AND date <= ?
            ORDER BY date
        """, (r["id"], today_str, f"-{days-1} days", today_str)).fetchall()

        # Build planned_flights: for each date, the set of planned flight numbers
        planned_flights = {}
        last_snapshot_data = None
        for snap in snapshots:
            snap_data = _parse_snapshot(snap["flight_numbers"])
            last_snapshot_data = snap_data
            snap_day = DAY_NAMES[date.fromisoformat(snap["date"]).weekday()]
            planned_flights[snap["date"]] = {
                iata for iata, info in snap_data.items()
                if not info.get("days") or snap_day in info["days"]
            }

        # Fill dates without snapshots using nearest earlier snapshot
        for d in all_dates:
            if d not in planned_flights and last_snapshot_data:
                d_day = DAY_NAMES[date.fromisoformat(d).weekday()]
                nearest = None
                for snap in snapshots:
                    if snap["date"] <= d:
                        nearest = snap
                if nearest:
                    snap_data = _parse_snapshot(nearest["flight_numbers"])
                    planned_flights[d] = {
                        iata for iata, info in snap_data.items()
                        if not info.get("days") or d_day in info["days"]
                    }

        # Get individual flight statuses (not aggregated)
        flight_rows = db.execute("""
            SELECT date, flight_iata, status FROM flights
            WHERE route_id = ? AND date >= date(?, ?) AND date <= ?
        """, (r["id"], today_str, f"-{days-1} days", today_str)).fetchall()

        # Build lookup: date → {flight_iata: status}
        flight_status = {}
        for row in flight_rows:
            flight_status.setdefault(row["date"], {})[row["flight_iata"]] = row["status"]

        # Per date: categorise each planned flight individually
        route_days = {}
        for d in all_dates:
            planned = planned_flights.get(d, set())
            statuses = flight_status.get(d, {})
            flown = 0
            cancelled = 0
            pending = 0

            for iata in planned:
                status = statuses.get(iata)
                if status in ("landed", "probably_landed"):
                    flown += 1
                elif status == "cancelled":
                    cancelled += 1
                elif status in ("scheduled", "active"):
                    pending += 1
                elif status is None:
                    # Not in flights table: past = not flown, today = pending
                    if d < today_str:
                        cancelled += 1
                    else:
                        pending += 1

            route_days[d] = {
                "planned": len(planned),
                "flown": flown,
                "cancelled": cancelled,
                "scrapped": 0,
                "pending": pending,
            }

        result[route_name] = route_days

    return jsonify({"dates": all_dates, "routes": result})


@bp.route("/check-schema", methods=["POST"])
@login_required
def manual_schema_check():
    db = get_db()
    run_schema_check(db)
    return redirect(url_for("main.dashboard"))


@bp.route("/check-all", methods=["POST"])
@login_required
def manual_check():
    db = get_db()
    run_flight_check(db)
    return redirect(url_for("main.dashboard"))


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

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()
    return render_template("routes.html", routes=routes)


@bp.route("/route/<int:route_id>/edit", methods=["POST"])
@login_required
def edit_route(route_id):
    db = get_db()
    airline = request.form.get("airline", "").strip().upper() or None
    db.execute("UPDATE routes SET airline = ? WHERE id = ?", (airline, route_id))
    db.commit()
    return redirect(url_for("main.manage_routes"))


@bp.route("/route/<int:route_id>/delete", methods=["POST"])
@login_required
def delete_route(route_id):
    db = get_db()
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    db.commit()
    return redirect(url_for("main.manage_routes"))


# --- Helpers ---

def _parse_snapshot(flight_numbers_json):
    """Parse snapshot data. Handles both old format (list of strings) and new format (list of dicts)."""
    data = json.loads(flight_numbers_json)
    result = {}
    for item in data:
        if isinstance(item, str):
            result[item] = {"dep_time": "—", "arr_time": "—", "days": []}
        elif isinstance(item, dict):
            iata = item.get("flight_iata", "")
            if iata:
                result[iata] = {
                    "dep_time": item.get("dep_time", "—"),
                    "arr_time": item.get("arr_time", "—"),
                    "days": item.get("days", []),
                }
    return result


def _planned_for_day(db, route_id, day_name):
    """Count planned flights for a specific weekday from the latest snapshot."""
    snapshot = db.execute(
        "SELECT flight_numbers FROM route_snapshots WHERE route_id = ? ORDER BY date DESC LIMIT 1",
        (route_id,),
    ).fetchone()
    if not snapshot:
        return 0
    snap_data = _parse_snapshot(snapshot["flight_numbers"])
    return sum(1 for info in snap_data.values()
               if not info.get("days") or day_name in info["days"])


def _count_flights(db, route_id, date_str, airline=""):
    """Count actual flights for a route on a specific date."""
    if airline:
        return db.execute(
            "SELECT COUNT(*) as cnt FROM flights WHERE route_id = ? AND date = ? AND flight_iata LIKE ?",
            (route_id, date_str, f"{airline}%"),
        ).fetchone()["cnt"]
    return db.execute(
        "SELECT COUNT(*) as cnt FROM flights WHERE route_id = ? AND date = ? AND flight_iata != ''",
        (route_id, date_str),
    ).fetchone()["cnt"]


def _query_flights_by_date(db, route_id, anchor_date, days, airline=""):
    """Get flight counts per date for sparkline/trend, anchored to a specific date."""
    if airline:
        return db.execute("""
            SELECT date, COUNT(*) as cnt FROM flights
            WHERE route_id = ? AND date >= date(?, ?) AND date <= ? AND flight_iata LIKE ?
            GROUP BY date ORDER BY date
        """, (route_id, anchor_date, f"-{days-1} days", anchor_date, f"{airline}%")).fetchall()
    return db.execute("""
        SELECT date, COUNT(*) as cnt FROM flights
        WHERE route_id = ? AND date >= date(?, ?) AND date <= ? AND flight_iata != ''
        GROUP BY date ORDER BY date
    """, (route_id, anchor_date, f"-{days-1} days", anchor_date)).fetchall()


def _calc_trend(db, route_id, anchor_date, airline=""):
    """Compare recent 3 days avg vs previous 3 days avg. Anchored to anchor_date."""
    rows = _query_flights_by_date(db, route_id, anchor_date, 7, airline)

    if len(rows) < 2:
        return "new"

    counts = [r["cnt"] for r in rows]
    mid = len(counts) // 2
    recent = sum(counts[mid:]) / max(len(counts[mid:]), 1)
    earlier = sum(counts[:mid]) / max(len(counts[:mid]), 1)

    if earlier == 0:
        return "new"
    change = (recent - earlier) / earlier
    if change < -0.2:
        return "down"
    elif change > 0.2:
        return "up"
    return "stable"


def _format_time(time_str):
    if not time_str:
        return "—"
    if " " in time_str:
        return time_str.split(" ")[1][:5]
    return time_str[:5]


def _utc_to_nl(timestamp_str):
    if not timestamp_str:
        return "—"
    try:
        dt = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(NL_TZ).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        return timestamp_str
