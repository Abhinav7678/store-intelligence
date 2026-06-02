from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_ws_echo():
    with client.websocket_connect("/ws/live") as websocket:
        websocket.send_text("ping")
        data = websocket.receive_text()
        assert "echo" in data
