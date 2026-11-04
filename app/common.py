from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, BeforeValidator, Field
from app.errors import check


def valid_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**64:
        raise ValueError("ID phải là số nguyên dương BIGINT")
    return str(value)


Id = Annotated[str, BeforeValidator(valid_id)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pagination(BaseModel):
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)

    @property
    def sql(self):
        return self.limit, (self.page - 1) * self.limit


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        check(False, 422, "INVALID_TIME", "Thời gian phải có múi giờ, ví dụ 2027-01-01T09:00:00+07:00")


def public(value, key=""):
    """Giữ ID dạng chuỗi và ngày UTC như response Node.js; không serialize bytes bí mật."""
    if isinstance(value, dict):
        return {k: public(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        raise ValueError("Không được trả dữ liệu nhị phân bí mật qua JSON")
    foreign_keys = {
        "created_by",
        "uploaded_by",
        "connected_by",
        "invited_by",
        "assigned_to",
        "requested_by",
        "resolved_by",
        "changed_by",
        "updated_by",
    }
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (key.endswith("_id") or key in foreign_keys)
    ):
        return str(value)
    return value


def ok(value):
    return {"data": public(value)}
