from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from antwork.app import create_app, Settings, hash_password


@pytest.fixture
def client(tmp_path):
    settings = Settings(hash_password("admin-test-password"), database=str(tmp_path / "app.db"))
    with TestClient(create_app(settings)) as client:
        yield client


def login(client):
    response = client.post("/api/login", json={"password":"admin-test-password"}, headers={"Origin":"http://127.0.0.1:8090"})
    assert response.status_code == 200
    return {"Origin":"http://127.0.0.1:8090"}


def test_auth_origin_and_cookie(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login",json={"password":"admin-test-password"}).status_code == 403
    headers = login(client)
    response = client.get("/api/state")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/concurrency/1",headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.post("/api/logout",headers=headers).status_code == 200
    assert client.get("/api/state").status_code == 401


def test_invalid_payload_does_not_echo_secrets(client, payload, caplog):
    headers = login(client)
    payload["card"]["cvv"] = "cvv-secret-invalid"
    result = client.post("/api/batches",json=payload,headers=headers)
    assert result.status_code == 422
    for value in ["cvv-secret-invalid", "4242424242424242", "test:password"]:
        assert value not in result.text + caplog.text


def test_live_disabled_until_verified(client, payload):
    headers = login(client)
    result = client.post("/api/batches",json=payload,headers=headers)
    assert result.status_code == 409
    assert not client.get("/api/state").json()["jobs"]


def test_socket_auth_and_origin(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/jobs/unknown/browser",headers={"Origin":"http://127.0.0.1:8090"}): pass
    login(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/jobs/unknown/browser",headers={"Origin":"https://evil.example"}): pass


def test_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/login",json={"password":"wrong"},headers={"Origin":"http://127.0.0.1:8090"}).status_code == 401
    assert client.post("/api/login",json={"password":"wrong"},headers={"Origin":"http://127.0.0.1:8090"}).status_code == 429


def test_assets_and_traversal(client):
    for path in ["/", "/static/app.js", "/static/style.css"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/.env").status_code == 404


def test_save_only_address(client):
    headers = login(client)
    address = {"address":"Test Street", "city":"Test City", "region":"Region", "country":"ID", "postal_code":"12345"}
    assert client.put("/api/address",json=address,headers=headers).status_code == 200
    assert client.get("/api/address").json()["address"] == address
    assert client.put("/api/address",json={**address,"cvv":"123"},headers=headers).status_code == 422
