from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, Request
from pydantic import Field, field_validator
from app.common import Input, Id, Pagination, now, ok
from app.config import settings
from app.db import transaction, json_value
from app.dependencies import current_user, membership, workspace
from app.errors import check
from app.events import audit
from app.security import token, hash_token
from app.modules.auth import EmailInput, TokenInput, ConfirmInput, queue_mail

router = APIRouter(tags=["Workspace"])
Role = Literal["admin", "manager", "editor", "approver", "viewer"]


class WorkspaceInput(Input):
    name: str = Field(min_length=1, max_length=150)
    slug: str = Field(max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    logo_url: str | None = Field(None, max_length=500)
    timezone: str = "Asia/Ho_Chi_Minh"

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class WorkspaceUpdate(Input):
    name: str | None = Field(None, min_length=1, max_length=150)
    slug: str | None = Field(None, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    logo_url: str | None = Field(None, max_length=500)
    timezone: str | None = Field(None, max_length=64)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class MemberUpdate(Input):
    role: Role | None = None
    status: Literal["active", "suspended"] | None = None


class InvitationInput(EmailInput):
    role: Role


class OwnerInput(Input):
    user_id: Id


class SettingsInput(Input):
    require_approval: bool | None = None
    allow_self_approval: bool | None = None


def valid_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError):
        check(False, 422, "INVALID_TIMEZONE", "Múi giờ không hợp lệ")


def role_by_code(db, code):
    row = db.one("SELECT * FROM roles WHERE code=%s", (code,))
    check(row, 422, "INVALID_ROLE", "Vai trò không hợp lệ")
    return row


def lock_workspace(db, request, permission="member.manage"):
    ws = db.one(
        "SELECT * FROM workspaces WHERE workspace_id=%s AND deleted_at IS NULL FOR UPDATE",
        (request.state.workspace_id,),
    )
    check(ws, 404, "NOT_FOUND", "Không tìm thấy workspace")
    actor = membership(db, ws["workspace_id"], request.state.user["user_id"], permission)
    return ws, actor


@router.get("/roles")
def roles(user=Depends(current_user)):
    with transaction() as db:
        return ok(db.all("SELECT * FROM roles ORDER BY role_id"))


@router.get("/workspaces")
def list_workspaces(paging: Pagination = Depends(), user=Depends(current_user)):
    with transaction() as db:
        return ok(
            db.all(
                """SELECT w.*,r.code role FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.workspace_id
            JOIN roles r ON r.role_id=m.role_id WHERE m.user_id=%s AND m.status='active' AND w.deleted_at IS NULL
            ORDER BY w.workspace_id LIMIT %s OFFSET %s""",
                (user["user_id"], *paging.sql),
            )
        )


@router.post("/workspaces", status_code=201)
def create_workspace(data: WorkspaceInput, request: Request, user=Depends(current_user)):
    valid_timezone(data.timezone)
    with transaction() as db:
        ws_id = db.insert("workspaces", {**data.model_dump(), "owner_id": user["user_id"]})
        role = role_by_code(db, "admin")
        db.insert(
            "workspace_members",
            {"workspace_id": ws_id, "user_id": user["user_id"], "role_id": role["role_id"]},
        )
        for key in ("require_approval", "allow_self_approval"):
            db.insert(
                "workspace_settings",
                {
                    "workspace_id": ws_id,
                    "setting_key": key,
                    "setting_value": "false",
                    "updated_by": user["user_id"],
                },
            )
        request.state.workspace_id = ws_id
        audit(db, request, "workspace.create", "workspace", ws_id, new=data.model_dump())
        return ok(db.one("SELECT * FROM workspaces WHERE workspace_id=%s", (ws_id,)))


@router.get("/workspaces/{workspace_id}", dependencies=[Depends(workspace())])
def workspace_detail(workspace_id: Id):
    with transaction() as db:
        return ok(db.one("SELECT * FROM workspaces WHERE workspace_id=%s", (workspace_id,)))


@router.get("/workspaces/{workspace_id}/access", dependencies=[Depends(workspace())])
def workspace_access(workspace_id: Id, request: Request):
    with transaction() as db:
        permissions = db.all(
            "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.permission_id=rp.permission_id WHERE rp.role_id=%s",
            (request.state.member["role_id"],),
        )
    return ok({"role": request.state.member["role_code"], "permissions": [p["code"] for p in permissions]})
@router.get("/workspaces/{workspace_id}/me/permissions")
def my_workspace_permissions(workspace_id: Id, member=Depends(workspace())):
    with transaction() as db:
        rows = db.all(
            """SELECT p.code FROM role_permissions rp
            JOIN permissions p ON p.permission_id=rp.permission_id
            WHERE rp.role_id=%s ORDER BY p.code""",
            (member["role_id"],),
        )
    return ok({"role": member["role_code"], "permissions": [row["code"] for row in rows]})


@router.patch("/workspaces/{workspace_id}", dependencies=[Depends(workspace("workspace.manage"))])
def update_workspace(workspace_id: Id, data: WorkspaceUpdate, request: Request):
    values = data.model_dump(exclude_unset=True)
    check(
        all(v is not None for k, v in values.items() if k != "logo_url"),
        422,
        "INVALID_INPUT",
        "Không được để trống tên/slug/múi giờ",
    )
    if "timezone" in values:
        valid_timezone(values["timezone"])
    with transaction() as db:
        old, _ = lock_workspace(db, request, "workspace.manage")
        for key, value in values.items():
            db.execute(f"UPDATE workspaces SET {key}=%s WHERE workspace_id=%s", (value, workspace_id))
        audit(db, request, "workspace.update", "workspace", workspace_id, old, values)
        return ok(db.one("SELECT * FROM workspaces WHERE workspace_id=%s", (workspace_id,)))


@router.get("/workspaces/{workspace_id}/members", dependencies=[Depends(workspace())])
def members(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                """SELECT m.member_id,m.user_id,m.status,m.joined_at,r.code role,u.full_name,u.email
            FROM workspace_members m JOIN roles r ON r.role_id=m.role_id JOIN users u ON u.user_id=m.user_id
            WHERE m.workspace_id=%s ORDER BY m.member_id LIMIT %s OFFSET %s""",
                (workspace_id, *paging.sql),
            )
        )


