from datetime import date
from io import StringIO
import csv

from flask import Blueprint, Response, render_template, request
from flask_login import login_required

from .extensions import db
from .models import Block, Deployment, HistoricalDengueCase, House, HouseAssignment, HouseVisit, Locality, ReinspectionTask
from .services.permissions import require_management_access


monitoring_bp = Blueprint("monitoring", __name__, url_prefix="/monitoring")


@monitoring_bp.get("/")
@login_required
def dashboard():
    require_management_access()
    selected_date = parse_date(request.args.get("date")) or date.today()
    query = Deployment.query.filter_by(deployment_date=selected_date)
    block_id = request.args.get("block_id", type=int)
    locality_id = request.args.get("locality_id", type=int)
    worker_query = request.args.get("worker", "").strip()
    status = request.args.get("status", "").strip()
    if block_id:
        query = query.filter_by(block_id=block_id)
    if locality_id:
        query = query.filter_by(locality_id=locality_id)
    if worker_query:
        query = query.filter(Deployment.worker_name.ilike(f"%{worker_query}%"))
    if status:
        query = query.filter_by(status=status)
    deployments = query.all()
    assignment_ids = [assignment.id for deployment in deployments for assignment in deployment.house_assignments]
    assigned = len(assignment_ids)
    completed = sum(1 for deployment in deployments for assignment in deployment.house_assignments if assignment.completed_at)
    visits = HouseVisit.query.filter(db.func.date(HouseVisit.visited_at) == selected_date.isoformat()).all()
    positives = [visit for visit in visits if visit.positive_containers > 0]
    repeat_positive = sum(1 for visit in positives if HouseVisit.query.filter(HouseVisit.house_id == visit.house_id, HouseVisit.id != visit.id, HouseVisit.positive_containers > 0).first())
    return render_template("monitoring/dashboard.html", selected_date=selected_date, deployments=deployments, blocks=Block.query.order_by(Block.name).all(), localities=Locality.query.order_by(Locality.name).all(), filters={"block_id": block_id, "locality_id": locality_id, "worker": worker_query, "status": status}, metrics={"deployed": len(deployments), "assigned": assigned, "completed": completed, "pending": assigned-completed, "visits": len(visits), "positive": len(positives), "repeat_positive": repeat_positive, "reinspection": ReinspectionTask.query.filter_by(status="open").count(), "active": sum(item.status not in {"completed", "cancelled"} for item in deployments), "overdue": sum(item.deployment_date < date.today() and item.status not in {"completed", "cancelled"} for item in deployments)})


@monitoring_bp.get("/reports/deployments.csv")
@login_required
def deployment_csv():
    require_management_access()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Worker", "Worker ID", "Block", "Locality", "Target", "Completed", "Status"])
    for deployment in Deployment.query.order_by(Deployment.deployment_date.desc()).all():
        assignments = deployment.house_assignments
        writer.writerow([deployment.deployment_date, deployment.worker_name, deployment.worker_code or "", deployment.block.name, deployment.locality.name if deployment.locality else "", len(assignments), sum(item.completed_at is not None for item in assignments), deployment.status])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=daily-deployments.csv"})


@monitoring_bp.get("/reports/visits.csv")
@login_required
def visits_csv():
    require_management_access()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Visit time", "Worker", "House ID", "Block", "Locality", "Containers checked", "Positive containers", "Outcome"])
    for visit in HouseVisit.query.order_by(HouseVisit.visited_at.desc()).all():
        writer.writerow([visit.visited_at, visit.worker_name_snapshot, visit.house.house_code, visit.house.locality.block.name, visit.house.locality.name, visit.containers_checked, visit.positive_containers, visit.visit_outcome])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=house-visits.csv"})


@monitoring_bp.get("/reports/historical-cases.csv")
@login_required
def historical_cases_csv():
    require_management_access()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Source file", "Source row", "Source serial", "Patient name", "Address", "Block", "Town", "Testing date (source)"])
    for case in HistoricalDengueCase.query.order_by(HistoricalDengueCase.id).all():
        writer.writerow([case.source_document.original_filename if hasattr(case, 'source_document') else '', case.source_row, case.source_serial, case.patient_name, case.address_raw, case.block_raw, case.town_raw, case.testing_date_raw])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=historical-dengue-cases.csv"})


@monitoring_bp.get("/map")
@login_required
def map_view():
    require_management_access()
    markers = []
    for house in House.query.filter(House.latitude.isnot(None), House.longitude.isnot(None)).all():
        last_visit = HouseVisit.query.filter_by(house_id=house.id).order_by(HouseVisit.visited_at.desc()).first()
        has_previous_positive = HouseVisit.query.filter_by(house_id=house.id).filter(HouseVisit.positive_containers > 0).first()
        status = "yellow" if not last_visit else "red" if last_visit.positive_containers > 0 else "orange" if has_previous_positive else "green"
        markers.append({"latitude": house.latitude, "longitude": house.longitude, "house_code": house.house_code, "locality": house.locality.name, "last_visit": last_visit.visited_at.strftime("%d-%m-%Y") if last_visit else "No visit", "status": status})
    return render_template("monitoring/map.html", markers=markers)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
