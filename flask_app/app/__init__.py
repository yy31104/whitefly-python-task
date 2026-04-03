from __future__ import annotations

from flask import Flask

from flask_app.app.config import Config
from shared.db import init_database


def create_app(config_override: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config)

    if config_override:
        app.config.update(config_override)

    init_database(app.config["DATABASE_URL"])

    from flask_app.app.routes import bp

    app.register_blueprint(bp)
    return app
