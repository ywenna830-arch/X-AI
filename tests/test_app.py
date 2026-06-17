from app import create_app


def test_home_page_loads():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "课迹".encode("utf-8") in response.data
    assert "项目初始化成功".encode("utf-8") in response.data


def test_health_check_returns_ok():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "service": "课迹"}
