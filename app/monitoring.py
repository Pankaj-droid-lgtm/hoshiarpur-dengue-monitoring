from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4

from flask import Blueprint, Response, render_template, request
from flask_login import login_required
from openpyxl import Workbook

from .extensions import db
from .models import (
    Block, Deployment, HistoricalBlockReport, HistoricalDengueCase,
    HistoricalFieldResponse, HistoricalHighRiskCluster, House, HouseAssignment,
    HouseVisit, Locality, ReinspectionTask, SourceDocument, Worker,
)
from .services.permissions import require_management_access, has_operational_management_access
from .services.reference_geography import REFERENCE_GEOGRAPHY, area_type_for_block


monitoring_bp = Blueprint("monitoring", __name__, url_prefix="/monitoring")


@monitoring_bp.get("/")
@login_required
def dashboard():
    require_management_access()
    selected_date = parse_date(request.args.get("date")) or date.today()

    # Get filter parameters from request
    block_id = request.args.get("block_id", type=int)
    locality_id = request.args.get("locality_id", type=int)
    worker_query = request.args.get("worker", "").strip()
    status = request.args.get("status", "").strip()

    # Build drill-down data: block -> locality -> MPHW -> checker -> house -> visit history
    area_drilldown = []
    for block in Block.query.order_by(Block.name).all():
        for locality in Locality.query.filter_by(block_id=block.id).order_by(Locality.name).all():
            mpw_name = None
            # Find the associated MPHW from assigned workers
            checkers = Worker.query.filter_by(
                mphw_name=locality.name,
                is_active=True
            ).all()
            checker_count = len(checkers)
            houses_inspected = sum(
                1 for h in house.assignments
                for v in house.visits
                if v.visited_at.date() == selected_date.isoformat()
            ) if checkers else 0

            # Larva positive count
            larva_positive = 0
            if checkers:
                for checker in checkers:
                    for visit in checker.visits:
                        if visit.visited_at.date() == selected_date.isoformat():
                            larva_positive += max(0, visit.positive_containers)
                            break

            # Get active alerts
            alerts = []
            for checker in checkers:
                for assignment in checker.deployments:
                    if assignment.status not in {"completed", "cancelled"}:
                        alerts.append({
                            "type": "reinspection_pending",
                            "area": checker.mphw_name or checker.official_worker_id,
                            "detail": f"Reinspection due in 7 days for house with positive containers",
                            "due": "7 days"
                        })

            area_drilldown.append({
                "block_id": block.id,
                "locality_id": locality.id,
                "block": block.name,
                "locality": locality.name,
                "mphw": locality.name,
                "checker_count": checker_count,
                "houses_inspected": houses_inspected,
                "larva_positive": larva_positive,
                "alerts": alerts,
            })

    # Operational metrics from live data
    deployments = Deployment.query.filter_by(deployment_date=selected_date).all()
    assignment_ids = [assignment.id for deployment in deployments for assignment in deployment.house_assignments]
    assigned = len(assignment_ids)
    completed = sum(1 for deployment in deployments for assignment in deployment.house_assignments if assignment.completed_at)

    # Visits today
    visits = HouseVisit.query.filter(db.func.date(HouseVisit.visited_at) == selected_date.isoformat()).all()
    visits_completed_today = len(visits)
    positives = [visit for visit in visits if visit.positive_containers > 0]
    larva_positive_today = len(positives)

    # Area coverage
    areas_covered_today = len(set(visit.house.locality_id for visit in visits))

    # Reinspection pending
    reinspection_pending = ReinspectionTask.query.filter_by(status="open").count()

    # Alert items - based on real data
    alert_items = []
    # Check for houses with recent positive that need reinspection
    for visit in visits:
        if visit.positive_containers > 0:
            last_positive = HouseVisit.query.filter(
                HouseVisit.house_id == visit.house_id,
                HouseVisit.visited_at < visit.visited_at,
                HouseVisit.positive_containers > 0
            ).first()
            if not last_positive:
                alerts.append({
                    "type": "larva_positive",
                    "area": visit.house.locality.block.name if visit.house.locality else "Unknown",
                    "detail": f"House {visit.house.house_code} has positive containers, requires reinspection",
                    "due": "7 days"
                })

    active = sum(item.status not in {"completed", "cancelled"} for item in deployments)
    overdue = sum(item.deployment_date < date.today() and item.status not in {"completed", "cancelled"} for item in deployments)

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

    # Area/drilldown data - using existing reference geography
    area_drilldown_data = []
    for block_name, area_type, locality_names in REFERENCE_GEOGRAPHY:
        block = Block.query.filter_by(name=block_name).first()
        if not block:
            continue
        for locality_name in locality_names:
            locality = Locality.query.filter_by(block_id=block.id, name=locality_name).first()
            if not locality:
                continue
            # Count active checkers with this locality
            checkers = Worker.query.filter(
                Worker.mphw_name == locality.name,
                Worker.is_active == True
            ).all()
            houses = 0
            larva_pos = 0
            if checkers:
                for checker in checkers:
                    if checker.deployments:
                        for h in checker.deployments[0].house_assignments:
                            houses += 1
                    for visit in checker.visits:
                        if visit.visited_at and visit.visited_at.date() == selected_date.isoformat():
                            larva_pos += max(0, visit.positive_containers)

            area_drilldown_data.append({
                "block_id": block.id,
                "locality_id": locality.id,
                "block": block.name,
                "locality": locality.name,
                "mphw": locality.name,
                "checker_count": len(checkers),
                "houses_inspected": 0,  # Will be filled from actual data
                "larva_positive": larva_pos,
                "alerts": [],
            })

    # ---- Chart data for last 30 days ----
    end_date = selected_date
    start_date = end_date - timedelta(days=29)
    visits_30d = HouseVisit.query.filter(
        HouseVisit.visited_at >= start_date,
        HouseVisit.visited_at <= end_date + timedelta(days=1)
    ).all()

    # Visits per day (last 30 days)
    visits_by_date = defaultdict(int)
    larva_by_date = defaultdict(lambda: {"positive": 0, "negative": 0})
    for visit in visits_30d:
        d = visit.visited_at.date()
        visits_by_date[d.isoformat()] += 1
        if visit.positive_containers > 0:
            larva_by_date[d.isoformat()]["positive"] += 1
        else:
            larva_by_date[d.isoformat()]["negative"] += 1

    date_labels = [(start_date + timedelta(days=i)).isoformat() for i in range(30)]
    visits_series = [visits_by_date.get(d, 0) for d in date_labels]
    larva_pos_series = [larva_by_date.get(d, {}).get("positive", 0) for d in date_labels]
    larva_neg_series = [larva_by_date.get(d, {}).get("negative", 0) for d in date_labels]

    # Area/Block coverage
    area_visits = defaultdict(int)
    for visit in visits_30d:
        block_name = visit.house.locality.block.name if visit.house and visit.house.locality and visit.house.locality.block else "Unknown"
        area_visits[block_name] += 1
    area_labels = sorted(area_visits.keys())
    area_series = [area_visits[name] for name in area_labels]

    # Checker activity
    checker_visits = defaultdict(int)
    for visit in visits_30d:
        checker_name = visit.worker_name_snapshot
        checker_visits[checker_name] += 1
    top_checkers = sorted(checker_visits.items(), key=lambda x: x[1], reverse=True)[:10]
    checker_labels = [name for name, _ in top_checkers]
    checker_series = [count for _, count in top_checkers]

    return render_template("monitoring/dashboard.html", selected_date=selected_date,
        deployments=deployments, blocks=Block.query.order_by(Block.name).all(),
        localities=Locality.query.order_by(Locality.name).all(),
        historical_sources=SourceDocument.query.order_by(SourceDocument.imported_at.desc()).all(),
        historical=historical, filters={"block_id": block_id, "locality_id": locality_id,
            "worker": worker_query, "status": status, "historical_source_id": historical_source_id,
            "historical_block": historical_block, "historical_area_type": historical_area_type},
        metrics={"deployed": len(deployments), "assigned": assigned, "completed": completed,
            "pending": assigned-completed, "visits": visits_completed_today, "positive": larva_positive_today,
            "reinspection": reinspection_pending, "active": active, "overdue": overdue,
            "area_drilldown": area_drilldown_data, "alerts": alert_items},
        chart_visits_labels=date_labels, chart_visits_data=visits_series,
        chart_larva_pos_data=larva_pos_series, chart_larva_neg_data=larva_neg_series,
        chart_area_labels=area_labels, chart_area_data=area_series,
        chart_checker_labels=checker_labels, chart_checker_data=checker_series)


