from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from .extensions import db
from .models import Block, GeographyAlias, House, Locality, SubCentre
from .security import validate_csrf
from .services.audit import log_change
from .services.permissions import require_operational_management_access


geography_bp = Blueprint("geography", __name__, url_prefix="/geography")


@geography_bp.get("/")
@login_required
def index():
    require_operational_management_access()
    return render_template("geography/index.html", blocks=Block.query.order_by(Block.name).all(), sub_centres=SubCentre.query.order_by(SubCentre.name).all(), aliases=GeographyAlias.query.order_by(GeographyAlias.review_status, GeographyAlias.source_name).all())


@geography_bp.post("/blocks")
@login_required
def add_block():
    require_operational_management_access()
    validate_csrf()
    name = request.form.get("name", "").strip()
    if not name or Block.query.filter_by(name=name).first():
        flash("Enter a unique approved block name.", "error")
    else:
        block = Block(name=name, is_urban=request.form.get("is_urban") == "on")
        db.session.add(block)
        db.session.flush()
        log_change("create", "block", block.id, after={"name": name})
        db.session.commit()
        flash("Block created.", "success")
    return redirect(url_for("geography.index"))


@geography_bp.post("/localities")
@login_required
def add_locality():
    require_operational_management_access()
    validate_csrf()
    block_id = request.form.get("block_id", type=int)
    name = request.form.get("name", "").strip()
    if not block_id or not name or Locality.query.filter_by(block_id=block_id, name=name).first():
        flash("Select a block and enter a unique locality name.", "error")
    else:
        sub_centre_id = request.form.get("sub_centre_id", type=int)
        sub_centre = db.session.get(SubCentre, sub_centre_id) if sub_centre_id else None
        if sub_centre and sub_centre.block_id != block_id:
            flash("The selected sub-centre does not belong to this block.", "error")
            return redirect(url_for("geography.index"))
        locality = Locality(block_id=block_id, sub_centre_id=sub_centre_id, name=name, locality_type=request.form.get("locality_type", "").strip() or None)
        db.session.add(locality)
        db.session.flush()
        log_change("create", "locality", locality.id, after={"name": name, "block_id": block_id})
        db.session.commit()
        flash("Locality created.", "success")
    return redirect(url_for("geography.index"))


@geography_bp.post("/sub-centres")
@login_required
def add_sub_centre():
    require_operational_management_access()
    validate_csrf()
    block_id = request.form.get("block_id", type=int)
    name = request.form.get("name", "").strip()
    if not block_id or not name or SubCentre.query.filter_by(block_id=block_id, name=name).first():
        flash("Select a block and enter a unique sub-centre name.", "error")
    else:
        sub_centre = SubCentre(block_id=block_id, name=name)
        db.session.add(sub_centre)
        db.session.flush()
        log_change("create", "sub_centre", sub_centre.id, after={"name": name, "block_id": block_id})
        db.session.commit()
        flash("Sub-centre created.", "success")
    return redirect(url_for("geography.index"))


@geography_bp.post("/aliases")
@login_required
def add_alias():
    require_operational_management_access()
    validate_csrf()
    source_name = request.form.get("source_name", "").strip()
    if not source_name:
        flash("Source spelling is required.", "error")
    else:
        alias = GeographyAlias(source_name=source_name, block_id=request.form.get("block_id", type=int), locality_id=request.form.get("locality_id", type=int), review_status="approved")
        db.session.add(alias)
        db.session.flush()
        log_change("map_source_name", "geography_alias", alias.id, after={"source_name": source_name})
        db.session.commit()
        flash("Source name mapping saved.", "success")
    return redirect(url_for("geography.index"))


@geography_bp.route("/houses/new", methods=["GET", "POST"])
@login_required
def create_house():
    require_operational_management_access()
    localities = Locality.query.order_by(Locality.name).all()
    if request.method == "POST":
        validate_csrf()
        code = request.form.get("house_code", "").strip() or next_house_code()
        locality_id = request.form.get("locality_id", type=int)
        address = request.form.get("address", "").strip()
        if not code or not locality_id or not address or House.query.filter_by(house_code=code).first():
            flash("House ID, locality, and address are required; House ID must be unique.", "error")
        else:
            house = House(house_code=code, locality_id=locality_id, house_number=request.form.get("house_number", "").strip() or None, mobile_number=normalised_mobile(request.form.get("mobile_number", "")) or None, address=address)
            db.session.add(house)
            db.session.flush()
            log_change("create", "house", house.id, after={"house_code": code})
            db.session.commit()
            flash("House created.", "success")
            return redirect(url_for("geography.index"))
    return render_template("geography/house_form.html", localities=localities)


def next_house_code() -> str:
    sequence = 1
    while House.query.filter_by(house_code=f"HP-HOS-{sequence:06d}").first():
        sequence += 1
    return f"HP-HOS-{sequence:06d}"


def normalised_mobile(value: str) -> str:
    return "".join(character for character in value if character.isdigit())
