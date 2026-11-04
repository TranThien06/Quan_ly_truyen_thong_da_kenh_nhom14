import hashlib
import hmac
import io
import json
import os
import subprocess
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import Field
from app.common import Input, Id, Pagination, ok
from app.config import settings
from app.db import transaction
from app.dependencies import workspace
from app.errors import check
from app.events import audit
from app.security import encryption_key
from app.modules.auth import ConfirmInput

router = APIRouter(tags=["Media"])
Image.MAX_IMAGE_PIXELS = 40_000_000


class FolderInput(Input):
    name: str = Field(min_length=1, max_length=150)
    parent_folder_id: Id | None = None


class TagInput(Input):
    name: str = Field(min_length=1, max_length=100)


class MediaUpdate(Input):
    file_name: str | None = Field(None, min_length=1, max_length=255)
    folder_id: Id | None = None
    tag_ids: list[Id] | None = Field(None, max_length=50)


def folder_exists(db, workspace_id, folder_id):
    if folder_id:
        check(
            db.one(
                "SELECT 1 FROM media_folders WHERE folder_id=%s AND workspace_id=%s",
                (folder_id, workspace_id),
            ),
            422,
            "INVALID_FOLDER",
            "Thư mục không thuộc workspace",
        )


def media_detail(db, workspace_id, media_id):
    row = db.one(
        "SELECT * FROM media_assets WHERE media_id=%s AND workspace_id=%s AND deleted_at IS NULL",
        (media_id, workspace_id),
    )
    check(row, 404, "NOT_FOUND", "Không tìm thấy media")
    return row


def file_signature(media_id, kind, expires):
    return hmac.new(encryption_key(), f"{media_id}:{kind}:{expires}".encode(), hashlib.sha256).hexdigest()


def signed_url(media, kind="file"):
    expires = str(int(time.time()) + 3600)
    key = media["storage_key"].split(".")[0]
    return f"{settings.app_url}/api/v1/media/{key}/{kind}?expires={expires}&signature={file_signature(media['media_id'], kind, expires)}"