def target_member(db, workspace_id, member_id):
    target = db.one(
        "SELECT m.*,r.code FROM workspace_members m JOIN roles r ON r.role_id=m.role_id WHERE m.workspace_id=%s AND m.member_id=%s FOR UPDATE",
        (workspace_id, member_id),
    )
    check(target, 404, "NOT_FOUND", "Không tìm thấy thành viên")
    return target


def protect_member(db, ws, actor, target, role, status):
    check(
        actor["role_code"] == "admin" or (target["code"] != "admin" and role != "admin"),
        403,
        "FORBIDDEN",
        "Chỉ Admin được quản lý Admin",
    )
    check(
        ws["owner_id"] != target["user_id"] or (role == "admin" and status == "active"),
        409,
        "OWNER_PROTECTED",
        "Chuyển owner trước",
    )
    count = db.one(
        "SELECT COUNT(*) n FROM workspace_members m JOIN roles r ON r.role_id=m.role_id WHERE m.workspace_id=%s AND m.status='active' AND r.code='admin'",
        (ws["workspace_id"],),
    )["n"]
    check(
        not (
            target["code"] == "admin"
            and target["status"] == "active"
            and (role != "admin" or status != "active")
            and count <= 1
        ),
        409,
        "LAST_ADMIN",
        "Phải còn ít nhất một Admin active",
    )


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}", dependencies=[Depends(workspace("member.manage"))]
)
def update_member(workspace_id: Id, member_id: Id, data: MemberUpdate, request: Request):
    with transaction() as db:
        ws, actor = lock_workspace(db, request)
        target = target_member(db, workspace_id, member_id)
        role, status = data.role or target["code"], data.status or target["status"]
        protect_member(db, ws, actor, target, role, status)
        db.execute(
            "UPDATE workspace_members SET role_id=%s,status=%s WHERE member_id=%s",
            (role_by_code(db, role)["role_id"], status, member_id),
        )
        audit(
            db,
            request,
            "member.update",
            "member",
            member_id,
            {"role": target["code"], "status": target["status"]},
            {"role": role, "status": status},
        )
    return ok({"member_id": member_id, "role": role, "status": status})


