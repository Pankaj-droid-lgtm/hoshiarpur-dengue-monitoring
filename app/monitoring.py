from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from time import monotonic
from uuid import uuid4

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required
from openpyxl import Workbook
from sqlalchemy import case, true

from .extensions import db
from .models import (
    Block, Deployment, HistoricalBlockReport, HistoricalDengueCase,
    HistoricalFieldResponse, HistoricalHighRiskCluster, House, HouseAssignment,
    HouseVisit, Locality, ReinspectionTask, SourceDocument, Worker,
)
from .services.permissions import require_management_access, has_operational_management_access
from .services.reference_geography import REFERENCE_GEOGRAPHY, area_type_for_block


monitoring_bp = Blueprint("monitoring", __name__, url_prefix="/monitoring")
DASHBOARD_CONFIG = {
    "high_positivity_percent": 10,
    "low_visits_today": 1,
    "cache_seconds": 45,
}
HIGH_POSITIVITY_THRESHOLD = DASHBOARD_CONFIG["high_positivity_percent"]
_dashboard_cache: dict[tuple[str, str], tuple[float, dict]] = {}


@monitoring_bp.get("/")
@login_required
def dashboard():
    require_management_access()
    return render_template(
        "monitoring/dashboard.html",
        blocks=Block.query.order_by(Block.name).all(),
        high_positivity_threshold=HIGH_POSITIVITY_THRESHOLD,
    )


@monitoring_bp.get("/api/options")
@login_required
def filter_options():
    require_management_access()
    block_id = request.args.get("block_id", type=int)
    locality_id = request.args.get("locality_id", type=int)
    area_type = request.args.get("area_type", "").strip().lower()
    localities_query = Locality.query.join(Block).filter(
        Locality.block_id == block_id if block_id else true()
    )
    if area_type == "rural":
        localities_query = localities_query.filter(Block.is_urban.is_(False))
    elif area_type == "urban":
        localities_query = localities_query.filter(Block.is_urban.is_(True))
    localities = localities_query.order_by(Locality.name).all()
    workers_query = Worker.query.join(Deployment, Deployment.worker_id == Worker.id).join(Block, Deployment.block_id == Block.id).filter(
        Worker.is_active.is_(True),
        Deployment.block_id == block_id if block_id else true(),
        Deployment.locality_id == locality_id if locality_id else true(),
    )
    if area_type == "rural":
        workers_query = workers_query.filter(Block.is_urban.is_(False))
    elif area_type == "urban":
        workers_query = workers_query.filter(Block.is_urban.is_(True))
    workers = workers_query.distinct().order_by(Worker.full_name).all()
    return jsonify({
        "localities": [{"id": item.id, "name": item.name} for item in localities],
        "workers": [{"id": item.id, "name": item.full_name, "code": item.official_worker_id} for item in workers],
    })


