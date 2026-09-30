from datetime import date
from io import StringIO
import csv

from flask import Blueprint, Response, render_template, request
from flask_login import login_required

from .extensions import db
from .models import (
    Block, Deployment, HistoricalBlockReport, HistoricalDengueCase,
    HistoricalFieldResponse, HistoricalHighRiskCluster, House, HouseAssignment,
    HouseVisit, Locality, ReinspectionTask, SourceDocument,
)
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
    historical_source_id = request.args.get("historical_source_id", type=int)
    historical_block = request.args.get("historical_block", "").strip()
    historical_area_type = request.args.get("historical_area_type", "").strip()
    historical_cases = HistoricalDengueCase.query
    historical_reports = HistoricalBlockReport.query
    if historical_source_id:
        historical_cases = historical_cases.filter_by(source_document_id=historical_source_id)
        historical_reports = historical_reports.filter_by(source_document_id=historical_source_id)
    if historical_block:
        pattern = f"%{historical_block}%"
        historical_cases = historical_cases.filter(HistoricalDengueCase.block_raw.ilike(pattern))
        historical_reports = historical_reports.filter(HistoricalBlockReport.block_raw.ilike(pattern))
    if historical_area_type:
        historical_cases = historical_cases.filter(HistoricalDengueCase.rural_urban_raw.ilike(historical_area_type))
    report_metrics = aggregate_report_metrics(historical_reports.all())
    historical = {
        "cases": historical_cases.count(),
        "reports": historical_reports.count(),
        "responses": HistoricalFieldResponse.query.count(),
        "high_risk": HistoricalHighRiskCluster.query.count(),
        **report_metrics,
    }
    return render_template("monitoring/dashboard.html", selected_date=selected_date, deployments=deployments, blocks=Block.query.order_by(Block.name).all(), localities=Locality.query.order_by(Locality.name).all(), historical_sources=SourceDocument.query.order_by(SourceDocument.imported_at.desc()).all(), historical=historical, filters={"block_id": block_id, "locality_id": locality_id, "worker": worker_query, "status": status, "historical_source_id": historical_source_id, "historical_block": historical_block, "historical_area_type": historical_area_type}, metrics={"deployed": len(deployments), "assigned": assigned, "completed": completed, "pending": assigned-completed, "visits": len(visits), "positive": len(positives), "repeat_positive": repeat_positive, "reinspection": ReinspectionTask.query.filter_by(status="open").count(), "active": sum(item.status not in {"completed", "cancelled"} for item in deployments), "overdue": sum(item.deployment_date < date.today() and item.status not in {"completed", "cancelled"} for item in deployments)})


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
    from flask_login import current_user
    include_identifiers = current_user.role != "adc"
    writer.writerow(["Source file", "Source row", "Source serial", "Patient name", "Address", "Block", "Town", "Testing date (source)"] if include_identifiers else ["Source file", "Source row", "Block", "Town", "Testing date (source)"])
    for case in HistoricalDengueCase.query.order_by(HistoricalDengueCase.id).all():
        source_name = case.source_document.original_filename if hasattr(case, 'source_document') else ''
        writer.writerow([source_name, case.source_row, case.source_serial, case.patient_name, case.address_raw, case.block_raw, case.town_raw, case.testing_date_raw] if include_identifiers else [source_name, case.source_row, case.block_raw, case.town_raw, case.testing_date_raw])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=historical-dengue-cases.csv"})


@monitoring_bp.get("/reports/historical-vbd.csv")
@login_required
def historical_vbd_csv():
    require_management_access()
    return historical_json_csv(HistoricalBlockReport.query.order_by(HistoricalBlockReport.id), "historical-vbd-reporting.csv")


@monitoring_bp.get("/reports/historical-field-responses.csv")
@login_required
def historical_field_responses_csv():
    require_management_access()
    return historical_json_csv(HistoricalFieldResponse.query.order_by(HistoricalFieldResponse.id), "historical-field-responses.csv")


@monitoring_bp.get("/reports/high-risk-areas.csv")
@login_required
def high_risk_areas_csv():
    require_management_access()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Source file", "Source row", "Area type", "Block", "Locality", "Positive case count"])
    for item in HistoricalHighRiskCluster.query.order_by(HistoricalHighRiskCluster.id).all():
        source = db.session.get(SourceDocument, item.source_document_id)
        writer.writerow([source.original_filename if source else "", item.source_row, item.area_type_raw, item.block_raw, item.locality_raw, item.positive_case_count])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=high-risk-areas.csv"})


def historical_json_csv(query, filename: str):
    output = StringIO()
    writer = csv.writer(output)
    records = query.all()
    headers = ["Source file", "Source sheet", "Source row", "Block"]
    value_headers = sorted({key for record in records for key in record.values})
    writer.writerow(headers + value_headers)
    for record in records:
        source = db.session.get(SourceDocument, record.source_document_id)
        writer.writerow([
            source.original_filename if source else "",
            record.source_sheet,
            record.source_row,
            record.block_raw,
        ] + [record.values.get(key, "") for key in value_headers])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


def aggregate_report_metrics(records):
    metrics = {
        "samples_tested": 0,
        "positive_cases": 0,
        "houses_checked": 0,
        "houses_positive": 0,
        "containers_checked": 0,
        "containers_positive": 0,
    }
    labels = {
        "denguesamplestestedweek": "samples_tested",
        "denguepositiveweek": "positive_cases",
        "houseschecked": "houses_checked",
        "housespositiveforlarvae": "houses_positive",
        "containerschecked": "containers_checked",
        "containerspositiveforlarvae": "containers_positive",
    }
    for record in records:
        for label, value in record.values.items():
            key = "".join(character for character in label.casefold() if character.isalnum())
            metric = labels.get(key)
            if metric and isinstance(value, (int, float)):
                metrics[metric] += value
    metrics["positivity_rate"] = (
        round(metrics["positive_cases"] * 100 / metrics["samples_tested"], 2)
        if metrics["samples_tested"] else None
    )
    return metrics


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
