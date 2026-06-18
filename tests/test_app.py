from app import create_app


def make_app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")})


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


def test_task_crud_status_filter_and_persistence(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    app = create_app({"TESTING": True, "DATABASE": str(db_path)})

    with app.test_client() as client:
        create_response = client.post(
            "/tasks",
            data={
                "course_name": "数据库系统",
                "title": "实验报告",
                "task_type": "实验",
                "description": "完成实验三",
                "deadline": "2099-06-18T18:00",
                "estimated_minutes": "90",
                "priority": "高",
                "status": "未开始",
                "submission_requirements": "PDF提交",
                "source_text": "课程平台通知",
            },
            follow_redirects=False,
        )

        assert create_response.status_code == 302
        detail_path = create_response.headers["Location"]

        detail_response = client.get(detail_path)
        assert detail_response.status_code == 200
        assert "实验报告".encode("utf-8") in detail_response.data
        assert "数据库系统".encode("utf-8") in detail_response.data

        edit_response = client.post(
            f"{detail_path}/edit",
            data={
                "course_name": "数据库系统",
                "title": "实验报告终稿",
                "task_type": "实验",
                "description": "补充截图",
                "deadline": "2099-06-19T18:00",
                "estimated_minutes": "120",
                "priority": "中",
                "status": "进行中",
                "submission_requirements": "PDF提交",
                "source_text": "课程平台通知",
            },
            follow_redirects=True,
        )

        assert edit_response.status_code == 200
        assert "实验报告终稿".encode("utf-8") in edit_response.data
        assert "120 分钟".encode("utf-8") in edit_response.data

        status_response = client.post(
            f"{detail_path}/status",
            data={"status": "待提交"},
            follow_redirects=True,
        )

        assert status_response.status_code == 200
        assert "待提交".encode("utf-8") in status_response.data

        filtered_response = client.get("/tasks?course=数据库&status=待提交&priority=中")
        assert filtered_response.status_code == 200
        assert "实验报告终稿".encode("utf-8") in filtered_response.data

    restarted_app = create_app({"TESTING": True, "DATABASE": str(db_path)})
    with restarted_app.test_client() as client:
        persisted_response = client.get("/tasks")
        assert persisted_response.status_code == 200
        assert "实验报告终稿".encode("utf-8") in persisted_response.data

        delete_response = client.post(f"{detail_path}/delete", follow_redirects=True)
        assert delete_response.status_code == 200
        assert "任务已删除".encode("utf-8") in delete_response.data
        assert "实验报告终稿".encode("utf-8") not in delete_response.data


def test_task_validation_rejects_invalid_inputs(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        response = client.post(
            "/tasks",
            data={
                "title": "",
                "deadline": "not-a-date",
                "estimated_minutes": "-1",
                "priority": "最高",
                "status": "已逾期",
            },
        )

    assert response.status_code == 400
    assert "任务名称不能为空".encode("utf-8") in response.data
    assert "截止时间格式无效".encode("utf-8") in response.data
    assert "预计时长必须为非负整数".encode("utf-8") in response.data
    assert "任务状态无效".encode("utf-8") in response.data
    assert "优先级无效".encode("utf-8") in response.data