@monitoring_bp.get("/api/summary")
@login_required
def summary_api():
    require_management_access()
    filters = dashboard_filters()
    start_at = datetime.combine(filters["date_from"], datetime.min.time())
    end_at = datetime.combine(filters["date_to"] + timedelta(days=1), datetime.min.time())
    previous_start = start_at - (end_at - start_at)
    previous_end = start_at

    def visit_query(from_at, to_at):
        query = HouseVisit.query.join(House).join(Locality).join(Block).filter(
            HouseVisit.visited_at >= from_at, HouseVisit.visited_at < to_at
        )
        if filters["block_id"]:
            query = query.filter(Block.id == filters["block_id"])
        if filters["locality_id"]:
            query = query.filter(Locality.id == filters["locality_id"])
        if filters["worker_id"]:
            query = query.filter(HouseVisit.worker_id == filters["worker_id"])
        if filters["larval_status"] == "positive":
            query = query.filter(HouseVisit.positive_containers > 0)
        elif filters["larval_status"] == "negative":
            query = query.filter(HouseVisit.positive_containers == 0)
        return query

    current_visits = visit_query(start_at, end_at)
    previous_visits = visit_query(previous_start, previous_end)
    positive_case = case((HouseVisit.positive_containers > 0, 1), else_=0)
    visit_total = current_visits.with_entities(db.func.count(HouseVisit.id)).scalar() or 0
    positive_total = current_visits.with_entities(db.func.coalesce(db.func.sum(positive_case), 0)).scalar() or 0
    previous_total = previous_visits.with_entities(db.func.count(HouseVisit.id)).scalar() or 0
    previous_positive = previous_visits.with_entities(db.func.coalesce(db.func.sum(positive_case), 0)).scalar() or 0
    deployed = deployment_count(filters, filters["date_from"], filters["date_to"])
    previous_deployed = deployment_count(filters, previous_start.date(), (previous_end - timedelta(days=1)).date())
    pending, overdue, completed_reinspections = reinspection_counts(filters)
    positivity = round(positive_total * 100 / visit_total, 1) if visit_total else 0
    previous_positivity = round(previous_positive * 100 / previous_total, 1) if previous_total else 0

    day_rows = current_visits.with_entities(
        db.func.date(HouseVisit.visited_at).label("day"), db.func.count(HouseVisit.id),
        db.func.coalesce(db.func.sum(positive_case), 0),
    ).group_by(db.func.date(HouseVisit.visited_at)).order_by(db.func.date(HouseVisit.visited_at)).all()
    days = [(filters["date_from"] + timedelta(days=offset)).isoformat() for offset in range((filters["date_to"] - filters["date_from"]).days + 1)]
    day_map = {str(row[0]): {"visits": row[1], "positive": row[2]} for row in day_rows}

    block_rows = current_visits.with_entities(
        Block.id, Block.name, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive_case), 0),
    ).group_by(Block.id, Block.name).order_by(db.func.count(HouseVisit.id).desc()).all()
    locality_rows = current_visits.with_entities(
        Block.id, Locality.id, Locality.name, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive_case), 0),
    ).group_by(Block.id, Locality.id, Locality.name).having(db.func.count(HouseVisit.id) > 0).order_by(
        (db.func.sum(positive_case) * 100.0 / db.func.count(HouseVisit.id)).desc()
    ).limit(10).all()
    checker_rows = current_visits.outerjoin(Worker, Worker.id == HouseVisit.worker_id).with_entities(
        HouseVisit.worker_id, db.func.coalesce(Worker.full_name, HouseVisit.worker_name_snapshot), db.func.count(HouseVisit.id),
        db.func.coalesce(db.func.sum(positive_case), 0),
    ).group_by(HouseVisit.worker_id, Worker.full_name, HouseVisit.worker_name_snapshot).all()
    deployment_table = Deployment.query.join(Block).outerjoin(Locality).outerjoin(Worker, Worker.id == Deployment.worker_id).outerjoin(
        HouseVisit, db.and_(HouseVisit.deployment_id == Deployment.id, HouseVisit.visited_at >= start_at, HouseVisit.visited_at < end_at)
    ).outerjoin(ReinspectionTask, ReinspectionTask.origin_visit_id == HouseVisit.id).filter(
        Deployment.deployment_date.between(filters["date_from"], filters["date_to"])
    )
    if filters["block_id"]:
        deployment_table = deployment_table.filter(Deployment.block_id == filters["block_id"])
    if filters["locality_id"]:
        deployment_table = deployment_table.filter(Deployment.locality_id == filters["locality_id"])
    if filters["worker_id"]:
        deployment_table = deployment_table.filter(Deployment.worker_id == filters["worker_id"])
    table_rows = deployment_table.with_entities(
        Block.id, Block.name, Locality.id, Locality.name, Worker.id,
        db.func.coalesce(Worker.full_name, Deployment.worker_name), Worker.mphw_name,
        Deployment.status, db.func.count(db.func.distinct(HouseVisit.house_id)), db.func.count(db.func.distinct(HouseVisit.id)),
        db.func.coalesce(db.func.sum(positive_case), 0), db.func.count(db.func.distinct(ReinspectionTask.id)), db.func.max(HouseVisit.visited_at),
    ).group_by(Block.id, Block.name, Locality.id, Locality.name, Worker.id, Worker.full_name, Deployment.worker_name, Worker.mphw_name, Deployment.status).all()
    high_alerts = [
        {"block_id": row[0], "locality_id": row[1], "locality": row[2], "visits": row[3], "positivity": round(row[4] * 100 / row[3], 1)}
        for row in locality_rows if row[4] * 100 / row[3] >= HIGH_POSITIVITY_THRESHOLD
    ]
    zero_checker_rows = Worker.query.join(Deployment, Deployment.worker_id == Worker.id).outerjoin(
        HouseVisit, db.and_(HouseVisit.worker_id == Worker.id, HouseVisit.visited_at >= start_at, HouseVisit.visited_at < end_at)
    ).filter(Worker.is_active.is_(True), Deployment.deployment_date.between(filters["date_from"], filters["date_to"])).filter(
        Deployment.block_id == filters["block_id"] if filters["block_id"] else true(),
        Deployment.locality_id == filters["locality_id"] if filters["locality_id"] else true(),
        Worker.id == filters["worker_id"] if filters["worker_id"] else true(),
    ).group_by(Worker.id, Worker.full_name, Worker.official_worker_id).having(db.func.count(HouseVisit.id) == 0).all()

    return jsonify({
        "filters": {**filters, "date_from": filters["date_from"].isoformat(), "date_to": filters["date_to"].isoformat()},
        "kpis": {
            "deployed": metric(deployed, previous_deployed), "houses": metric(visit_total, previous_total),
            "visits": metric(visit_total, previous_total), "positive": metric(positive_total, previous_positive),
            "positivity": metric(positivity, previous_positivity), "pending": metric(pending, 0), "overdue": metric(overdue, 0),
        },
        "charts": {
            "trend": {"labels": days, "visits": [day_map.get(day, {}).get("visits", 0) for day in days], "positive": [day_map.get(day, {}).get("positive", 0) for day in days]},
            "larva": {"positive": positive_total, "negative": max(visit_total - positive_total, 0)},
            "area": [{"id": row[1] if filters["block_id"] else row[0], "label": row[2] if filters["block_id"] else row[1], "coverage": row[3] if filters["block_id"] else row[2], "positivity": round((row[4] if filters["block_id"] else row[3]) * 100 / (row[3] if filters["block_id"] else row[2]), 1) if (row[3] if filters["block_id"] else row[2]) else 0} for row in (locality_rows if filters["block_id"] else block_rows)],
            "localities": [{"block_id": row[0], "id": row[1], "label": row[2], "positivity": round(row[4] * 100 / row[3], 1)} for row in locality_rows],
            "reinspections": {"pending": pending, "completed": completed_reinspections, "overdue": overdue},
            "checkers": [{"id": row[0], "label": row[1], "visits": row[2], "positive": row[3]} for row in sorted(checker_rows, key=lambda row: row[2], reverse=True)[:5] + sorted(checker_rows, key=lambda row: row[2])[:5]],
        },
        "alerts": {"threshold": HIGH_POSITIVITY_THRESHOLD, "high_positivity": high_alerts, "overdue": overdue, "positive_findings": positive_total, "zero_checkers": [{"id": row[0], "name": row[1], "code": row[2]} for row in zero_checker_rows]},
        "table": [{"block_id": row[0], "block": row[1], "locality_id": row[2], "locality": row[3], "worker_id": row[4], "checker": row[5], "mphw": row[6] or "Not recorded", "status": row[7], "houses": row[8], "visits": row[9], "positive": row[10], "reinspections": row[11], "last_visit": row[12].strftime("%d/%m/%Y %H:%M") if row[12] else "No visit", "positivity": round(row[10] * 100 / row[9], 1) if row[9] else 0} for row in table_rows],
    })


