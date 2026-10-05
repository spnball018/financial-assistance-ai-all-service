from fastapi.testclient import TestClient

from app.main import app


def test_foreign_host_is_rejected():
    c = TestClient(app)
    assert c.get("/docs", headers={"host": "evil.example"}).status_code == 400
    assert c.get("/docs", headers={"host": "localhost:8002"}).status_code == 200
    assert c.get("/docs", headers={"host": "expense-api:8000"}).status_code == 200
