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
from .services.historical_import import import_high_risk_reference, import_historical_workbook
from .services.reference_geography import load_reference_geography
from .services.migrations import migrate_field_house_schema


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

    @app.cli.command("import-historical")
    @click.argument("workbook", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option(
        "--source-type",
        type=click.Choice(["case_line_list", "staffing_allocation", "block_reporting"]),
        required=True,
    )
    def import_historical(workbook: Path, source_type: str):
        """Import one reviewed historical Excel workbook without operational records."""
        with app.app_context():
            try:
                summary = import_historical_workbook(workbook.name, workbook.read_bytes(), source_type)
                if summary.skipped:
                    click.echo(f"Skipped: {workbook.name} was already imported.")
                    return
                db.session.commit()
            except Exception as error:
                db.session.rollback()
                raise click.ClickException(f"Import failed; no records were committed: {error}") from error
        click.echo(
            f"Import complete: {summary.inserted} inserted, {summary.updated} updated, "
            f"{summary.skipped} skipped, {summary.errors} errors."
        )

    @app.cli.command("import-high-risk")
    @click.argument("source_image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.argument("areas_csv", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def import_high_risk(source_image: Path, areas_csv: Path):
        """Import a reviewed high-risk area transcription linked to its source image."""
        with app.app_context():
            try:
                summary = import_high_risk_reference(
                    source_image.name, source_image.read_bytes(), areas_csv.read_bytes()
                )
                if summary.skipped:
                    click.echo(f"Skipped: {source_image.name} was already imported.")
                    return
                db.session.commit()
            except Exception as error:
                db.session.rollback()
                raise click.ClickException(f"Import failed; no records were committed: {error}") from error
        click.echo(f"Import complete: {summary.inserted} inserted, {summary.errors} errors.")

    @app.cli.command("load-reference-geography")
    def load_geography():
        """Load the approved block/locality reference without changing existing records."""
        with app.app_context():
            try:
                blocks_added, localities_added = load_reference_geography()
                db.session.commit()
            except Exception as error:
                db.session.rollback()
                raise click.ClickException(f"Geography load failed; no records were committed: {error}") from error
        click.echo(f"Reference geography loaded: {blocks_added} blocks and {localities_added} localities added.")

    @app.cli.command("migrate-household-member")
    def migrate_household_member():
        """Back up the configured SQLite database and add the house member field."""
        database_path = db.engine.url.database
        if not database_path or db.engine.url.drivername != "sqlite":
            raise click.ClickException("This migration supports only the configured SQLite database.")
        try:
            backup_path = migrate_field_house_schema(database_path)
        except Exception as error:
            raise click.ClickException(f"Migration failed: {error}") from error
        click.echo(f"Migration complete. Backup created: {backup_path}")

    @app.cli.command("migrate-field-house")
    def migrate_field_house():
        """Back up SQLite and add permanent field-house columns safely."""
        database_path = db.engine.url.database
        if not database_path or db.engine.url.drivername != "sqlite":
            raise click.ClickException("This migration supports only the configured SQLite database.")
        try:
            backup_path = migrate_field_house_schema(database_path)
        except Exception as error:
            raise click.ClickException(f"Migration failed: {error}") from error
        click.echo(f"Migration complete. Backup created: {backup_path}")

    return app
