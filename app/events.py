from app.db import json_value


def audit(db, request, action, entity, entity_id=None, old=None, new=None):
    state = request.state
    db.insert(
        "audit_logs",
        {
            "workspace_id": getattr(state, "workspace_id", None),
            "user_id": getattr(state, "user", {}).get("user_id"),
            "action": action,
            "entity_type": entity,
            "entity_id": entity_id,
            "old_values": json_value(old) if old is not None else None,
            "new_values": json_value(new) if new is not None else None,
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent", "")[:500],
        },
    )


def notify(db, workspace_id, user_id, kind, title, entity, entity_id):
    setting = db.one(
        "SELECT * FROM notification_settings WHERE workspace_id=%s AND user_id=%s AND type=%s",
        (workspace_id, user_id, kind),
    )
    in_app, email = (
        (bool(setting["in_app_enabled"]), bool(setting["email_enabled"])) if setting else (True, True)
    )
    if in_app or email:
        db.insert(
            "notifications",
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "type": kind,
                "title": title,
                "entity_type": entity,
                "entity_id": entity_id,
                "in_app_visible": int(in_app),
                "email_status": "pending" if email else "not_required",
            },
        )


def notify_managers(db, workspace_id, kind, title, entity, entity_id):
    for row in db.all(
        """SELECT m.user_id FROM workspace_members m JOIN roles r ON r.role_id=m.role_id
        WHERE m.workspace_id=%s AND m.status='active' AND r.code IN ('admin','manager','approver')""",
        (workspace_id,),
    ):
        notify(db, workspace_id, row["user_id"], kind, title, entity, entity_id)


def change_status(db, post, status, user_id=None, note=None):
    if post["status"] != status:
        db.execute("UPDATE posts SET status=%s WHERE post_id=%s", (status, post["post_id"]))
        db.insert(
            "post_status_history",
            {
                "post_id": post["post_id"],
                "from_status": post["status"],
                "to_status": status,
                "changed_by": user_id,
                "note": note,
            },
        )
        post["status"] = status
