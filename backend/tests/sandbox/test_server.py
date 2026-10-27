from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from vibonomics.sandbox import server


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.create_app())


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_run(client):
    response = client.post(
        "/run",
        json={
            "files": {"main.py": "print('hello')\n"},
            "command": ["python", "main.py"],
            "timeout": 20,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert response.json()["exit_code"] == 0
    assert "hello" in body["stdout"]


def test_rejects_bad_command_shape(client):
    assert client.post("/run", json={"files": {}, "command": []}).status_code == 422


def test_rejects_oversized_workspaces(client):
    big = {"big.txt": "x" * 1000}
    payload = {"files": {f"f{i}.txt": big["big.txt"] for i in range(2200)}, "command": ["python", "-c", "pass"]}
    assert client.post("/run", json=payload).status_code == 413


def test_token_is_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("VIBONOMICS_SANDBOX_TOKEN", "s3cret")
    body = {"files": {}, "command": ["python", "-c", "pass"]}
    assert client.post("/run", json=body).status_code == 401
    ok = client.post("/run", json=body, headers={"X-Sandbox-Token": "s3cret"})
    assert ok.status_code == 200
    monkeypatch.delenv("VIBONOMICS_SANDBOX_TOKEN")
    assert os.environ.get("VIBONOMICS_SANDBOX_TOKEN") is None


def test_a_workspace_that_cannot_be_written_is_a_result_not_a_server_error(client):
    body = {"files": {"tests": "x", "tests/test_a.py": "pass\n"}, "command": ["python", "-c", "pass"]}
    response = client.post("/run", json=body)
    assert response.status_code == 200
    assert response.json()["exit_code"] is None
    assert "FileExistsError" in response.json()["stderr"]