@router.delete(
    "/workspaces/{workspace_id}/members/{member_id}", dependencies=[Depends(workspace("member.manage"))]
)
def remove_member(workspace_id: Id, member_id: Id, request: Request):
    with transaction() as db:
        ws, actor = lock_workspace(db, request)
        target = target_member(db, workspace_id, member_id)
        protect_member(db, ws, actor, target, None, None)
        db.execute("DELETE FROM workspace_members WHERE member_id=%s", (member_id,))
        audit(db, request, "member.remove", "member", member_id)
    return ok({"message": "Đã xóa thành viên"})


@router.post(
    "/workspaces/{workspace_id}/transfer-owner", dependencies=[Depends(workspace("workspace.manage"))]
)
def transfer_owner(workspace_id: Id, data: OwnerInput, request: Request):
    with transaction() as db:
        ws, _ = lock_workspace(db, request)
        check(
            ws["owner_id"] == request.state.user["user_id"], 403, "OWNER_ONLY", "Chỉ owner được chuyển sở hữu"
        )
        check(
            membership(db, workspace_id, data.user_id)["role_code"] == "admin",
            422,
            "ADMIN_REQUIRED",
            "Owner mới phải là Admin active",
        )
        db.execute("UPDATE workspaces SET owner_id=%s WHERE workspace_id=%s", (data.user_id, workspace_id))
        audit(
            db,
            request,
            "workspace.transfer_owner",
            "workspace",
            workspace_id,
            {"owner_id": ws["owner_id"]},
            {"owner_id": data.user_id},
        )
    return ok({"owner_id": data.user_id})


@router.get("/workspaces/{workspace_id}/invitations", dependencies=[Depends(workspace("member.manage"))])
def invitations(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT i.invitation_id,i.email,i.role_id,i.status,i.expires_at,i.created_at,r.code role FROM workspace_invitations i JOIN roles r ON r.role_id=i.role_id WHERE i.workspace_id=%s ORDER BY i.invitation_id DESC LIMIT %s OFFSET %s",
                (workspace_id, *paging.sql),
            )
        )