@monitoring_bp.get("/area/<int:block_id>/<int:locality_id>")
@login_required
def area_detail(block_id: int, locality_id: int):
    """Drill-down: Block → Locality → MPHW → Checker → House → Visit History"""
    require_management_access()
    block = db.get_or_404(Block, block_id)
    locality = db.get_or_404(Locality, locality_id)

    # Get MPHW name for this locality
    mpw_name = locality.name

    # Get checkers assigned to this locality
    checkers = Worker.query.filter(
        Worker.mphw_name == locality.name,
        Worker.is_active == True
    ).all()

    checker_data = []
    for checker in checkers:
        # Get houses assigned to this checker
        houses = House.query.join(HouseAssignment).join(Deployment).filter(
            Deployment.worker_id == checker.id,
            Deployment.status.notin_(["completed", "cancelled"])
        ).distinct().all()

        house_data = []
        for house in houses:
            # Get visit history for this house
            visits = HouseVisit.query.filter_by(house_id=house.id).order_by(HouseVisit.visited_at.desc()).all()
            visit_data = []
            for visit in visits:
                visit_data.append({
                    "visited_at": visit.visited_at.strftime("%d-%m-%Y %H:%M"),
                    "worker": visit.worker_name_snapshot,
                    "outcome": visit.visit_outcome,
                    "containers_checked": visit.containers_checked,
                    "positive_containers": visit.positive_containers,
                    "source_reduction": visit.source_reduction_done,
                    "larvicide": visit.larvicide_used,
                    "remarks": visit.remarks,
                })

            # Get latest larva status
            last_visit = visits[0] if visits else None
            larva_status = "unknown"
            if last_visit:
                larva_status = "positive" if last_visit.positive_containers > 0 else "negative"

            house_data.append({
                "house_code": house.house_code,
                "house_number": house.house_number,
                "address": house.address,
                "household_member": house.household_member_name,
                "mphw": checker.mphw_name,
                "checker": checker.full_name,
                "checker_id": checker.official_worker_id,
                "larva_status": larva_status,
                "last_visit": last_visit.visited_at.strftime("%d-%m-%Y %H:%M") if last_visit else "Never",
                "visits": visit_data,
            })

        checker_data.append({
            "checker_id": checker.official_worker_id,
            "checker_name": checker.full_name,
            "phone": checker.phone_number,
            "mphw": checker.mphw_name,
            "houses": house_data,
        })

    return render_template("monitoring/area_detail.html",
        block=block, locality=locality, mpw_name=mpw_name,
        checker_data=checker_data)


