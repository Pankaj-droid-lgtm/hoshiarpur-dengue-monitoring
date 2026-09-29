from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .extensions import db
from .models import Block, Deployment, House, HouseAssignment, HouseVisit, Locality, ReinspectionTask, Worker
from .security import validate_csrf
from .services.audit import log_change
from .services.permissions import require_management_access


deployments_bp = Blueprint("deployments", __name__, url_prefix="/deployments")
DEPLOYMENT_STATUSES = ("assigned", "started", "in_progress", "completed", "overdue", "cancelled")


def deployment_status(deployment: Deployment) -> str:
    if deployment.status in {"completed", "cancelled"}:
        return deployment.status
    if deployment.deployment_date < date.today():
        return "overdue"
    return deployment.status


def can_access_deployment(deployment: Deployment) -> bool:
    if current_user.role in {"admin", "dc", "adc", "district_officer", "block_officer", "supervisor"}:
        return True
    if current_user.worker is not None and deployment.worker_id == current_user.worker.id:
        return True
    return deployment.worker_id is None and deployment.account_user_id == current_user.id


def current_user_deployment_filter():
    """Use permanent-worker ownership, retaining a narrow legacy fallback."""
    worker_id = current_user.worker.id if current_user.worker is not None else -1
    return ((Deployment.worker_id == worker_id) | (
        Deployment.worker_id.is_(None) & (Deployment.account_user_id == current_user.id)
    ))


@deployments_bp.get("/")
@login_required
def dashboard():
    require_management_access()
    selected_date = parse_date(request.args.get("date")) or date.today()
    deployments = Deployment.query.filter_by(deployment_date=selected_date).order_by(Deployment.id).all()
    return render_template(
        "deployments/dashboard.html", deployments=deployments, selected_date=selected_date,
        deployment_status=deployment_status
    )


@deployments_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_deployment():
    require_management_access()
    if request.method == "POST":
        validate_csrf()
        worker_id = optional_int(request.form.get("worker_id"))
        worker = db.session.get(Worker, worker_id) if worker_id else None
        block_id = optional_int(request.form.get("block_id"))
        block = db.session.get(Block, block_id) if block_id else None
        locality_id = request.form.get("locality_id", "").strip()
        locality = db.session.get(Locality, optional_int(locality_id)) if locality_id else None
        scheduled_date = parse_date(request.form.get("deployment_date"))
        if not worker or not worker.is_active or not block or not scheduled_date:
            flash("Date, an active permanent worker, and block are required.", "error")
        elif locality and locality.block_id != block.id:
            flash("The locality does not belong to the selected block.", "error")
        else:
            deployment = Deployment(
                worker=worker,
                account_user=worker.user,
                worker_name=worker.full_name,
                worker_code=worker.official_worker_id,
                worker_contact=worker.phone_number,
                worker_designation=worker.designation,
                block=block,
                locality=locality,
                supervisor=worker.supervisor,
                supervisor_name=worker.supervisor.full_name if worker.supervisor else None,
                deployment_date=scheduled_date,
                team_name=request.form.get("team_name", "").strip() or None,
                priority=request.form.get("priority", "normal"),
                notes=request.form.get("notes", "").strip() or None,
                assigned_by_user_id=current_user.id,
            )
            db.session.add(deployment)
            db.session.flush()
            log_change("create", "deployment", deployment.id, after={"date": str(scheduled_date), "worker_id": worker.id, "block_id": block.id})
            db.session.commit()
            flash("Deployment created. Add known houses only when authoritative house data is available.", "success")
            return redirect(url_for("deployments.detail", deployment_id=deployment.id))
    return render_template("deployments/form.html", workers=active_workers(), blocks=Block.query.order_by(Block.name).all(), localities=Locality.query.order_by(Locality.name).all(), today=date.today())


@deployments_bp.get("/<int:deployment_id>")
@login_required
def detail(deployment_id: int):
    deployment = db.get_or_404(Deployment, deployment_id)
    if not can_access_deployment(deployment):
        abort(403)
    assignments = HouseAssignment.query.filter_by(deployment_id=deployment.id).order_by(HouseAssignment.id).all()
    destinations = []
    if current_user.role in {"admin", "dc", "adc", "district_officer", "block_officer", "supervisor"}:
        destinations = Deployment.query.filter(
            Deployment.deployment_date == deployment.deployment_date,
            Deployment.id != deployment.id,
            Deployment.status.notin_(["completed", "cancelled"]),
        ).order_by(Deployment.id).all()
    return render_template(
        "deployments/detail.html", deployment=deployment, assignments=assignments,
        available_houses=available_houses(deployment), destinations=destinations,
        deployment_status=deployment_status
    )


