from datetime import date, datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .extensions import db
from .models import (
    Block, Deployment, HistoricalHighRiskCluster, House, HouseAssignment,
    HouseVisit, Locality, ReinspectionTask, Worker,
)
from .security import validate_csrf
from .services.audit import log_change
from .services.reference_geography import REFERENCE_GEOGRAPHY
from .services.permissions import (
    has_operational_management_access,
    require_management_access,
    require_operational_management_access,
)


deployments_bp = Blueprint("deployments", __name__, url_prefix="/deployments")
DEPLOYMENT_STATUSES = ("assigned", "started", "in_progress", "completed", "overdue", "cancelled")


def deployment_status(deployment: Deployment) -> str:
    if not deployment.house_assignments:
        return "no_houses"
    if deployment.status in {"completed", "cancelled"}:
        return deployment.status
    if deployment.deployment_date < date.today():
        return "overdue"
    return deployment.status


def can_access_deployment(deployment: Deployment) -> bool:
    if current_user.role in {"admin", "dc", "adc", "district_officer", "block_officer", "supervisor"}:
        return True
    return (
        (current_user.worker is not None and deployment.worker_id == current_user.worker.id)
        or deployment.account_user_id == current_user.id
    )


def current_user_deployment_filter():
    """Match the worker record and its linked permanent account."""
    worker_id = current_user.worker.id if current_user.worker is not None else -1
    return (Deployment.worker_id == worker_id) | (Deployment.account_user_id == current_user.id)


@deployments_bp.get("/")
@login_required
def dashboard():
    require_management_access()
    selected_date = parse_date(request.args.get("date")) or date.today()
    deployments = [
        item for item in Deployment.query.filter_by(deployment_date=selected_date).order_by(Deployment.id).all()
        if is_valid_team_task(item)
    ]
    return render_template(
        "deployments/dashboard.html", team_tasks=build_team_tasks(deployments), selected_date=selected_date,
        deployment_status=deployment_status
    )


@deployments_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_deployment():
    require_operational_management_access()
    if request.method == "POST":
        validate_csrf()
        worker_ids = [worker_id for worker_id in request.form.getlist("worker_ids", type=int) if worker_id]
        if not worker_ids and request.form.get("worker_id", type=int):
            worker_ids = [request.form.get("worker_id", type=int)]
        workers = Worker.query.filter(Worker.id.in_(worker_ids), Worker.is_active.is_(True)).all() if worker_ids else []
        block_id = optional_int(request.form.get("block_id"))
        block = db.session.get(Block, block_id) if block_id else None
        locality_id = request.form.get("locality_id", "").strip()
        locality = db.session.get(Locality, optional_int(locality_id)) if locality_id else None
        scheduled_date = parse_date(request.form.get("deployment_date"))
        team_name = request.form.get("team_name", "").strip()
        if not workers or len(workers) != len(set(worker_ids)) or not block or not locality or not scheduled_date or not team_name:
            flash("Date, team name, active workers, block, and locality are required.", "error")
        elif locality and locality.block_id != block.id:
            flash("The locality does not belong to the selected block.", "error")
        elif not is_approved_location(block, locality):
            flash("Select a block and locality from the approved Hoshiarpur reference geography.", "error")
        elif existing_valid_team_name(team_name, scheduled_date):
            flash("That team name is already assigned for this date.", "error")
        elif workers_with_active_task(worker_ids, scheduled_date):
            flash("One or more selected workers already have a house-assigned team task for this date.", "error")
        else:
            task_filter = request.form.get("risk_filter", "all")
            houses = eligible_task_houses(block, locality, scheduled_date, task_filter)
            if not houses:
                flash("No eligible houses are available for this team task.", "error")
                return render_template(
                    "deployments/form.html", workers=active_workers(),
                    blocks=approved_blocks(), localities=approved_localities(),
                    high_risk_house_ids=high_risk_house_ids(), today=date.today(),
                )
            deployments = []
            for worker in sorted(workers, key=lambda item: item.official_worker_id):
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
                    team_name=team_name,
                    priority=request.form.get("priority", "normal"),
                    notes=request.form.get("notes", "").strip() or None,
                    assigned_by_user_id=current_user.id,
                )
                db.session.add(deployment)
                deployments.append(deployment)
            db.session.flush()
            for index, house in enumerate(houses):
                db.session.add(HouseAssignment(deployment=deployments[index % len(deployments)], house=house, priority="priority_recheck" if house.id in high_risk_house_ids(block, locality) else deployments[index % len(deployments)].priority))
            for deployment in deployments:
                log_change("create", "deployment", deployment.id, after={"date": str(scheduled_date), "worker_id": deployment.worker_id, "block_id": block.id, "risk_filter": task_filter})
            db.session.commit()
            flash(f"Task assigned to {len(deployments)} workers with {len(houses)} eligible houses distributed.", "success")
            return redirect(url_for("deployments.team_detail", deployment_id=deployments[0].id))
    return render_template(
        "deployments/form.html",
        workers=active_workers(),
        blocks=approved_blocks(), localities=approved_localities(),
        high_risk_house_ids=high_risk_house_ids(),
        today=date.today(),
    )


