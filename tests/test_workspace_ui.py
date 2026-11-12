from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.dependencies import current_user
from app.main import create_app
from app.modules import workspaces


def test_frontend_is_served_without_login():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="vi"' in response.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/styles.css").status_code == 200
    assert client.post("/api/v1/workspaces", json={"name": "Team", "slug": "team"}).status_code == 401


@pytest.mark.parametrize("model", [workspaces.WorkspaceInput, workspaces.WorkspaceUpdate])
def test_workspace_rejects_blank_names(model):
    with pytest.raises(ValidationError):
        model(name="   ", slug="team")
    assert model(name="  Đội ngũ  ", slug="team").name == "Đội ngũ"


def test_viewer_can_read_permissions_but_cannot_update(monkeypatch):
    class FakeDB:
        def one(self, sql, args=()):
            if "SELECT m.*" in sql:
                return {"role_id": "5", "role_code": "viewer"}
            if "role_permissions" in sql:
                return None
            raise AssertionError("Unauthorized request reached a write query")

        def all(self, sql, args=()):
            assert "role_permissions" in sql
            return [{"code": "workspace.view"}]

    @contextmanager
    def transaction():
        yield FakeDB()

    import app.dependencies as dependencies

    monkeypatch.setattr(dependencies, "transaction", transaction)
    monkeypatch.setattr(workspaces, "transaction", transaction)
    app = create_app()
    app.dependency_overrides[current_user] = lambda: {"user_id": "2"}
    client = TestClient(app)
    result = client.get("/api/v1/workspaces/1/access")
    assert result.status_code == 200
    assert result.json()["data"] == {"role": "viewer", "permissions": ["workspace.view"]}
    assert client.patch("/api/v1/workspaces/1", json={"name": "Changed"}).status_code == 403