@monitoring_bp.get("/reports/deployments.xlsx")
@login_required
def deployment_csv():
    require_management_access()
    rows = [["Date", "Worker", "Worker ID", "Block", "Locality", "Target", "Completed", "Status"]]
    for deployment in Deployment.query.order_by(Deployment.deployment_date.desc()).all():
        assignments = deployment.house_assignments
        rows.append([
            deployment.deployment_date,
            deployment.worker_name,
            deployment.worker_code or "",
            deployment.block.name,
            deployment.locality.name if deployment.locality else "",
            len(assignments),
            sum(item.completed_at is not None for item in assignments),
            deployment.status,
        ])
    return xlsx_response(rows, "daily-deployments.xlsx")


@monitoring_bp.get("/reports/visits.xlsx")
@login_required
def visits_csv():
    require_management_access()
    rows = [["Visit time", "Worker", "House ID", "Block", "Locality", "Containers checked", "Positive containers", "Outcome"]]
    for visit in HouseVisit.query.order_by(HouseVisit.visited_at.desc()).all():
        rows.append([
            visit.visited_at,
            visit.worker_name_snapshot,
            visit.house.house_code,
            visit.house.locality.block.name,
            visit.house.locality.name,
            visit.containers_checked,
            visit.positive_containers,
            visit.visit_outcome,
        ])
    return xlsx_response(rows, "house-visits.xlsx")