@deployments_bp.get("/eligible-house-count")
@login_required
def eligible_house_count():
    """Return current assignment capacity for the daily deployment form."""
    require_operational_management_access()
    block = db.session.get(Block, optional_int(request.args.get("block_id")))
    locality_id = request.args.get("locality_id", "").strip()
    locality = db.session.get(Locality, optional_int(locality_id)) if locality_id else None
    scheduled_date = parse_date(request.args.get("deployment_date"))
    if not block or not scheduled_date or (locality and locality.block_id != block.id):
        return jsonify(count=0)
    if not locality or not is_approved_location(block, locality):
        return jsonify(count=0)
    houses = eligible_task_houses(block, locality, scheduled_date, request.args.get("risk_filter", "all"))
    return jsonify(count=len(houses))


@deployments_bp.get("/<int:deployment_id>")
@login_required
def detail(deployment_id: int):
    deployment = db.get_or_404(Deployment, deployment_id)
    if not can_access_deployment(deployment):
        abort(403)
    assignments = HouseAssignment.query.filter_by(deployment_id=deployment.id).order_by(HouseAssignment.id).all()
    destinations = []
    if has_operational_management_access():
        destinations = Deployment.query.filter(
            Deployment.deployment_date == deployment.deployment_date,
            Deployment.id != deployment.id,
            Deployment.status.notin_(["completed", "cancelled"]),
        ).order_by(Deployment.id).all()
    return render_template(
        "deployments/detail.html", deployment=deployment, assignments=assignments,
        available_houses=available_houses(deployment, request.args.get("house_q", "").strip()),
        destinations=destinations, deployment_status=deployment_status,
        house_query=request.args.get("house_q", "").strip(), can_manage=has_operational_management_access(),
        house_statuses=house_statuses(assignment.house for assignment in assignments),
    )


@deployments_bp.get("/team/<int:deployment_id>")
@login_required
def team_detail(deployment_id: int):
    deployment = db.get_or_404(Deployment, deployment_id)
    members = team_deployments(deployment)
    if not has_operational_management_access() and not any(can_access_deployment(item) for item in members):
        abort(403)
    if not has_operational_management_access():
        members = [item for item in members if can_access_deployment(item)]
    return render_template(
        "deployments/team_detail.html", team=deployment, members=members,
        deployment_status=deployment_status, can_manage=has_operational_management_access(),
    )


@deployments_bp.post("/<int:deployment_id>/start")
@login_required
def start_deployment(deployment_id: int):
    validate_csrf()
    deployment = db.get_or_404(Deployment, deployment_id)
    if not can_access_deployment(deployment):
        abort(403)
    if current_user.role != "field_worker" and not has_operational_management_access():
        abort(403)
    owned_deployments = [item for item in team_deployments(deployment) if can_access_deployment(item)]
    deployable = [item for item in owned_deployments if item.house_assignments]
    if not deployable:
        flash("This task has no assigned houses and cannot be started.", "error")
    else:
        for item in deployable:
            if item.status == "assigned":
                item.status = "started"
                item.started_at = datetime.utcnow()
                log_change("start", "deployment", item.id, after={"status": "started"})
        db.session.commit()
    return redirect(url_for("deployments.mobile"))


@deployments_bp.post("/<int:deployment_id>/assignments")
@login_required
def add_assignment(deployment_id: int):
    require_operational_management_access()
    validate_csrf()
    deployment = db.get_or_404(Deployment, deployment_id)
    house = db.session.get(House, optional_int(request.form.get("house_id")))
    if deployment.status in {"completed", "cancelled"}:
        flash("Houses cannot be assigned to a completed or cancelled deployment.", "error")
    elif not house:
        flash("Select a valid operational house.", "error")
    elif deployment.locality_id and house.locality_id != deployment.locality_id:
        flash("The house does not belong to the deployment locality.", "error")
    elif HouseAssignment.query.filter_by(deployment_id=deployment.id, house_id=house.id).first():
        flash("That house is already assigned to this deployment.", "error")
    elif active_assignment_for_date(house.id, deployment.deployment_date, deployment.id):
        flash("That house already has an active assignment for this date.", "error")
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
    require_operational_management_access()
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