def dashboard_filters():
    return {
        "date_from": parse_date(request.args.get("date_from")) or date.today(),
        "date_to": parse_date(request.args.get("date_to")) or date.today(),
        "block_id": request.args.get("block_id", type=int),
        "locality_id": request.args.get("locality_id", type=int),
        "worker_id": request.args.get("worker_id", type=int),
        "larval_status": request.args.get("larval_status", "").strip(),
    }


def deployment_count(filters, start_date, end_date):
    query = Deployment.query.filter(Deployment.deployment_date.between(start_date, end_date))
    if filters["block_id"]:
        query = query.filter(Deployment.block_id == filters["block_id"])
    if filters["locality_id"]:
        query = query.filter(Deployment.locality_id == filters["locality_id"])
    if filters["worker_id"]:
        query = query.filter(Deployment.worker_id == filters["worker_id"])
    return query.with_entities(db.func.count(db.func.distinct(Deployment.worker_id))).scalar() or 0


def reinspection_counts(filters):
    query = ReinspectionTask.query.join(House).join(Locality).join(Block)
    if filters["block_id"]:
        query = query.filter(Block.id == filters["block_id"])
    if filters["locality_id"]:
        query = query.filter(Locality.id == filters["locality_id"])
    if filters["worker_id"]:
        query = query.join(HouseVisit, ReinspectionTask.origin_visit_id == HouseVisit.id).filter(HouseVisit.worker_id == filters["worker_id"])
    pending = query.filter(ReinspectionTask.status == "open", ReinspectionTask.due_date >= date.today()).count()
    overdue = query.filter(ReinspectionTask.status == "open", ReinspectionTask.due_date < date.today()).count()
    completed = query.filter(ReinspectionTask.status != "open").count()
    return pending, overdue, completed