@deployments_bp.post("/<int:deployment_id>/start")
@login_required
def start_deployment(deployment_id: int):
    validate_csrf()
    deployment = db.get_or_404(Deployment, deployment_id)
    if not can_access_deployment(deployment):
        abort(403)
    if deployment.status == "assigned":
        deployment.status = "started"
        deployment.started_at = datetime.utcnow()
        log_change("start", "deployment", deployment.id, after={"status": "started"})
        db.session.commit()
    return redirect(url_for("deployments.mobile"))


@deployments_bp.post("/<int:deployment_id>/assignments")
@login_required
def add_assignment(deployment_id: int):
    require_management_access()
    validate_csrf()
    deployment = db.get_or_404(Deployment, deployment_id)
    house = db.session.get(House, optional_int(request.form.get("house_id")))
    if not house:
        flash("Select a valid operational house.", "error")
    elif deployment.locality_id and house.locality_id != deployment.locality_id:
        flash("The house does not belong to the deployment locality.", "error")
    elif HouseAssignment.query.filter_by(deployment_id=deployment.id, house_id=house.id).first():
        flash("That house is already assigned to this deployment.", "error")
    else:
        assignment = HouseAssignment(deployment=deployment, house=house, priority=request.form.get("priority", deployment.priority))
        db.session.add(assignment)
        db.session.flush()
        log_change("assign_house", "house_assignment", assignment.id, after={"deployment_id": deployment.id, "house_id": house.id})
        db.session.commit()
        flash("House assigned.", "success")
    return redirect(url_for("deployments.detail", deployment_id=deployment.id))


@deployments_bp.post("/assignments/<int:assignment_id>/reassign")
@login_required
def reassign_assignment(assignment_id: int):
    require_management_access()
    validate_csrf()
    assignment = db.get_or_404(HouseAssignment, assignment_id)
    destination = db.session.get(Deployment, optional_int(request.form.get("destination_deployment_id")))
    if assignment.completed_at is not None or HouseVisit.query.filter_by(assignment_id=assignment.id).first():
        flash("Completed work cannot be reassigned.", "error")
    elif not destination or destination.deployment_date != assignment.deployment.deployment_date:
        flash("Choose an active deployment for the same date.", "error")
    elif destination.status in {"completed", "cancelled"}:
        flash("The selected destination deployment is not active.", "error")
    elif HouseAssignment.query.filter_by(deployment_id=destination.id, house_id=assignment.house_id).first():
        flash("The house is already assigned to the selected deployment.", "error")
    else:
        previous_deployment_id = assignment.deployment_id
        assignment.deployment = destination
        log_change("reassign", "house_assignment", assignment.id, before={"deployment_id": previous_deployment_id}, after={"deployment_id": destination.id})
        db.session.commit()
        flash("Pending house assignment reassigned.", "success")
    return redirect(url_for("deployments.detail", deployment_id=assignment.deployment_id))


@deployments_bp.get("/mobile")
@login_required
def mobile():
    deployments = Deployment.query.filter(Deployment.deployment_date == date.today()).filter(
        current_user_deployment_filter(),
        Deployment.status.notin_(["cancelled", "completed"])
    ).all()
    priority_rechecks = 0
    if deployments:
        priority_rechecks = ReinspectionTask.query.join(House).join(HouseAssignment).join(Deployment).filter(
            current_user_deployment_filter(),
            Deployment.deployment_date == date.today(),
            ReinspectionTask.status == "open",
        ).distinct().count()
    return render_template("deployments/mobile.html", deployments=deployments, priority_rechecks=priority_rechecks, today=date.today(), deployment_status=deployment_status)


@deployments_bp.get("/mobile/houses")
@login_required
def mobile_house_search():
    query = request.args.get("q", "").strip()
    houses = []
    if query:
        houses = House.query.join(HouseAssignment).join(Deployment).filter(
            current_user_deployment_filter(),
            House.address.ilike(f"%{query}%"),
        ).distinct().all()
    return render_template("deployments/mobile_house_search.html", houses=houses, query=query)


@deployments_bp.get("/mobile/visits")
@login_required
def mobile_visits():
    visits = HouseVisit.query.join(Deployment, HouseVisit.deployment_id == Deployment.id).filter(
        current_user_deployment_filter()
    ).order_by(HouseVisit.visited_at.desc()).limit(50).all()
    return render_template("deployments/mobile_visits.html", visits=visits)


def active_workers() -> list[Worker]:
    return Worker.query.filter_by(is_active=True).order_by(Worker.full_name).all()


def available_houses(deployment: Deployment) -> list[House]:
    query = House.query.filter_by(is_active=True)
    if deployment.locality_id:
        query = query.filter_by(locality_id=deployment.locality_id)
    return query.order_by(House.house_code).all()


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None