@deployments_bp.post("/<int:deployment_id>/manage")
@login_required
def manage_deployment(deployment_id: int):
    """Update only safe operational metadata without changing historical links."""
    require_operational_management_access()
    validate_csrf()
    deployment = db.get_or_404(Deployment, deployment_id)
    before = {"team_name": deployment.team_name, "priority": deployment.priority, "status": deployment.status}
    requested_status = request.form.get("status", deployment.status)
    if requested_status not in DEPLOYMENT_STATUSES or requested_status == "completed":
        flash("Completed status is set only when all assigned houses are completed.", "error")
    elif deployment.status == "completed":
        flash("Completed deployments cannot be changed.", "error")
    else:
        deployment.team_name = request.form.get("team_name", "").strip() or None
        deployment.priority = request.form.get("priority", deployment.priority)
        deployment.notes = request.form.get("notes", "").strip() or None
        deployment.status = requested_status
        if requested_status == "started" and deployment.started_at is None:
            deployment.started_at = datetime.utcnow()
        log_change("update", "deployment", deployment.id, before=before, after={"team_name": deployment.team_name, "priority": deployment.priority, "status": deployment.status})
        db.session.commit()
        flash("Deployment details updated.", "success")
    return redirect(url_for("deployments.detail", deployment_id=deployment.id))


@deployments_bp.get("/mobile")
@login_required
def mobile():
    deployments = Deployment.query.filter(
        Deployment.deployment_date == date.today(),
        current_user_deployment_filter(),
        Deployment.status != "cancelled",
    ).order_by(Deployment.id).all()
    deployments = [item for item in deployments if is_valid_team_task(item)]
    priority_rechecks = 0
    if deployments:
        priority_rechecks = ReinspectionTask.query.join(House).join(HouseAssignment).join(Deployment).filter(
            current_user_deployment_filter(),
            Deployment.deployment_date == date.today(),
            ReinspectionTask.status == "open",
        ).distinct().count()
    tasks = build_worker_team_tasks(deployments)
    statuses = house_statuses(assignment.house for task in tasks for assignment in task["assignments"])
    return render_template("deployments/mobile.html", tasks=tasks, priority_rechecks=priority_rechecks, today=date.today(), deployment_status=deployment_status, house_statuses=statuses)


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


def approved_blocks() -> list[Block]:
    names = [item[0] for item in REFERENCE_GEOGRAPHY]
    return Block.query.filter(Block.name.in_(names)).order_by(Block.name).all()


def approved_localities() -> list[Locality]:
    approved_pairs = {(block_name, locality_name) for block_name, _, names in REFERENCE_GEOGRAPHY for locality_name in names}
    return [
        locality for locality in Locality.query.join(Block).order_by(Block.name, Locality.name).all()
        if (locality.block.name, locality.name) in approved_pairs
    ]


def is_approved_location(block: Block | None, locality: Locality | None) -> bool:
    if not block or not locality:
        return False
    return any(block.name == block_name and locality.name in locality_names for block_name, _, locality_names in REFERENCE_GEOGRAPHY)


def is_valid_team_task(deployment: Deployment) -> bool:
    return bool(
        deployment.team_name
        and deployment.locality_id
        and deployment.house_assignments
        and is_approved_location(deployment.block, deployment.locality)
    )


def workers_with_active_task(worker_ids: list[int], deployment_date: date) -> bool:
    if not worker_ids:
        return False
    deployments = Deployment.query.filter(
        Deployment.deployment_date == deployment_date,
        Deployment.worker_id.in_(worker_ids),
        Deployment.status.notin_(["completed", "cancelled"]),
    ).all()
    return any(is_valid_team_task(item) for item in deployments)


def existing_valid_team_name(team_name: str, deployment_date: date) -> bool:
    deployments = Deployment.query.filter_by(
        deployment_date=deployment_date, team_name=team_name
    ).all()
    return any(is_valid_team_task(item) for item in deployments)


def team_deployments(deployment: Deployment) -> list[Deployment]:
    """Return the worker deployments that form one daily team task."""
    if not deployment.team_name:
        return [deployment]
    return Deployment.query.filter_by(
        deployment_date=deployment.deployment_date,
        team_name=deployment.team_name,
        block_id=deployment.block_id,
        locality_id=deployment.locality_id,
    ).order_by(Deployment.worker_code, Deployment.id).all()


