from datetime import datetime, timezone
from app.modules.auth_db.models import ActivityLog

def log_event(db, user_id: int, action: str, module: str, ip: str = None):
    new_log = ActivityLog(
        user_id=user_id,
        action=action,
        module_affected=module,
        timestamp=datetime.now(timezone.utc),
        ip_address=ip
    )
    db.add(new_log)
    db.commit()
