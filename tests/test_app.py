from app import create_app


def test_home_page_loads():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "课迹".encode("utf-8") in response.data
    assert "今日学习总览".encode("utf-8") in response.data
    assert "快速添加任务入口".encode("utf-8") in response.data


def test_stage_one_pages_load():
    app = create_app()

    pages = {
        "/tasks/new": "添加任务",
        "/tasks/confirm": "AI识别确认",
        "/tasks": "任务中心",
        "/plan": "智能计划",
    }

    with app.test_client() as client:
        for path, expected_text in pages.items():
            response = client.get(path)

            assert response.status_code == 200
            assert expected_text.encode("utf-8") in response.data
            assert "暂未接入".encode("utf-8") in response.data


def test_health_check_returns_ok():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "service": "课迹"}
