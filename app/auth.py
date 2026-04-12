import os
from functools import wraps
from flask import session, redirect, url_for

PASSWORD = os.environ.get("FLIGHT_MONITOR_PASSWORD", "changeme")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated


def check_password(password):
    return password == PASSWORD