@router.post(
    "/workspaces/{workspace_id}/invitations",
    status_code=201,
    dependencies=[Depends(workspace("member.manage"))],
)
def invite(workspace_id: Id, data: InvitationInput, request: Request):
    with transaction() as db:
        _, actor = lock_workspace(db, request)
        check(
            actor["role_code"] == "admin" or data.role != "admin",
            403,
            "FORBIDDEN",
            "Chỉ Admin được mời Admin",
        )
        check(
            not db.one(
                "SELECT 1 FROM workspace_members m JOIN users u ON u.user_id=m.user_id WHERE m.workspace_id=%s AND u.email=%s",
                (workspace_id, str(data.email)),
            ),
            409,
            "ALREADY_MEMBER",
            "Email này đã là thành viên workspace",
        )
        raw, role = token(), role_by_code(db, data.role)
        db.execute(
            "UPDATE workspace_invitations SET status='revoked' WHERE workspace_id=%s AND email=%s AND status='pending'",
            (workspace_id, str(data.email)),
        )
        invite_id = db.insert(
            "workspace_invitations",
            {
                "workspace_id": workspace_id,
                "email": str(data.email),
                "role_id": role["role_id"],
                "token_hash": hash_token(raw),
                "invited_by": request.state.user["user_id"],
                "expires_at": now() + timedelta(days=7),
            },
        )
        queue_mail(
            db, str(data.email), "Lời mời workspace", f"{settings.app_url}/accept-invitation?token={raw}"
        )
        audit(
            db,
            request,
            "invitation.create",
            "invitation",
            invite_id,
            new={"email": str(data.email), "role": data.role},
        )
    return ok({"invitation_id": invite_id})


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/resend",
    dependencies=[Depends(workspace("member.manage"))],
)
def resend_invitation(workspace_id: Id, invitation_id: Id, request: Request):
    with transaction() as db:
        _, actor = lock_workspace(db, request)
        item = db.one(
            "SELECT i.*,r.code role FROM workspace_invitations i JOIN roles r ON r.role_id=i.role_id WHERE i.workspace_id=%s AND i.invitation_id=%s FOR UPDATE",
            (workspace_id, invitation_id),
        )
        check(
            item and item["status"] in ("pending", "expired"),
            409,
            "INVITATION_UNAVAILABLE",
            "Chỉ mời lại lời mời đang chờ hoặc đã hết hạn",
        )
        check(
            actor["role_code"] == "admin" or item["role"] != "admin",
            403,
            "FORBIDDEN",
            "Chỉ Admin được mời Admin",
        )
        check(
            not db.one(
                "SELECT 1 FROM workspace_members m JOIN users u ON u.user_id=m.user_id WHERE m.workspace_id=%s AND u.email=%s",
                (workspace_id, item["email"]),
            ),
            409,
            "ALREADY_MEMBER",
            "Email này đã là thành viên workspace",
        )
        raw = token()
        db.execute(
            "UPDATE workspace_invitations SET token_hash=%s,status='pending',expires_at=%s,invited_by=%s WHERE invitation_id=%s",
            (hash_token(raw), now() + timedelta(days=7), request.state.user["user_id"], invitation_id),
        )
        queue_mail(
            db, item["email"], "Lời mời workspace", f"{settings.app_url}/accept-invitation?token={raw}"
        )
        audit(db, request, "invitation.resend", "invitation", invitation_id)
    return ok({"invitation_id": invitation_id, "message": "Đã gửi lại lời mời"})


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/revoke",
    dependencies=[Depends(workspace("member.manage"))],
)
def revoke(workspace_id: Id, invitation_id: Id):
    with transaction() as db:
        count = db.execute(
            "UPDATE workspace_invitations SET status='revoked' WHERE workspace_id=%s AND invitation_id=%s AND status='pending'",
            (workspace_id, invitation_id),
        )
        check(count, 404, "NOT_FOUND", "Không tìm thấy lời mời đang chờ")
    return ok({"message": "Đã thu hồi"})


@router.post("/invitations/accept")
def accept(data: TokenInput, user=Depends(current_user)):
    with transaction() as db:
        item = db.one(
            "SELECT * FROM workspace_invitations WHERE token_hash=%s AND status='pending' AND expires_at>UTC_TIMESTAMP()",
            (hash_token(data.token),),
        )
        check(
            item and item["email"] == user["email"],
            422,
            "INVALID_INVITATION",
            "Lời mời không hợp lệ hoặc email không khớp",
        )
        check(
            db.one(
                "SELECT 1 FROM workspaces WHERE workspace_id=%s AND deleted_at IS NULL FOR UPDATE",
                (item["workspace_id"],),
            ),
            404,
            "NOT_FOUND",
            "Workspace không còn tồn tại",
        )
        # Match invitation writers: lock workspace first, then recheck the token.
        item = db.one(
            "SELECT * FROM workspace_invitations WHERE token_hash=%s AND status='pending' AND expires_at>UTC_TIMESTAMP() FOR UPDATE",
            (hash_token(data.token),),
        )
        check(item and item["email"] == user["email"], 422, "INVALID_INVITATION", "Lời mời không còn hợp lệ")
        check(
            not db.one(
                "SELECT 1 FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
                (item["workspace_id"], user["user_id"]),
            ),
            409,
            "ALREADY_MEMBER",
            "Đã là thành viên",
        )
        db.insert(
            "workspace_members",
            {"workspace_id": item["workspace_id"], "user_id": user["user_id"], "role_id": item["role_id"]},
        )
        db.execute(
            "UPDATE workspace_invitations SET status='accepted',accepted_at=UTC_TIMESTAMP() WHERE invitation_id=%s",
            (item["invitation_id"],),
        )
    return ok({"workspace_id": item["workspace_id"]})


