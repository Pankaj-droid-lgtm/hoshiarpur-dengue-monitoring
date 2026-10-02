from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


class Role(str, Enum):
    ADMIN = "admin"
    ADC = "adc"
    DISTRICT_OFFICER = "district_officer"
    BLOCK_OFFICER = "block_officer"
    SUPERVISOR = "supervisor"
    FIELD_WORKER = "field_worker"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'adc', 'district_officer', 'block_officer', 'supervisor', 'field_worker')",
            name="ck_user_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    worker: Mapped[Worker | None] = relationship(back_populates="user", uselist=False)


class Worker(TimestampMixin, db.Model):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(primary_key=True)
    official_worker_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    designation: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20))
    availability_status: Mapped[str] = mapped_column(
        String(24), default="available", nullable=False
    )
    requires_login: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    mphw_name: Mapped[str | None] = mapped_column(String(160))

    user: Mapped[User | None] = relationship(back_populates="worker")
    supervisor: Mapped[Worker | None] = relationship(
        back_populates="supervised_workers", remote_side="Worker.id"
    )
    supervised_workers: Mapped[list[Worker]] = relationship(back_populates="supervisor")
    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="worker", foreign_keys="Deployment.worker_id"
    )
    visits: Mapped[list[HouseVisit]] = relationship(back_populates="worker")


class Block(TimestampMixin, db.Model):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    is_urban: Mapped[bool] = mapped_column(default=False, nullable=False)

    localities: Mapped[list[Locality]] = relationship(back_populates="block")
    deployments: Mapped[list[Deployment]] = relationship(back_populates="block")
    aliases: Mapped[list[GeographyAlias]] = relationship(back_populates="block")