def metric(value, previous):
    return {"value": value or 0, "change": round((value or 0) - (previous or 0), 1)}


def cached_dashboard_json(widget: str, builder):
    """Keep repeated dashboard aggregate requests inexpensive per Gunicorn worker."""
    key = (widget, request.query_string.decode("utf-8"))
    now = monotonic()
    cached = _dashboard_cache.get(key)
    if cached and cached[0] > now:
        return jsonify(cached[1])
    payload = builder()
    _dashboard_cache[key] = (now + DASHBOARD_CONFIG["cache_seconds"], payload)
    return jsonify(payload)


def monitoring_filters() -> dict:
    date_from = parse_date(request.args.get("date_from")) or date.today()
    date_to = parse_date(request.args.get("date_to")) or date.today()
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return {
        "date_from": date_from,
        "date_to": date_to,
        "block_id": request.args.get("block_id", type=int),
        "locality_id": request.args.get("locality_id", type=int),
        "worker_id": request.args.get("worker_id", type=int),
        "area_type": request.args.get("area_type", "").strip().lower(),
    }


def filtered_visits(filters: dict, start_at=None, end_at=None):
    start_at = start_at or datetime.combine(filters["date_from"], datetime.min.time())
    end_at = end_at or datetime.combine(filters["date_to"] + timedelta(days=1), datetime.min.time())
    query = HouseVisit.query.join(House).join(Locality).join(Block).filter(
        HouseVisit.visited_at >= start_at, HouseVisit.visited_at < end_at
    )
    if filters["block_id"]:
        query = query.filter(Block.id == filters["block_id"])
    if filters["locality_id"]:
        query = query.filter(Locality.id == filters["locality_id"])
    if filters["worker_id"]:
        query = query.filter(HouseVisit.worker_id == filters["worker_id"])
    if filters["area_type"] == "rural":
        query = query.filter(Block.is_urban.is_(False))
    elif filters["area_type"] == "urban":
        query = query.filter(Block.is_urban.is_(True))
    return query


def filtered_reinspections(filters: dict):
    query = ReinspectionTask.query.join(House).join(Locality).join(Block)
    if filters["block_id"]:
        query = query.filter(Block.id == filters["block_id"])
    if filters["locality_id"]:
        query = query.filter(Locality.id == filters["locality_id"])
    if filters["worker_id"]:
        query = query.join(HouseVisit, ReinspectionTask.origin_visit_id == HouseVisit.id).filter(HouseVisit.worker_id == filters["worker_id"])
    if filters["area_type"] == "rural":
        query = query.filter(Block.is_urban.is_(False))
    elif filters["area_type"] == "urban":
        query = query.filter(Block.is_urban.is_(True))
    return query


@monitoring_bp.get("/api/kpis")
@login_required
def kpis_api():
    require_management_access()
    return cached_dashboard_json("kpis", lambda: kpi_payload(monitoring_filters()))


def kpi_payload(filters: dict) -> dict:
    positive = case((HouseVisit.positive_containers > 0, 1), else_=0)
    totals = filtered_visits(filters).with_entities(
        db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)
    ).one()
    reinspections = filtered_reinspections(filters).with_entities(
        db.func.coalesce(db.func.sum(case((ReinspectionTask.status == "open", 1), else_=0)), 0),
        db.func.coalesce(db.func.sum(case((db.and_(ReinspectionTask.status == "open", ReinspectionTask.due_date < date.today()), 1), else_=0)), 0),
    ).one()
    today_filters = {**filters, "date_from": date.today(), "date_to": date.today()}
    active = filtered_visits(today_filters).with_entities(db.func.count(db.func.distinct(HouseVisit.worker_id))).scalar() or 0
    visits, positives = int(totals[0] or 0), int(totals[1] or 0)
    return {"total_visits": visits, "larva_positive": positives, "positivity_percent": round(positives * 100 / visits, 1) if visits else 0, "pending_reinspections": int(reinspections[0] or 0), "overdue_reinspections": int(reinspections[1] or 0), "active_checkers_today": active}


