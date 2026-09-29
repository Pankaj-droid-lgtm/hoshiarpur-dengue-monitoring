from flask import request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def log_change(action: str, entity_type: str, entity_id: str, before=None, after=None) -> AuditLog:
    """Create an audit entry in the caller's transaction."""
    entry = AuditLog(
        actor_user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_values=before,
        after_values=after,
        remote_address=request.remote_addr,
    )
    db.session.add(entry)
    return entry