class Locality(TimestampMixin, db.Model):
    __tablename__ = "localities"

    __table_args__ = (UniqueConstraint("block_id", "name", name="uq_locality_block_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    locality_type: Mapped[str | None] = mapped_column(String(32))

    block: Mapped[Block] = relationship(back_populates="localities")
    houses: Mapped[list[House]] = relationship(back_populates="locality")
    deployments: Mapped[list[Deployment]] = relationship(back_populates="locality")
    aliases: Mapped[list[GeographyAlias]] = relationship(back_populates="locality")


class GeographyAlias(TimestampMixin, db.Model):
    __tablename__ = "geography_aliases"
    __table_args__ = (UniqueConstraint("source_name", "source_document_id", name="uq_geography_alias_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    block_id: Mapped[int | None] = mapped_column(ForeignKey("blocks.id"))
    locality_id: Mapped[int | None] = mapped_column(ForeignKey("localities.id"))
    review_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)

    block: Mapped[Block | None] = relationship(back_populates="aliases")
    locality: Mapped[Locality | None] = relationship(back_populates="aliases")


class House(TimestampMixin, db.Model):
    __tablename__ = "houses"

    id: Mapped[int] = mapped_column(primary_key=True)
    locality_id: Mapped[int] = mapped_column(ForeignKey("localities.id"), nullable=False)
    house_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    house_number: Mapped[str | None] = mapped_column(String(80))
    household_member_name: Mapped[str | None] = mapped_column(String(160))
    reference_photo_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    reference_photo_content_type: Mapped[str | None] = mapped_column(String(100))
    registered_by_worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    locality: Mapped[Locality] = relationship(back_populates="houses")
    assignments: Mapped[list[HouseAssignment]] = relationship(back_populates="house")
    visits: Mapped[list[HouseVisit]] = relationship(back_populates="house")


class Deployment(TimestampMixin, db.Model):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    account_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    worker_name: Mapped[str] = mapped_column(String(160), nullable=False)
    worker_code: Mapped[str | None] = mapped_column(String(80))
    worker_contact: Mapped[str | None] = mapped_column(String(40))
    worker_designation: Mapped[str | None] = mapped_column(String(120))
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"), nullable=False)
    locality_id: Mapped[int | None] = mapped_column(ForeignKey("localities.id"))
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    supervisor_name: Mapped[str | None] = mapped_column(String(160))
    deployment_date: Mapped[date] = mapped_column(nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="assigned", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    assigned_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    worker: Mapped[Worker] = relationship(
        back_populates="deployments", foreign_keys=[worker_id]
    )
    block: Mapped[Block] = relationship(back_populates="deployments")
    locality: Mapped[Locality | None] = relationship(back_populates="deployments")
    supervisor: Mapped[Worker | None] = relationship(foreign_keys=[supervisor_id])
    account_user: Mapped[User | None] = relationship(foreign_keys=[account_user_id])
    house_assignments: Mapped[list[HouseAssignment]] = relationship(back_populates="deployment")
    visits: Mapped[list[HouseVisit]] = relationship(back_populates="deployment")


class HouseAssignment(TimestampMixin, db.Model):
    __tablename__ = "house_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    __table_args__ = (UniqueConstraint("deployment_id", "house_id", name="uq_deployment_house"),)

    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id"), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    due_date: Mapped[date | None]
    completed_at: Mapped[datetime | None]

    deployment: Mapped[Deployment] = relationship(back_populates="house_assignments")
    house: Mapped[House] = relationship(back_populates="assignments")
    visits: Mapped[list[HouseVisit]] = relationship(back_populates="assignment")


class HouseVisit(TimestampMixin, db.Model):
    __tablename__ = "house_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id"), nullable=False)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id"), nullable=False)
    worker_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("house_assignments.id"))
    visited_at: Mapped[datetime] = mapped_column(nullable=False)
    visit_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    containers_checked: Mapped[int] = mapped_column(default=0, nullable=False)
    positive_containers: Mapped[int] = mapped_column(default=0, nullable=False)
    source_reduction_done: Mapped[bool | None]
    larvicide_used: Mapped[bool | None]
    remarks: Mapped[str | None] = mapped_column(Text)

    house: Mapped[House] = relationship(back_populates="visits")
    worker: Mapped[Worker | None] = relationship(back_populates="visits")
    deployment: Mapped[Deployment] = relationship(back_populates="visits")
    assignment: Mapped[HouseAssignment | None] = relationship(back_populates="visits")
    larval_observations: Mapped[list[LarvalObservation]] = relationship(back_populates="visit")
    photos: Mapped[list[Photo]] = relationship(back_populates="visit")
    reinspections: Mapped[list[ReinspectionTask]] = relationship(back_populates="origin_visit")


class LarvalObservation(TimestampMixin, db.Model):
    __tablename__ = "larval_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("house_visits.id"), nullable=False)
    container_type: Mapped[str] = mapped_column(String(80), nullable=False)
    container_count: Mapped[int] = mapped_column(default=1, nullable=False)
    larvae_found: Mapped[bool] = mapped_column(nullable=False)
    action_taken: Mapped[str | None] = mapped_column(Text)

    visit: Mapped[HouseVisit] = relationship(back_populates="larval_observations")


class ReinspectionTask(TimestampMixin, db.Model):
    __tablename__ = "reinspection_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("houses.id"), nullable=False)
    origin_visit_id: Mapped[int | None] = mapped_column(ForeignKey("house_visits.id"))
    due_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_deployment_id: Mapped[int | None] = mapped_column(ForeignKey("deployments.id"))

    origin_visit: Mapped[HouseVisit | None] = relationship(back_populates="reinspections")


class Photo(TimestampMixin, db.Model):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("house_visits.id"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    captured_at: Mapped[datetime | None]
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    photo_type: Mapped[str] = mapped_column(String(32), default="inspection", nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    visit: Mapped[HouseVisit] = relationship(back_populates="photos")


class SourceDocument(TimestampMixin, db.Model):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)

    dengue_cases: Mapped[list[HistoricalDengueCase]] = relationship(back_populates="source_document")


class HistoricalDengueCase(TimestampMixin, db.Model):
    __tablename__ = "historical_dengue_cases"
    __table_args__ = (UniqueConstraint("source_document_id", "source_row", name="uq_case_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    source_serial: Mapped[str | None] = mapped_column(String(40))
    patient_name: Mapped[str | None] = mapped_column(String(160))
    age_years: Mapped[int | None]
    sex_gender_raw: Mapped[str | None] = mapped_column(String(32))
    contact_number_raw: Mapped[str | None] = mapped_column(String(40))
    address_raw: Mapped[str | None] = mapped_column(Text)
    rural_urban_raw: Mapped[str | None] = mapped_column(String(32))
    block_raw: Mapped[str | None] = mapped_column(String(120))
    town_raw: Mapped[str | None] = mapped_column(String(120))
    testing_date_raw: Mapped[str | None] = mapped_column(String(40))

    source_document: Mapped[SourceDocument] = relationship(back_populates="dengue_cases")
    case_response_visits: Mapped[list[CaseResponseVisit]] = relationship(
        back_populates="historical_case"
    )


class HistoricalBlockReport(TimestampMixin, db.Model):
    __tablename__ = "historical_block_reports"
    __table_args__ = (UniqueConstraint("source_document_id", "source_sheet", "source_row", name="uq_report_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    block_raw: Mapped[str | None] = mapped_column(String(160))
    reporting_period: Mapped[date | None]
    values: Mapped[dict] = mapped_column(db.JSON, nullable=False)


class HistoricalStaffingAllocation(TimestampMixin, db.Model):
    __tablename__ = "historical_staffing_allocations"
    __table_args__ = (UniqueConstraint("source_document_id", "source_row", name="uq_staffing_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    rural_area_raw: Mapped[str | None] = mapped_column(String(120))
    urban_area_raw: Mapped[str | None] = mapped_column(String(120))
    breeding_checker_count: Mapped[int | None]
    mphw_male_count: Mapped[int | None]
    team_count: Mapped[int | None]


class HistoricalFieldResponse(TimestampMixin, db.Model):
    __tablename__ = "historical_field_responses"
    __table_args__ = (UniqueConstraint("source_document_id", "source_sheet", "source_row", name="uq_field_response_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    block_raw: Mapped[str | None] = mapped_column(String(160))
    locality_raw: Mapped[str | None] = mapped_column(String(200))
    visit_date_raw: Mapped[str | None] = mapped_column(String(40))
    values: Mapped[dict] = mapped_column(db.JSON, nullable=False)


class HistoricalHighRiskCluster(TimestampMixin, db.Model):
    __tablename__ = "historical_high_risk_clusters"
    __table_args__ = (
        UniqueConstraint("source_document_id", "source_row", name="uq_high_risk_source_row"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    source_row: Mapped[int] = mapped_column(nullable=False)
    area_type_raw: Mapped[str | None] = mapped_column(String(32))
    block_raw: Mapped[str | None] = mapped_column(String(160))
    locality_raw: Mapped[str | None] = mapped_column(String(200))
    positive_case_count: Mapped[int | None]


class CaseResponseVisit(TimestampMixin, db.Model):
    __tablename__ = "case_response_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    historical_case_id: Mapped[int] = mapped_column(
        ForeignKey("historical_dengue_cases.id"), nullable=False
    )
    locality_id: Mapped[int | None] = mapped_column(ForeignKey("localities.id"))
    house_id: Mapped[int | None] = mapped_column(ForeignKey("houses.id"))
    performed_by_worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    visited_at: Mapped[datetime] = mapped_column(nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    historical_case: Mapped[HistoricalDengueCase] = relationship(back_populates="case_response_visits")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    before_values: Mapped[dict | None] = mapped_column(db.JSON)
    after_values: Mapped[dict | None] = mapped_column(db.JSON)
    remote_address: Mapped[str | None] = mapped_column(String(64))