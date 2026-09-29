from sqlalchemy import or_
from werkzeug.security import generate_password_hash

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .extensions import db
from .models import Role, User, Worker
from .security import validate_csrf
from .services.audit import log_change
from .services.permissions import can_access_worker, require_operational_management_access


workers_bp = Blueprint("workers", __name__, url_prefix="/workers")


def selected_supervisor_id() -> int | None:
    value = request.form.get("supervisor_id", "").strip()
    return int(value) if value else None


@workers_bp.get("/")
@login_required
def worker_list():
    require_operational_management_access()
    query = request.args.get("q", "").strip()
    active_filter = request.args.get("active", "active")
    workers = Worker.query
    if query:
        pattern = f"%{query}%"
        workers = workers.filter(
            or_(Worker.official_worker_id.ilike(pattern), Worker.full_name.ilike(pattern))
        )
    if active_filter == "active":
        workers = workers.filter_by(is_active=True)
    elif active_filter == "inactive":
        workers = workers.filter_by(is_active=False)
    return render_template(
        "workers/list.html", workers=workers.order_by(Worker.full_name).all(), query=query,
        active_filter=active_filter
    )


@workers_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_worker():
    require_operational_management_access()
    if request.method == "POST":
        validate_csrf()
        worker_id = request.form.get("official_worker_id", "").strip()
        full_name = request.form.get("full_name", "").strip()
        designation = request.form.get("designation", "").strip()
        if not worker_id or not full_name or not designation:
            flash("Worker ID, name, and designation are required.", "error")
        elif Worker.query.filter_by(official_worker_id=worker_id).first():
            flash("That worker ID already exists.", "error")
        else:
            worker = Worker(
                official_worker_id=worker_id,
                full_name=full_name,
                designation=designation,
                phone_number=request.form.get("phone_number", "").strip() or None,
                availability_status=request.form.get("availability_status", "available"),
                requires_login=request.form.get("requires_login") == "on",
                supervisor_id=selected_supervisor_id(),
            )
            db.session.add(worker)
            db.session.flush()
            log_change("create", "worker", worker.id, after={"worker_id": worker.official_worker_id})
            db.session.commit()
            flash("Worker created. Create an account only when login access is required.", "success")
            return redirect(url_for("workers.worker_detail", worker_id=worker.id))
    return render_template("workers/form.html", worker=None, supervisors=active_supervisors())


@workers_bp.route("/<int:worker_id>/edit", methods=["GET", "POST"])
@login_required
def edit_worker(worker_id: int):
    require_operational_management_access()
    worker = db.get_or_404(Worker, worker_id)
    if request.method == "POST":
        validate_csrf()
        before = {"designation": worker.designation, "active": worker.is_active}
        worker.full_name = request.form.get("full_name", "").strip()
        worker.designation = request.form.get("designation", "").strip()
        worker.phone_number = request.form.get("phone_number", "").strip() or None
        worker.availability_status = request.form.get("availability_status", "available")
        worker.requires_login = request.form.get("requires_login") == "on"
        worker.supervisor_id = selected_supervisor_id()
        if not worker.full_name or not worker.designation:
            flash("Name and designation are required.", "error")
        else:
            log_change("update", "worker", worker.id, before=before, after={"designation": worker.designation, "active": worker.is_active})
            db.session.commit()
            flash("Worker details updated.", "success")
            return redirect(url_for("workers.worker_detail", worker_id=worker.id))
    return render_template("workers/form.html", worker=worker, supervisors=active_supervisors(exclude_id=worker.id))


@workers_bp.get("/<int:worker_id>")
@login_required
def worker_detail(worker_id: int):
    worker = db.get_or_404(Worker, worker_id)
    if not can_access_worker(worker.id):
        abort(403)
    return render_template("workers/detail.html", worker=worker, roles=[role.value for role in Role])


@workers_bp.post("/<int:worker_id>/deactivate")
@login_required
def deactivate_worker(worker_id: int):
    require_operational_management_access()
    validate_csrf()
    worker = db.get_or_404(Worker, worker_id)
    worker.is_active = False
    worker.availability_status = "inactive"
    if worker.user:
        worker.user.is_active = False
    log_change("deactivate", "worker", worker.id, after={"active": False})
    db.session.commit()
    flash("Worker deactivated. Historical records remain preserved.", "success")
    return redirect(url_for("workers.worker_detail", worker_id=worker.id))


@workers_bp.route("/<int:worker_id>/account", methods=["GET", "POST"])
@login_required
def set_account(worker_id: int):
    require_operational_management_access()
    worker = db.get_or_404(Worker, worker_id)
    if request.method == "POST":
        validate_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "field_worker")
        if len(password) < 12:
            flash("Password must contain at least 12 characters.", "error")
        elif role not in {item.value for item in Role}:
            flash("Invalid access role.", "error")
        elif worker.user is None and User.query.filter_by(username=username).first():
            flash("That login identifier is already in use.", "error")
        elif not username:
            flash("Login identifier is required.", "error")
        else:
            if worker.user is None:
                worker.user = User(username=username, password_hash=generate_password_hash(password), role=role)
                action = "create_account"
            else:
                worker.user.username = username
                worker.user.password_hash = generate_password_hash(password)
                worker.user.role = role
                worker.user.is_active = worker.is_active
                action = "reset_account_password"
            worker.requires_login = True
            log_change(action, "worker", worker.id, after={"username": username, "role": role})
            db.session.commit()
            flash("Worker account updated. Passwords are stored only as hashes.", "success")
            return redirect(url_for("workers.worker_detail", worker_id=worker.id))
    return render_template("workers/account.html", worker=worker, roles=[role.value for role in Role])


def active_supervisors(exclude_id: int | None = None) -> list[Worker]:
    query = Worker.query.filter_by(is_active=True)
    if exclude_id is not None:
        query = query.filter(Worker.id != exclude_id)
    return query.order_by(Worker.full_name).all()