@monitoring_bp.get("/api/trends")
@login_required
def trends_api():
    require_management_access()
    return cached_dashboard_json("trends", lambda: trends_payload(monitoring_filters()))


def trends_payload(filters: dict) -> dict:
    positive = case((HouseVisit.positive_containers > 0, 1), else_=0)
    rows = filtered_visits(filters).with_entities(
        db.func.date(HouseVisit.visited_at), db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)
    ).group_by(db.func.date(HouseVisit.visited_at)).order_by(db.func.date(HouseVisit.visited_at)).all()
    values = {str(row[0]): (int(row[1]), int(row[2])) for row in rows}
    days = [(filters["date_from"] + timedelta(days=offset)).isoformat() for offset in range((filters["date_to"] - filters["date_from"]).days + 1)]
    visits = [values.get(day, (0, 0))[0] for day in days]
    positives = [values.get(day, (0, 0))[1] for day in days]
    return {"labels": days, "visits": visits, "positive": positives, "negative": [visit - positive for visit, positive in zip(visits, positives)]}


@monitoring_bp.get("/api/performance")
@login_required
def performance_api():
    require_management_access()
    return cached_dashboard_json("performance", lambda: performance_payload(monitoring_filters()))


def performance_payload(filters: dict) -> dict:
    positive = case((HouseVisit.positive_containers > 0, 1), else_=0)
    query = filtered_visits(filters)
    def rows_for(*columns):
        return query.with_entities(*columns, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)).group_by(*columns).order_by(db.func.count(HouseVisit.id).desc()).limit(15).all()
    blocks = rows_for(Block.id, Block.name)
    localities = rows_for(Locality.id, Locality.name)
    mphw_name = db.func.coalesce(db.func.nullif(Worker.mphw_name, ""), "Unassigned")
    mphw = query.outerjoin(Worker, Worker.id == HouseVisit.worker_id).with_entities(mphw_name, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)).group_by(mphw_name).order_by(db.func.count(HouseVisit.id).desc()).limit(15).all()
    checker_name = db.func.coalesce(Worker.full_name, HouseVisit.worker_name_snapshot)
    checkers = query.outerjoin(Worker, Worker.id == HouseVisit.worker_id).with_entities(HouseVisit.worker_id, checker_name, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)).group_by(HouseVisit.worker_id, checker_name).order_by(db.func.count(HouseVisit.id).desc()).limit(15).all()
    def format_rows(rows, id_index=0, label_index=1, count_index=2, positive_index=3):
        return [{"id": row[id_index], "label": row[label_index], "visits": int(row[count_index]), "positive": int(row[positive_index]), "positivity": round(row[positive_index] * 100 / row[count_index], 1) if row[count_index] else 0} for row in rows]
    return {"blocks": format_rows(blocks), "localities": format_rows(localities), "mphw": [{"label": row[0], "visits": int(row[1]), "positive": int(row[2]), "positivity": round(row[2] * 100 / row[1], 1) if row[1] else 0} for row in mphw], "checkers": format_rows(checkers)}


@monitoring_bp.get("/api/alerts")
@login_required
def alerts_api():
    require_management_access()
    return cached_dashboard_json("alerts", lambda: alerts_payload(monitoring_filters()))


