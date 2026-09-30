from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import csv

from openpyxl import load_workbook

from ..extensions import db
from ..models import (
    Block,
    GeographyAlias,
    HistoricalBlockReport,
    HistoricalDengueCase,
    HistoricalFieldResponse,
    HistoricalHighRiskCluster,
    HistoricalStaffingAllocation,
    Locality,
    SourceDocument,
)


@dataclass
class ImportSummary:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    source_document_id: int | None = None


def import_historical_workbook(filename: str, contents: bytes, source_type: str) -> ImportSummary:
    """Import one source workbook atomically without creating operational activity."""
    digest = sha256(contents).hexdigest()
    existing = SourceDocument.query.filter_by(sha256=digest).first()
    if existing:
        return ImportSummary(skipped=1, source_document_id=existing.id)

    workbook = load_workbook(BytesIO(contents), read_only=True, data_only=False)
    source = SourceDocument(original_filename=Path(filename).name, sha256=digest, source_type=source_type)
    db.session.add(source)
    db.session.flush()
    summary = ImportSummary(source_document_id=source.id)
    try:
        normalized_name = source.original_filename.casefold()
        if "dengue cases" in normalized_name:
            import_cases(workbook, source, summary)
        elif "breeding checkers" in normalized_name:
            import_staffing(workbook, source, summary)
        else:
            import_vbd_reporting(workbook, source, summary)
        return summary
    except Exception:
        db.session.rollback()
        raise


def import_high_risk_reference(image_filename: str, image_contents: bytes, csv_contents: bytes) -> ImportSummary:
    """Import a reviewed transcription while retaining the original image as source."""
    digest = sha256(image_contents).hexdigest()
    existing = SourceDocument.query.filter_by(sha256=digest).first()
    if existing:
        return ImportSummary(skipped=1, source_document_id=existing.id)
    source = SourceDocument(
        original_filename=Path(image_filename).name,
        sha256=digest,
        source_type="high_risk_reference",
    )
    db.session.add(source)
    db.session.flush()
    summary = ImportSummary(source_document_id=source.id)
    try:
        rows = csv.DictReader(csv_contents.decode("utf-8-sig").splitlines())
        required = {"area_type", "block", "locality"}
        if not rows.fieldnames or not required.issubset(rows.fieldnames):
            raise ValueError("High-risk CSV requires area_type, block, locality headers.")
        for row_number, row in enumerate(rows, 2):
            area_type = text(row.get("area_type"))
            block_name = text(row.get("block"))
            locality_name = text(row.get("locality"))
            if not area_type or not locality_name:
                summary.errors += 1
                continue
            db.session.add(HistoricalHighRiskCluster(
                source_document_id=source.id,
                source_row=row_number,
                area_type_raw=area_type,
                block_raw=block_name,
                locality_raw=locality_name,
                positive_case_count=integer(row.get("positive_case_count")),
            ))
            summary.inserted += 1
        if summary.errors:
            raise ValueError("High-risk CSV contains incomplete rows.")
        return summary
    except Exception:
        db.session.rollback()
        raise


def import_cases(workbook, source: SourceDocument, summary: ImportSummary) -> None:
    sheet = workbook["Sheet1"]
    for row_number, row in enumerate(sheet.iter_rows(min_row=3, values_only=True), 3):
        if not row or not row[0]:
            continue
        values = list(row) + [None] * 10
        db.session.add(HistoricalDengueCase(
            source_document_id=source.id,
            source_row=row_number,
            source_serial=text(values[0]),
            patient_name=text(values[1]),
            age_years=integer(values[2]),
            sex_gender_raw=text(values[3]),
            contact_number_raw=text(values[4]),
            address_raw=text(values[5]),
            rural_urban_raw=text(values[6]),
            block_raw=text(values[7]),
            town_raw=text(values[8]),
            testing_date_raw=text(values[9]),
        ))
        if text(values[7]):
            resolve_block(text(values[7]), source)
        summary.inserted += 1


def import_staffing(workbook, source: SourceDocument, summary: ImportSummary) -> None:
    sheet = workbook["Sheet1"]
    for row_number, row in enumerate(sheet.iter_rows(min_row=3, values_only=True), 3):
        if not row or str(row[0]).strip().casefold() == "total":
            continue
        values = list(row) + [None] * 6
        rural_area = text(values[1])
        urban_area = text(values[2])
        db.session.add(HistoricalStaffingAllocation(
            source_document_id=source.id,
            source_row=row_number,
            rural_area_raw=rural_area,
            urban_area_raw=urban_area,
            breeding_checker_count=integer(values[3]),
            mphw_male_count=integer(values[4]),
            team_count=integer(values[5]),
        ))
        for area in (rural_area, urban_area):
            if area:
                resolve_block(area, source)
        summary.inserted += 1


def import_vbd_reporting(workbook, source: SourceDocument, summary: ImportSummary) -> None:
    for sheet in workbook.worksheets:
        if sheet.title == "Instructions":
            continue
        header = [text(cell) or f"column_{index + 1}" for index, cell in enumerate(next(sheet.iter_rows(min_row=3, max_row=3, values_only=True)))]
        for row_number, row in enumerate(sheet.iter_rows(min_row=4, values_only=True), 4):
            if not row or not row[0] or normalized(str(row[0])) in {"district total", "total"}:
                continue
            values = {header[index]: json_value(value) for index, value in enumerate(row)}
            block_name = text(row[0])
            block = resolve_block(block_name, source) if block_name else None
            if sheet.title == "Field Visit & Case Response":
                locality_name = text(row[1])
                if block and locality_name:
                    resolve_locality(locality_name, block, source)
                db.session.add(HistoricalFieldResponse(
                    source_document_id=source.id,
                    source_sheet=sheet.title,
                    source_row=row_number,
                    block_raw=block_name,
                    locality_raw=locality_name,
                    visit_date_raw=text(row[2]),
                    values=values,
                ))
            else:
                db.session.add(HistoricalBlockReport(
                    source_document_id=source.id,
                    source_sheet=sheet.title,
                    source_row=row_number,
                    block_raw=block_name,
                    reporting_period=None,
                    values=values,
                ))
            summary.inserted += 1


def resolve_block(raw_name: str, source: SourceDocument) -> Block | None:
    key = normalized(raw_name)
    if not key:
        return None
    for block in Block.query.order_by(Block.id).all():
        if normalized(block.name) == key:
            return block
    return None


def resolve_locality(raw_name: str, block: Block, source: SourceDocument) -> Locality | None:
    key = normalized(raw_name)
    if not key:
        return None
    for locality in Locality.query.filter_by(block_id=block.id).order_by(Locality.id).all():
        if normalized(locality.name) == key:
            return locality
    return None


def record_alias(raw_name: str, source: SourceDocument, block: Block, locality: Locality | None = None) -> None:
    if GeographyAlias.query.filter_by(source_name=raw_name, source_document_id=source.id).first() is None:
        db.session.add(GeographyAlias(
            source_name=raw_name,
            source_document_id=source.id,
            block=block,
            locality=locality,
            review_status="approved",
        ))


def clean(value: str) -> str:
    return " ".join(value.split()).strip()


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean(value).casefold())


def text(value) -> str | None:
    return clean(str(value)) if value is not None and clean(str(value)) else None


def integer(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def json_value(value):
    return value.isoformat() if isinstance(value, datetime) else value
