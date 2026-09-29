from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required


main_bp = Blueprint("main", __name__)


@main_bp.get("/health")
def health():
    return jsonify(status="ok")


@main_bp.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html", is_manager=current_user.role != "field_worker")