def alerts_payload(filters: dict) -> dict:
    positive = case((HouseVisit.positive_containers > 0, 1), else_=0)
    localities = filtered_visits(filters).with_entities(
        Block.id, Locality.id, Locality.name, db.func.count(HouseVisit.id), db.func.coalesce(db.func.sum(positive), 0)
    ).group_by(Block.id, Locality.id, Locality.name).having(
        db.func.sum(positive) * 100.0 / db.func.count(HouseVisit.id) >= DASHBOARD_CONFIG["high_positivity_percent"]
    ).order_by(db.func.sum(positive).desc()).limit(15).all()
    today = date.today()
    worker_query = Worker.query.join(Deployment, Deployment.worker_id == Worker.id).outerjoin(
        HouseVisit, db.and_(HouseVisit.worker_id == Worker.id, db.func.date(HouseVisit.visited_at) == today.isoformat())
    ).join(Block, Deployment.block_id == Block.id).filter(Worker.is_active.is_(True), Deployment.deployment_date == today)
    if filters["block_id"]: worker_query = worker_query.filter(Deployment.block_id == filters["block_id"])
    if filters["locality_id"]: worker_query = worker_query.filter(Deployment.locality_id == filters["locality_id"])
    if filters["worker_id"]: worker_query = worker_query.filter(Worker.id == filters["worker_id"])
    if filters["area_type"] == "rural": worker_query = worker_query.filter(Block.is_urban.is_(False))
    elif filters["area_type"] == "urban": worker_query = worker_query.filter(Block.is_urban.is_(True))
    low_workers = worker_query.with_entities(Worker.id, Worker.official_worker_id, Worker.full_name, db.func.count(HouseVisit.id)).group_by(Worker.id, Worker.official_worker_id, Worker.full_name).having(db.func.count(HouseVisit.id) <= DASHBOARD_CONFIG["low_visits_today"]).all()
    overdue = filtered_reinspections(filters).filter(ReinspectionTask.status == "open", ReinspectionTask.due_date < today).count()
    return {"threshold": DASHBOARD_CONFIG["high_positivity_percent"], "overdue_reinspections": overdue, "high_positivity_localities": [{"block_id": row[0], "locality_id": row[1], "locality": row[2], "visits": int(row[3]), "positivity": round(row[4] * 100 / row[3], 1)} for row in localities], "low_visit_checkers": [{"id": row[0], "code": row[1], "name": row[2], "visits": int(row[3])} for row in low_workers]}


@monitoring_bp.get("/api/drilldown")
@login_required
def drilldown_api():
    require_management_access()
    return cached_dashboard_json("drilldown", lambda: drilldown_payload(monitoring_filters()))


def drilldown_payload(filters: dict) -> dict:
    positive = case((HouseVisit.positive_containers > 0, 1), else_=0)
    start_at = datetime.combine(filters["date_from"], datetime.min.time())
    end_at = datetime.combine(filters["date_to"] + timedelta(days=1), datetime.min.time())
    query = Deployment.query.join(Block).outerjoin(Locality).outerjoin(Worker, Worker.id == Deployment.worker_id).outerjoin(HouseVisit, db.and_(HouseVisit.deployment_id == Deployment.id, HouseVisit.visited_at >= start_at, HouseVisit.visited_at < end_at)).filter(Deployment.deployment_date.between(filters["date_from"], filters["date_to"]))
    if filters["block_id"]: query = query.filter(Deployment.block_id == filters["block_id"])
    if filters["locality_id"]: query = query.filter(Deployment.locality_id == filters["locality_id"])
    if filters["worker_id"]: query = query.filter(Deployment.worker_id == filters["worker_id"])
    if filters["area_type"] == "rural": query = query.filter(Block.is_urban.is_(False))
    elif filters["area_type"] == "urban": query = query.filter(Block.is_urban.is_(True))
    rows = query.with_entities(Block.id, Block.name, Locality.id, Locality.name, Worker.id, db.func.coalesce(Worker.full_name, Deployment.worker_name), Worker.mphw_name, Deployment.status, db.func.count(db.func.distinct(HouseVisit.house_id)), db.func.count(db.func.distinct(HouseVisit.id)), db.func.coalesce(db.func.sum(positive), 0), db.func.max(HouseVisit.visited_at)).group_by(Block.id, Block.name, Locality.id, Locality.name, Worker.id, Worker.full_name, Deployment.worker_name, Worker.mphw_name, Deployment.status).all()
    return {"rows": [{"block_id": row[0], "block": row[1], "locality_id": row[2], "locality": row[3] or "Not recorded", "worker_id": row[4], "checker": row[5], "mphw": row[6] or "Unassigned", "status": row[7], "houses": int(row[8]), "visits": int(row[9]), "positive": int(row[10]), "last_visit": row[11].strftime("%d/%m/%Y %H:%M") if row[11] else "No visit"} for row in rows]}


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
