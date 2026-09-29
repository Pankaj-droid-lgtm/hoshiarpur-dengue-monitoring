from pathlib import Path

import click
from flask import Flask
from werkzeug.security import generate_password_hash

from config import Config

from .auth import auth_bp
from .deployments import deployments_bp
from .extensions import db, login_manager
from .geography import geography_bp
from .imports import imports_bp
from .main import main_bp
from .monitoring import monitoring_bp
from .models import User
from .workers import workers_bp
from .visits import visits_bp
from .security import csrf_token


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if app.config["UPLOAD_DIRECTORY"] is None:
        app.config["UPLOAD_DIRECTORY"] = str(Path(app.instance_path) / "uploads")

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("DENGUE_SECRET_KEY must be configured before the application can start.")

    db.init_app(app)
    login_manager.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(deployments_bp)
    app.register_blueprint(geography_bp)
    app.register_blueprint(imports_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(workers_bp)
    app.register_blueprint(visits_bp)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.cli.command("init-db")
    def init_db():
        """Create the empty application database schema."""
        with app.app_context():
            db.create_all()

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(username: str, password: str):
        """Create the first administrator without creating a worker record."""
        if len(password) < 12:
            raise click.UsageError("Password must contain at least 12 characters.")
        with app.app_context():
            if User.query.filter_by(username=username.strip()).first():
                raise click.UsageError("That username already exists.")
            db.session.add(
                User(
                    username=username.strip(),
                    password_hash=generate_password_hash(password),
                    role="admin",
                )
            )
            db.session.commit()
        click.echo("Administrator account created.")

    return app
