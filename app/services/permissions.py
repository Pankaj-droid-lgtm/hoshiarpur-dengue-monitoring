from flask import abort
from flask_login import current_user


MANAGEMENT_ROLES = {"admin", "dc", "adc", "district_officer", "block_officer", "supervisor"}


def require_management_access() -> None:
    if not current_user.is_authenticated or current_user.role not in MANAGEMENT_ROLES:
        abort(403)


def can_access_worker(worker_id: int) -> bool:
    return current_user.role in MANAGEMENT_ROLES or (
        current_user.worker is not None and current_user.worker.id == worker_id
    )
