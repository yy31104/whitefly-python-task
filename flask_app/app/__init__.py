from __future__ import annotations

from flask import Flask

from flask_app.app.config import Config
from shared.db import init_database


def create_app(config_override: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config)

    if config_override:
        app.config.update(config_override)

    app_env = str(app.config.get("APP_ENV", "development")).strip().lower()
    secret_key = app.config.get("SECRET_KEY")
    secret_key_is_set = bool(app.config.get("SECRET_KEY_IS_SET")) or bool(config_override and config_override.get("SECRET_KEY"))
    if app_env == "production" and not secret_key_is_set:
        raise RuntimeError("SECRET_KEY must be explicitly set when APP_ENV=production.")
    if app_env == "production" and not secret_key:
        raise RuntimeError("SECRET_KEY must not be empty when APP_ENV=production.")

    init_database(app.config["DATABASE_URL"])

    from flask_app.app.routes import bp

    app.register_blueprint(bp, url_prefix=app.config.get("FLASK_URL_PREFIX", ""))
    return app
