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
    User,
    Worker,
)
from werkzeug.security import generate_password_hash


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
            # Check if it's the compiled breeding checkers file by header
            sheet = workbook["Sheet1"]
            first_row = [text(cell) for cell in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
            expected_header = ["URBAN AREA", "Number of breeding checkers", "Name of MPHW (m)", "S.N.", "Name of breeding checkers", "MOBILE NUMBER"]
            if first_row == expected_header:
                import_breeding_checkers_workers(workbook, source, summary)
            else:
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


def import_breeding_checkers_workers(workbook, source: SourceDocument, summary: ImportSummary) -> None:
    """Import breeding checkers as Worker and User records."""
    sheet = workbook["Sheet1"]
    # We'll keep track of the current block and MPHW name as we iterate rows
    current_block_name = None
    current_mphw_name = None
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        # Skip empty rows
        if not row or all(cell is None for cell in row):
            continue
        # Extract cells: A=block (maybe empty), B=urban area number (ignore), C=MPHW name (maybe empty), D=S.N., E=name, F=mobile
        block_cell = row[0]
        mphw_cell = row[2]
        sn_cell = row[3]
        name_cell = row[4]
        mobile_cell = row[5]
        # Update current block and MPHW if block cell is not empty
        if block_cell is not None and str(block_cell).strip() != "":
            current_block_name = str(block_cell).strip()
            current_mphw_name = str(mphw_cell).strip() if mphw_cell is not None else None
        # If we don't have a block yet, skip (should not happen after first row)
        if current_block_name is None:
            continue
        # S.N. must be present
        if sn_cell is None:
            continue
        try:
            sn = int(sn_cell)
        except (ValueError, TypeError):
            continue
        # Generate official_worker_id as W{sn:03d}
        official_worker_id = f"W{sn:03d}"
        # Name and mobile
        full_name = text(name_cell)
        phone_number = text(mobile_cell)
        if not full_name:
            summary.errors += 1
            continue
        # Check if Worker already exists
        worker = Worker.query.filter_by(official_worker_id=official_worker_id).first()
        worker_created = False
        if worker is None:
            worker = Worker(
                official_worker_id=official_worker_id,
                full_name=full_name,
                designation="Breeding Checker",
                phone_number=phone_number,
                mphw_name=current_mphw_name,
                requires_login=True,
                is_active=True,
                availability_status="available",
            )
            db.session.add(worker)
            db.session.flush()
            worker_created = True
            summary.inserted += 1  # count as inserted worker
        else:
            # Update existing worker if needed (optional, but we can update fields to match source)
            updated = False
            if worker.full_name != full_name:
                worker.full_name = full_name
                updated = True
            if worker.designation != "Breeding Checker":
                worker.designation = "Breeding Checker"
                updated = True
            if worker.phone_number != phone_number:
                worker.phone_number = phone_number
                updated = True
            if worker.mphw_name != current_mphw_name:
                worker.mphw_name = current_mphw_name
                updated = True
            if not worker.requires_login:
                worker.requires_login = True
                updated = True
            if not worker.is_active:
                worker.is_active = True
                updated = True
            if worker.availability_status != "available":
                worker.availability_status = "available"
                updated = True
            if updated:
                summary.updated += 1
        # Now handle the User account
        user = User.query.filter_by(username=official_worker_id).first()
        user_created = False
        if user is None:
            # Create new user with password hash of mobile number
            password_hash = generate_password_hash(phone_number)
            user = User(
                username=official_worker_id,
                password_hash=password_hash,
                role="field_worker",
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            user_created = True
            summary.inserted += 1  # count as inserted user
        else:
            # Update existing user if needed
            updated = False
            if user.role != "field_worker":
                user.role = "field_worker"
                updated = True
            if not user.is_active:
                user.is_active = True
                updated = True
            # Check if password needs update (if mobile changed)
            # We do not store plaintext, so we cannot compare. We'll only update if the user's password is not set or we want to reset?
            # For safety, we do not update password if user exists (to avoid breaking existing logins).
            # However, the requirement says initial password = official mobile number.
            # If the user already exists, we assume the password is already set correctly from a previous import.
            # We'll not update password on existing user.
            if updated:
                summary.updated += 1
        # Link worker and user (if both exist and not already linked)
        if worker.user is None:
            worker.user = user
        if user.worker is None:
            user.worker = worker
        # If we created either worker or user, we already counted in summary.inserted above.
        # If we updated, we counted in summary.updated.
    # End of row loop


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