import re
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.db import transaction
from app.errors import check
from app.security import hash_token
from app.common import valid_id

bearer = HTTPBearer(auto_error=False)


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    raw = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    check(re.fullmatch(r"[A-Za-z0-9_-]{43}", raw), 401, "UNAUTHENTICATED", "Cần Bearer token")
    with transaction() as db:
        user = db.one(
            """SELECT s.session_id,u.user_id,u.email,u.full_name,u.is_system_admin
            FROM auth_sessions s JOIN users u ON u.user_id=s.user_id
            WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>UTC_TIMESTAMP()
            AND s.last_seen_at>DATE_SUB(UTC_TIMESTAMP(),INTERVAL 30 MINUTE) AND u.status='active'""",
            (hash_token(raw),),
        )
        check(user, 401, "SESSION_EXPIRED", "Phiên không hợp lệ hoặc hết hạn")
        db.execute(
            "UPDATE auth_sessions SET last_seen_at=UTC_TIMESTAMP() WHERE session_id=%s", (user["session_id"],)
        )
    request.state.user = user
    return user


def membership(db, workspace_id, user_id, permission=None, lock=False):
    member = db.one(
        """SELECT m.*,r.code role_code FROM workspace_members m
        JOIN roles r ON r.role_id=m.role_id JOIN workspaces w ON w.workspace_id=m.workspace_id
        WHERE m.workspace_id=%s AND m.user_id=%s AND m.status='active' AND w.deleted_at IS NULL"""
        + (" FOR UPDATE" if lock else ""),
        (workspace_id, user_id),
    )
    check(member, 404, "WORKSPACE_NOT_FOUND", "Không tìm thấy workspace")
    if permission:
        allowed = db.one(
            """SELECT 1 FROM role_permissions rp JOIN permissions p ON p.permission_id=rp.permission_id
            WHERE rp.role_id=%s AND p.code=%s""",
            (member["role_id"], permission),
        )
        check(allowed, 403, "FORBIDDEN", "Không có quyền thực hiện thao tác")
    return member


def workspace(permission=None):
    def dependency(workspace_id: str, request: Request, user=Depends(current_user)):
        try:
            valid_id(workspace_id)
        except ValueError:
            check(False, 422, "INVALID_ID", "Workspace ID không hợp lệ")
        with transaction() as db:
            member = membership(db, workspace_id, user["user_id"], permission)
        request.state.workspace_id, request.state.member = workspace_id, member
        return member

    return dependency


def system_admin(user=Depends(current_user)):
    check(user["is_system_admin"], 403, "FORBIDDEN", "Cần quyền quản trị hệ thống")
    return user
