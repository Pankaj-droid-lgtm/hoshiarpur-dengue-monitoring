from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from .extensions import db
from .models import SourceDocument
from .security import validate_csrf
from .services.audit import log_change
from .services.historical_import import import_historical_workbook
from .services.permissions import require_operational_management_access


imports_bp = Blueprint("imports", __name__, url_prefix="/imports")


@imports_bp.route("/historical", methods=["GET", "POST"])
@login_required
def historical_import():
    require_operational_management_access()
    if request.method == "POST":
        validate_csrf()
        upload = request.files.get("source_file")
        if not upload or not upload.filename.lower().endswith(".xlsx"):
            flash("Select an Excel .xlsx source file.", "error")
            return redirect(url_for("imports.historical_import"))
        try:
            summary = import_historical_workbook(
                upload.filename, upload.read(), request.form.get("source_type", "other")
            )
        except Exception:
            db.session.rollback()
            flash("The uploaded file could not be read as an Excel workbook.", "error")
            return redirect(url_for("imports.historical_import"))
        if summary.skipped:
            flash("This source document was already imported; no duplicate records were created.", "error")
            return redirect(url_for("imports.historical_import"))
        log_change("import_source", "source_document", summary.source_document_id, after={"filename": upload.filename, "inserted": summary.inserted})
        db.session.commit()
        flash(f"Import complete: {summary.inserted} inserted, {summary.updated} updated, {summary.skipped} skipped, {summary.errors} errors. No houses, visits, deployments, workers, or accounts were created.", "success")
        return redirect(url_for("imports.historical_import"))
    return render_template("imports/historical.html", sources=SourceDocument.query.order_by(SourceDocument.imported_at.desc()).all())