def build_worker_team_tasks(deployments: list[Deployment]) -> list[dict]:
    """Present all of a worker's deployment rows for one team as a single field task."""
    groups = {}
    for deployment in deployments:
        key = (deployment.team_name, deployment.block_id, deployment.locality_id)
        groups.setdefault(key, []).append(deployment)
    tasks = []
    for own_deployments in groups.values():
        team_members = team_deployments(own_deployments[0])
        assignments = sorted(
            (assignment for item in own_deployments for assignment in item.house_assignments),
            key=lambda item: item.house.house_code,
        )
        member_codes = []
        for member in team_members:
            code = member.worker_code or member.worker_name
            if code not in member_codes:
                member_codes.append(code)
        completed = sum(assignment.completed_at is not None for assignment in assignments)
        status = "completed" if assignments and completed == len(assignments) else next(
            (deployment_status(item) for item in own_deployments if deployment_status(item) in {"in_progress", "started"}),
            deployment_status(own_deployments[0]),
        )
        tasks.append({
            "deployment": own_deployments[0], "assignments": assignments,
            "member_codes": ", ".join(member_codes), "target": len(assignments),
            "completed": completed, "pending": len(assignments) - completed, "status": status,
            "can_start": any(item.status == "assigned" for item in own_deployments),
        })
    return tasks


def build_team_tasks(deployments: list[Deployment]) -> list[dict]:
    """Group worker-level records for the operational dashboard only."""
    groups = {}
    for deployment in deployments:
        key = (
            deployment.team_name or f"Individual task {deployment.id}",
            deployment.block_id,
            deployment.locality_id,
        )
        groups.setdefault(key, []).append(deployment)
    tasks = []
    for members in groups.values():
        target = sum(len(item.house_assignments) for item in members)
        completed = sum(sum(assignment.completed_at is not None for assignment in item.house_assignments) for item in members)
        tasks.append({
            "deployment": members[0], "members": members, "target": target, "completed": completed,
            "pending": target - completed, "member_codes": ", ".join(item.worker_code or item.worker_name for item in members),
            "status": "completed" if target and completed == target else deployment_status(members[0]),
        })
    return tasks


def available_houses(deployment: Deployment, search: str) -> list[House]:
    """Return a small, deployment-compatible house selection result."""
    query = House.query.join(Locality).filter(House.is_active.is_(True))
    if deployment.locality_id:
        query = query.filter(House.locality_id == deployment.locality_id)
    else:
        query = query.filter(Locality.block_id == deployment.block_id)
    query = query.filter(~House.assignments.any(HouseAssignment.deployment_id == deployment.id))
    query = query.filter(~House.assignments.any(
        HouseAssignment.deployment.has(
            (Deployment.deployment_date == deployment.deployment_date)
            & (Deployment.status != "cancelled")
        )
    ))
    if not search:
        return []
    pattern = f"%{search}%"
    return query.filter((House.house_code.ilike(pattern)) | (House.address.ilike(pattern))).order_by(House.house_code).limit(100).all()


def eligible_task_houses(block: Block, locality: Locality | None, deployment_date: date, risk_filter: str) -> list[House]:
    query = House.query.join(Locality).filter(House.is_active.is_(True), Locality.block_id == block.id)
    if locality:
        query = query.filter(House.locality_id == locality.id)
    query = query.filter(~House.assignments.any(HouseAssignment.deployment.has(
        (Deployment.deployment_date == deployment_date) & (Deployment.status != "cancelled")
    )))
    houses = query.order_by(House.house_code).all()
    high_risk_ids = high_risk_house_ids(block, locality)
    if risk_filter == "high_risk":
        return [house for house in houses if house.id in high_risk_ids]
    if risk_filter == "normal":
        return [house for house in houses if house.id not in high_risk_ids]
    return houses


def high_risk_house_ids(block: Block | None = None, locality: Locality | None = None) -> set[int]:
    clusters = HistoricalHighRiskCluster.query.all()
    localities = Locality.query
    if block:
        localities = localities.filter_by(block_id=block.id)
    if locality:
        localities = localities.filter_by(id=locality.id)
    matched = set()
    for candidate in localities.all():
        for cluster in clusters:
            locality_matches = normalized_name(candidate.name) == normalized_name(cluster.locality_raw or "")
            block_matches = not cluster.block_raw or (
                normalized_name(candidate.block.name) == normalized_name(cluster.block_raw)
            )
            if locality_matches and block_matches:
                matched.update(house.id for house in candidate.houses)
                break
    return matched


def house_statuses(houses) -> dict[int, str]:
    statuses = {}
    for house in houses:
        status = "positive_revisit" if ReinspectionTask.query.filter_by(house_id=house.id, status="open").first() else "normal"
        statuses[house.id] = status
    return statuses


def normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def active_assignment_for_date(house_id: int, deployment_date: date, exclude_deployment_id: int | None = None) -> bool:
    query = HouseAssignment.query.join(Deployment).filter(
        HouseAssignment.house_id == house_id,
        Deployment.deployment_date == deployment_date,
        Deployment.status != "cancelled",
    )
    if exclude_deployment_id is not None:
        query = query.filter(HouseAssignment.deployment_id != exclude_deployment_id)
    return db.session.query(query.exists()).scalar()


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