@monitoring_bp.get("/reports/historical-cases.xlsx")
@login_required
def historical_cases_csv():
    require_management_access()
    from flask_login import current_user
    include_identifiers = current_user.role != "adc"

    if include_identifiers:
        headers = ["Source file", "Source row", "Source serial", "Patient name", "Address", "Block", "Town", "Testing date (source)"]
    else:
        headers = ["Source file", "Source row", "Block", "Town", "Testing date (source)"]

    rows = [headers]

    for case in HistoricalDengueCase.query.order_by(HistoricalDengueCase.id).all():
        source_name = case.source_document.original_filename if hasattr(case, "source_document") else ""

        if include_identifiers:
            rows.append([
                source_name,
                case.source_row,
                case.source_serial,
                case.patient_name,
                case.address_raw,
                case.block_raw,
                case.town_raw,
                case.testing_date_raw,
            ])
        else:
            rows.append([
                source_name,
                case.source_row,
                case.block_raw,
                case.town_raw,
                case.testing_date_raw,
            ])

    return xlsx_response(rows, "historical-dengue-cases.xlsx")


@monitoring_bp.get("/reports/historical-vbd.xlsx")
@login_required
def historical_vbd_csv():
    require_management_access()
    return historical_xlsx(
        HistoricalBlockReport.query.order_by(HistoricalBlockReport.id),
        "historical-vbd-reporting.xlsx",
    )


@monitoring_bp.get("/reports/historical-field-responses.xlsx")
@login_required
def historical_field_responses_csv():
    require_management_access()
    return historical_xlsx(
        HistoricalFieldResponse.query.order_by(HistoricalFieldResponse.id),
        "historical-field-responses.xlsx",
    )


@monitoring_bp.get("/reports/high-risk-areas.xlsx")
@login_required
def high_risk_areas_csv():
    require_management_access()
    rows = [["Source file", "Source row", "Area type", "Block", "Locality", "Positive case count"]]

    for item in HistoricalHighRiskCluster.query.order_by(HistoricalHighRiskCluster.id).all():
        source = db.session.get(SourceDocument, item.source_document_id)
        rows.append([
            source.original_filename if source else "",
            item.source_row,
            item.area_type_raw,
            item.block_raw,
            item.locality_raw,
            item.positive_case_count,
        ])

    return xlsx_response(rows, "high-risk-areas.xlsx")


def xlsx_response(rows, filename):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Report"

    for row in rows:
        worksheet.append(row)

    if rows:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def historical_xlsx(query, filename):
    records = query.all()
    headers = ["Source file", "Source sheet", "Source row", "Block"]
    value_headers = sorted({key for record in records for key in record.values})

    rows = [headers + value_headers]

    for record in records:
        source = db.session.get(SourceDocument, record.source_document_id)
        rows.append([
            source.original_filename if source else "",
            record.source_sheet,
            record.source_row,
            record.block_raw,
        ] + [record.values.get(key, "") for key in value_headers])

    return xlsx_response(rows, filename)


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
    # Map view is now GPS-disabled; show houses with their last visit status
    # Using a simple list without leaflet maps
    houses = House.query.filter(House.is_active.is_(True)).all()
    marker_data = []
    for house in houses:
        last_visit = HouseVisit.query.filter_by(house_id=house.id).order_by(HouseVisit.visited_at.desc()).first()
        has_previous_positive = HouseVisit.query.filter_by(house_id=house.id).filter(HouseVisit.positive_containers > 0).first()
        status = "yellow" if not last_visit else "red" if last_visit.positive_containers > 0 else "orange" if has_previous_positive else "green"
        marker_data.append({
            "house_code": house.house_code,
            "locality": house.locality.name,
            "status": status,
            "last_visit": last_visit.visited_at.strftime("%d-%m-%Y") if last_visit else "No visit",
        })
    return render_template("monitoring/map.html", markers=marker_data)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None