@router.get("/workspaces/{workspace_id}/settings", dependencies=[Depends(workspace())])
def get_settings(workspace_id: Id):
    with transaction() as db:
        return ok(
            {
                r["setting_key"]: r["setting_value"]
                for r in db.all("SELECT * FROM workspace_settings WHERE workspace_id=%s", (workspace_id,))
            }
        )


@router.patch("/workspaces/{workspace_id}/settings", dependencies=[Depends(workspace("workspace.manage"))])
def update_settings(workspace_id: Id, data: SettingsInput, request: Request):
    with transaction() as db:
        lock_workspace(db, request, "workspace.manage")
        values = data.model_dump(exclude_unset=True)
        check(
            all(v is not None for v in values.values()), 422, "INVALID_SETTING", "Cấu hình phải là true/false"
        )
        for key, value in values.items():
            db.execute(
                "INSERT INTO workspace_settings(workspace_id,setting_key,setting_value,updated_by) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),updated_by=VALUES(updated_by)",
                (workspace_id, key, json_value(value), request.state.user["user_id"]),
            )
        audit(db, request, "workspace.settings", "workspace", workspace_id, new=values)
    return get_settings(workspace_id)


@router.delete("/workspaces/{workspace_id}", dependencies=[Depends(workspace("workspace.manage"))])
def delete_workspace(workspace_id: Id, data: ConfirmInput, request: Request):
    check(data.confirm, 422, "CONFIRM_REQUIRED", "Cần xác nhận")
    with transaction() as db:
        lock_workspace(db, request, "workspace.manage")
        db.all("SELECT post_id FROM posts WHERE workspace_id=%s ORDER BY post_id FOR UPDATE", (workspace_id,))
        check(
            not db.one(
                "SELECT 1 FROM publish_jobs j JOIN post_variants v ON v.variant_id=j.variant_id WHERE v.workspace_id=%s AND j.status='running'",
                (workspace_id,),
            ),
            409,
            "WORKSPACE_BUSY",
            "Đang gửi bài, thử lại sau",
        )
        db.execute(
            "UPDATE schedules s JOIN post_variants v ON v.variant_id=s.variant_id SET s.status='cancelled',s.cancelled_at=UTC_TIMESTAMP() WHERE v.workspace_id=%s AND s.status IN ('pending','queued')",
            (workspace_id,),
        )
        db.execute(
            "UPDATE publish_jobs j JOIN post_variants v ON v.variant_id=j.variant_id SET j.status='failed',j.error_code='WORKSPACE_DELETED',j.finished_at=UTC_TIMESTAMP() WHERE v.workspace_id=%s AND j.status='queued'",
            (workspace_id,),
        )
        db.execute(
            "DELETE t FROM social_account_tokens t JOIN social_accounts a ON a.social_account_id=t.social_account_id WHERE a.workspace_id=%s",
            (workspace_id,),
        )
        db.execute(
            "UPDATE social_accounts SET status='disconnected',disconnected_at=UTC_TIMESTAMP() WHERE workspace_id=%s",
            (workspace_id,),
        )
        db.execute("UPDATE workspaces SET deleted_at=UTC_TIMESTAMP() WHERE workspace_id=%s", (workspace_id,))
        audit(db, request, "workspace.delete", "workspace", workspace_id)
    return ok({"message": "Đã xóa mềm workspace, hủy lịch và thu hồi token"})
