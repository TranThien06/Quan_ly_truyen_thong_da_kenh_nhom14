import json
import re
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from scripts.migrate import statements


def normalized(path):
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_all_90_express_operations_are_preserved():
    old = json.loads((Path(__file__).parent / "fixtures" / "express_endpoints.json").read_text())
    new = {
        (method.upper(), normalized(path)): operation
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
    }
    assert len(old) == 90
    for operation in old:
        key = (operation["method"], normalized(operation["path"]))
        assert key in new, key
        assert str(operation["status"]) in new[key]["responses"], key


def test_swagger_health_and_validation_without_mysql():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["version"] == "2.0.0"
    assert client.get("/api/v1/me").status_code == 401
    result = client.post(
        "/api/v1/auth/register", json={"email": "bad", "password": "short", "full_name": "Test"}
    )
    assert result.status_code == 422
    assert "error" in result.json()
    assert "short" not in json.dumps(result.json())


def test_sql_parser_handles_comments_and_semicolons_in_strings():
    sql = "-- comment;\nINSERT INTO x VALUES ('a;b'); /* ; */ SELECT ';';"
    assert statements(sql) == ["INSERT INTO x VALUES ('a;b')", "SELECT ';'"]


def test_large_json_is_rejected_with_and_without_content_length():
    client = TestClient(app)
    content = b'"' + b"a" * (1024 * 1024) + b'"'
    assert (
        client.post(
            "/api/v1/auth/login", content=content, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    chunks = (content[i : i + 65536] for i in range(0, len(content), 65536))
    response = client.post("/api/v1/auth/login", content=chunks, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
