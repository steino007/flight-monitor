import os
from flask import Flask
from app.db import init_db


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    init_db(app)

    from app.views import bp
    app.register_blueprint(bp)

    from app.scheduler import start_scheduler
    start_scheduler(app)

    return app