@router.get("/media/{key}/{kind}")
def download(key: uuid.UUID, kind: Literal["file", "thumbnail"], expires: int, signature: str):
    with transaction() as db:
        row = db.one(
            "SELECT * FROM media_assets WHERE storage_key LIKE %s AND deleted_at IS NULL", (f"{key}.%",)
        )
    check(row, 404, "NOT_FOUND", "Không tìm thấy tệp")
    expected = file_signature(row["media_id"], kind, str(expires))
    check(
        expires > time.time() and hmac.compare_digest(signature, expected),
        403,
        "INVALID_SIGNATURE",
        "Liên kết tải không hợp lệ hoặc hết hạn",
    )
    filename = row["storage_key"] if kind == "file" else f"{key}.thumb.jpg"
    return FileResponse(
        settings.storage / "media" / filename,
        media_type=row["mime_type"] if kind == "file" else "image/jpeg",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.get("/workspaces/{workspace_id}/media/usage", dependencies=[Depends(workspace())])
def usage(workspace_id: Id):
    with transaction() as db:
        return ok(
            db.one(
                "SELECT COALESCE(SUM(file_size),0) retained_bytes,COALESCE(SUM(IF(deleted_at IS NULL,file_size,0)),0) active_bytes,COUNT(*) retained_files FROM media_assets WHERE workspace_id=%s",
                (workspace_id,),
            )
        )


@router.get("/workspaces/{workspace_id}/media", dependencies=[Depends(workspace())])
def list_media(
    workspace_id: Id,
    q: str | None = None,
    media_type: Literal["image", "video"] | None = None,
    folder_id: Id | None = None,
    tag_id: Id | None = None,
    paging: Pagination = Depends(),
):
    with transaction() as db:
        return ok(
            db.all(
                """SELECT m.* FROM media_assets m WHERE m.workspace_id=%s AND m.deleted_at IS NULL
            AND (%s IS NULL OR m.file_name LIKE %s) AND (%s IS NULL OR m.media_type=%s)
            AND (%s IS NULL OR m.folder_id=%s) AND (%s IS NULL OR EXISTS (SELECT 1 FROM media_asset_tags t WHERE t.media_id=m.media_id AND t.tag_id=%s))
            ORDER BY m.media_id DESC LIMIT %s OFFSET %s""",
                (
                    workspace_id,
                    q,
                    f"%{q or ''}%",
                    media_type,
                    media_type,
                    folder_id,
                    folder_id,
                    tag_id,
                    tag_id,
                    *paging.sql,
                ),
            )
        )


def save_upload(db, workspace_id, user_id, file, folder_id, request):
    folder_exists(db, workspace_id, folder_id)
    content = file.file.read(settings.max_upload_bytes + 1)
    check(content, 422, "EMPTY_FILE", "Tệp rỗng")
    check(len(content) <= settings.max_upload_bytes, 413, "FILE_TOO_LARGE", "Tệp vượt giới hạn dung lượng")
    directory = settings.storage / "media"
    directory.mkdir(parents=True, exist_ok=True)
    key = str(uuid.uuid4())
    paths = []
    try:
        thumb = directory / f"{key}.thumb.jpg"
        paths.append(thumb)
        duration = None
        if len(content) > 12 and content[4:8] == b"ftyp":
            extension, mime, kind = "mp4", "video/mp4", "video"
            output = directory / f"{key}.mp4"
            paths.append(output)
            output.write_bytes(content)
            try:
                probe = subprocess.run(
                    [
                        os.getenv("FFPROBE_PATH", "ffprobe"),
                        "-v",
                        "error",
                        "-show_streams",
                        "-show_format",
                        "-of",
                        "json",
                        str(output),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                info = json.loads(probe.stdout)
                video = next(v for v in info["streams"] if v["codec_type"] == "video")
                width, height, duration = video["width"], video["height"], float(info["format"]["duration"])
                subprocess.run(
                    [
                        os.getenv("FFMPEG_PATH", "ffmpeg"),
                        "-y",
                        "-i",
                        str(output),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:-2",
                        str(thumb),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
            except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
                check(False, 422, "VIDEO_PROBE_FAILED", "Video không hợp lệ hoặc chưa cài FFmpeg/FFprobe")
        else:
            try:
                with Image.open(io.BytesIO(content)) as image:
                    check(
                        image.format in ("JPEG", "PNG", "WEBP"),
                        422,
                        "UNSUPPORTED_MEDIA",
                        "Chỉ hỗ trợ JPEG/PNG/WebP/MP4",
                    )
                    width, height = image.size
                    check(
                        width * height <= 40_000_000, 422, "IMAGE_TOO_LARGE", "Ảnh vượt giới hạn độ phân giải"
                    )
                    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image.format]
                    mime = "image/" + image.format.lower()
                    image.load()
                    image = image.convert("RGB")
                    image.thumbnail((320, 320))
                    image.save(thumb, "JPEG")
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                check(False, 422, "INVALID_IMAGE", "Tệp ảnh không hợp lệ")
            kind = "image"
            output = directory / f"{key}.{extension}"
            paths.append(output)
            output.write_bytes(content)
        db.one("SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        quota = db.one(
            "SELECT setting_value FROM system_settings WHERE setting_key='media.workspace_quota_bytes'"
        )
        used = db.one(
            "SELECT COALESCE(SUM(file_size),0) bytes FROM media_assets WHERE workspace_id=%s", (workspace_id,)
        )
        check(
            int(used["bytes"]) + len(content) <= int(quota["setting_value"] if quota else 1073741824),
            413,
            "QUOTA_EXCEEDED",
            "Vượt hạn mức dung lượng",
        )
        media_id = db.insert(
            "media_assets",
            {
                "workspace_id": workspace_id,
                "folder_id": folder_id,
                "uploaded_by": user_id,
                "file_name": (file.filename or "file").replace("\\", "/").split("/")[-1][:255],
                "storage_key": f"{key}.{extension}",
                "file_url": f"{settings.app_url}/api/v1/media/{key}/file",
                "thumbnail_url": f"{settings.app_url}/api/v1/media/{key}/thumbnail",
                "media_type": kind,
                "mime_type": mime,
                "file_size": len(content),
                "width": width,
                "height": height,
                "duration_sec": duration,
            },
        )
        audit(db, request, "media.upload", "media", media_id)
        return media_detail(db, workspace_id, media_id)
    except Exception:
        for filename in paths:
            filename.unlink(missing_ok=True)
        raise


@router.post(
    "/workspaces/{workspace_id}/media", status_code=201, dependencies=[Depends(workspace("media.manage"))]
)
def upload(
    workspace_id: Id, request: Request, file: UploadFile = File(...), folder_id: str | None = Form(None)
):
    if folder_id:
        from app.common import valid_id

        folder_id = valid_id(folder_id)
    with transaction() as db:
        return ok(save_upload(db, workspace_id, request.state.user["user_id"], file, folder_id, request))


@router.get("/workspaces/{workspace_id}/media/{media_id}", dependencies=[Depends(workspace())])
def detail(workspace_id: Id, media_id: Id):
    with transaction() as db:
        return ok(media_detail(db, workspace_id, media_id))


@router.get("/workspaces/{workspace_id}/media/{media_id}/download-link", dependencies=[Depends(workspace())])
def download_link(workspace_id: Id, media_id: Id):
    with transaction() as db:
        row = media_detail(db, workspace_id, media_id)
    return ok(
        {"file_url": signed_url(row), "thumbnail_url": signed_url(row, "thumbnail"), "expires_in": 3600}
    )


@router.patch(
    "/workspaces/{workspace_id}/media/{media_id}", dependencies=[Depends(workspace("media.manage"))]
)
def update_media(workspace_id: Id, media_id: Id, data: MediaUpdate):
    with transaction() as db:
        db.one("SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        media_detail(db, workspace_id, media_id)
        folder_exists(db, workspace_id, data.folder_id)
        if data.file_name:
            db.execute("UPDATE media_assets SET file_name=%s WHERE media_id=%s", (data.file_name, media_id))
        if "folder_id" in data.model_fields_set:
            db.execute("UPDATE media_assets SET folder_id=%s WHERE media_id=%s", (data.folder_id, media_id))
        if data.tag_ids is not None:
            db.execute("DELETE FROM media_asset_tags WHERE media_id=%s", (media_id,))
            for tag_id in set(data.tag_ids):
                check(
                    db.one(
                        "SELECT 1 FROM media_tags WHERE tag_id=%s AND workspace_id=%s", (tag_id, workspace_id)
                    ),
                    422,
                    "INVALID_TAG",
                    "Nhãn không thuộc workspace",
                )
                db.insert("media_asset_tags", {"media_id": media_id, "tag_id": tag_id})
        return ok(media_detail(db, workspace_id, media_id))


@router.delete(
    "/workspaces/{workspace_id}/media/{media_id}", dependencies=[Depends(workspace("media.manage"))]
)
def remove_media(workspace_id: Id, media_id: Id, request: Request):
    with transaction() as db:
        media_detail(db, workspace_id, media_id)
        used = db.one(
            "SELECT 1 FROM post_media pm JOIN posts p ON p.post_id=pm.post_id WHERE pm.media_id=%s AND p.deleted_at IS NULL AND p.status IN ('pending_approval','approved','scheduled','publishing') LIMIT 1",
            (media_id,),
        )
        check(not used, 409, "MEDIA_IN_USE", "Gỡ media khỏi bài đang chờ duyệt/có lịch trước")
        db.execute("UPDATE media_assets SET deleted_at=UTC_TIMESTAMP() WHERE media_id=%s", (media_id,))
        audit(db, request, "media.delete", "media", media_id)
    return ok({"message": "Đã xóa mềm; giữ tệp cho lịch sử"})


@router.get("/workspaces/{workspace_id}/media-folders", dependencies=[Depends(workspace())])
def folders(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM media_folders WHERE workspace_id=%s ORDER BY folder_id LIMIT %s OFFSET %s",
                (workspace_id, *paging.sql),
            )
        )


@router.post(
    "/workspaces/{workspace_id}/media-folders",
    status_code=201,
    dependencies=[Depends(workspace("media.manage"))],
)
def create_folder(workspace_id: Id, data: FolderInput, request: Request):
    with transaction() as db:
        db.one("SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        folder_exists(db, workspace_id, data.parent_folder_id)
        return ok(
            {
                "folder_id": db.insert(
                    "media_folders",
                    {
                        **data.model_dump(),
                        "workspace_id": workspace_id,
                        "created_by": request.state.user["user_id"],
                    },
                )
            }
        )


@router.put(
    "/workspaces/{workspace_id}/media-folders/{folder_id}", dependencies=[Depends(workspace("media.manage"))]
)
def update_folder(workspace_id: Id, folder_id: Id, data: FolderInput):
    with transaction() as db:
        db.one("SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        folder_exists(db, workspace_id, folder_id)
        folder_exists(db, workspace_id, data.parent_folder_id)
        ancestor = data.parent_folder_id
        while ancestor:
            check(str(ancestor) != folder_id, 422, "FOLDER_CYCLE", "Không được tạo vòng lặp thư mục")
            ancestor = db.one(
                "SELECT parent_folder_id FROM media_folders WHERE folder_id=%s AND workspace_id=%s",
                (ancestor, workspace_id),
            )["parent_folder_id"]
        db.execute(
            "UPDATE media_folders SET name=%s,parent_folder_id=%s WHERE folder_id=%s AND workspace_id=%s",
            (data.name, data.parent_folder_id, folder_id, workspace_id),
        )
    return ok({"folder_id": folder_id, **data.model_dump()})


@router.delete(
    "/workspaces/{workspace_id}/media-folders/{folder_id}", dependencies=[Depends(workspace("media.manage"))]
)
def delete_folder(workspace_id: Id, folder_id: Id, data: ConfirmInput, request: Request):
    check(data.confirm, 422, "CONFIRM_REQUIRED", "Cần xác nhận")
    with transaction() as db:
        db.one("SELECT workspace_id FROM workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
        folder_exists(db, workspace_id, folder_id)
        check(
            not db.one(
                "SELECT 1 FROM media_folders WHERE parent_folder_id=%s AND workspace_id=%s",
                (folder_id, workspace_id),
            ),
            409,
            "FOLDER_HAS_CHILDREN",
            "Di chuyển hoặc xóa thư mục con trước",
        )
        db.execute(
            "UPDATE media_assets SET folder_id=NULL WHERE folder_id=%s AND workspace_id=%s",
            (folder_id, workspace_id),
        )
        db.execute(
            "DELETE FROM media_folders WHERE folder_id=%s AND workspace_id=%s", (folder_id, workspace_id)
        )
        audit(db, request, "media.folder_delete", "folder", folder_id)
    return ok({"message": "Đã xóa thư mục, giữ tệp ở thư mục gốc"})


@router.get("/workspaces/{workspace_id}/media-tags", dependencies=[Depends(workspace())])
def tags(workspace_id: Id, paging: Pagination = Depends()):
    with transaction() as db:
        return ok(
            db.all(
                "SELECT * FROM media_tags WHERE workspace_id=%s ORDER BY tag_id LIMIT %s OFFSET %s",
                (workspace_id, *paging.sql),
            )
        )


@router.post(
    "/workspaces/{workspace_id}/media-tags",
    status_code=201,
    dependencies=[Depends(workspace("media.manage"))],
)
def create_tag(workspace_id: Id, data: TagInput):
    with transaction() as db:
        return ok({"tag_id": db.insert("media_tags", {"workspace_id": workspace_id, "name": data.name})})


@router.delete(
    "/workspaces/{workspace_id}/media-tags/{tag_id}", dependencies=[Depends(workspace("media.manage"))]
)
def delete_tag(workspace_id: Id, tag_id: Id):
    with transaction() as db:
        db.execute("DELETE FROM media_tags WHERE tag_id=%s AND workspace_id=%s", (tag_id, workspace_id))
    return ok({"message": "Đã xóa nhãn"})
