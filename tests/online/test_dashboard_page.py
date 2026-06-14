from fastapi.testclient import TestClient

from microgrid_online.api import create_app
from microgrid_online.database import create_sqlite_memory_session


def test_root_serves_customer_dashboard_page():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir="/not/present"))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="dashboard-root"' in response.text
    assert "MPC 策略对比看板" in response.text
    assert "/api/v1/plants/" in response.text
    assert "const DEFAULT_PLANT_ID = \"hehong_huajin\"" in response.text


def test_dashboard_route_serves_same_customer_page():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir="/not/present"))

    response = client.get("/dashboard?plant_id=aodelai")

    assert response.status_code == 200
    assert "const DEFAULT_PLANT_ID = \"aodelai\"" in response.text
    assert "drawPowerChart" in response.text
