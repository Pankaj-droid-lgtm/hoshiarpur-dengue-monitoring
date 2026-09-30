from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from .deployments import can_access_deployment
from .extensions import db
from .models import HouseAssignment, HouseVisit, LarvalObservation, Photo, ReinspectionTask
from .security import validate_csrf
from .services.audit import log_change


visits_bp = Blueprint("visits", __name__, url_prefix="/visits")
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@visits_bp.route("/assignments/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def inspect_house(assignment_id: int):
    assignment = db.get_or_404(HouseAssignment, assignment_id)
    if not can_access_deployment(assignment.deployment):
        abort(403)
    previous_positive = HouseVisit.query.filter_by(house_id=assignment.house_id).filter(HouseVisit.positive_containers > 0).order_by(HouseVisit.visited_at.desc()).first()
    if request.method == "POST":
        validate_csrf()
        positives = number(request.form.get("positive_containers"))
        visit = HouseVisit(
            house=assignment.house,
            deployment=assignment.deployment,
            assignment=assignment,
            worker_id=assignment.deployment.worker_id,
            worker_name_snapshot=assignment.deployment.worker_name,
            visited_at=datetime.utcnow(),
            visit_outcome=request.form.get("visit_outcome", "completed"),
            containers_checked=number(request.form.get("containers_checked")),
            positive_containers=positives,
            source_reduction_done=request.form.get("source_reduction_done") == "yes",
            larvicide_used=request.form.get("larvicide_used") == "yes",
            remarks=request.form.get("remarks", "").strip() or None,
        )
        db.session.add(visit)
        db.session.flush()
        for container_type, larvae in zip(request.form.getlist("container_type"), request.form.getlist("larvae_found")):
            if container_type.strip():
                db.session.add(LarvalObservation(visit=visit, container_type=container_type.strip(), larvae_found=larvae == "yes"))
        save_photo(visit)
        assignment.completed_at = visit.visited_at
        update_deployment_completion(assignment.deployment, visit.visited_at)
        if positives:
            task = ReinspectionTask(house_id=assignment.house_id, origin_visit=visit, due_date=(visit.visited_at + timedelta(days=7)).date(), reason="Larval-positive house inspection")
            db.session.add(task)
        else:
            ReinspectionTask.query.filter_by(house_id=assignment.house_id, status="open").update({"status": "completed"})
        log_change("submit_visit", "house_visit", visit.id, after={"house_id": visit.house_id, "positive_containers": positives})
        db.session.commit()
        flash("Visit recorded. Previous inspections remain unchanged.", "success")
        return redirect(url_for("deployments.mobile_visits"))
    return render_template("visits/inspect.html", assignment=assignment, previous_positive=previous_positive)


@visits_bp.get("/photos/<int:photo_id>")
@login_required
def photo_file(photo_id: int):
    photo = db.get_or_404(Photo, photo_id)
    if not can_access_deployment(photo.visit.deployment):
        abort(403)
    if Path(photo.storage_key).name != photo.storage_key:
        abort(404)
    return send_from_directory(upload_directory(), photo.storage_key)


def update_deployment_completion(deployment, completed_at: datetime) -> None:
    """Update progress from completed assignments without altering cancelled work."""
    assignments = deployment.house_assignments
    if not assignments or deployment.status == "cancelled":
        return
    if all(assignment.completed_at is not None for assignment in assignments):
        deployment.status = "completed"
        deployment.completed_at = completed_at
    elif deployment.status in {"assigned", "started"}:
        deployment.status = "in_progress"


def save_photo(visit: HouseVisit) -> None:
    upload = request.files.get("photo")
    if not upload or not upload.filename:
        return
    extension = Path(secure_filename(upload.filename)).suffix.lower()
    if (
        upload.mimetype not in ALLOWED_PHOTO_TYPES
        or extension not in ALLOWED_EXTENSIONS
        or not matches_image_signature(upload, extension)
    ):
        abort(400)
    filename = f"{uuid4().hex}{extension}"
    upload_directory().mkdir(parents=True, exist_ok=True)
    upload.save(upload_directory() / filename)
    db.session.add(Photo(visit=visit, storage_key=filename, content_type=upload.mimetype, uploaded_by_user_id=current_user.id))


def upload_directory() -> Path:
    return Path(current_app.config["UPLOAD_DIRECTORY"]).resolve()


def matches_image_signature(upload, extension: str) -> bool:
    signature = upload.stream.read(16)
    upload.stream.seek(0)
    if extension == ".png":
        return signature.startswith(b"\x89PNG\r\n\x1a\n")
    return signature.startswith(b"\xff\xd8\xff")


def number(value: str | None) -> int:
    try:
        return max(int(value or 0), 0)
    except ValueError:
        return 0
