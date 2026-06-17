from pathlib import Path

from fastapi.testclient import TestClient

from server.api.main import create_app
from server.api.database import create_sqlite_memory_session


def test_dashboard_route_serves_react_index_when_dist_exists(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><div id="root"></div><script type="module" src="/assets/index.js"></script>',
        encoding="utf-8",
    )
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir=dist))

    response = client.get("/dashboard?plant_id=ecloud_factory")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


def test_dashboard_route_keeps_python_fallback_when_dist_missing():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir=Path("/not/present")))

    response = client.get("/dashboard?plant_id=aodelai")

    assert response.status_code == 200
    assert 'id="dashboard-root"' in response.text
