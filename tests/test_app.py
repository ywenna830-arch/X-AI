import io
import json
import sys
import types
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import create_app
from app.ai_parser import AIParseError, _call_chat_model, parse_model_json, parse_text_notice
from app.planner import generate_plan
from app.reminders import countdown_label, dashboard_data as reminder_dashboard_data
from app.tasks import get_db


def make_app(tmp_path, extra_config=None):
    config = {
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "AI_API_KEY": "",
        "AI_API_BASE_URL": "",
        "AI_MODEL": "",
    }
    if extra_config:
        config.update(extra_config)
    return create_app(config)


def test_home_page_loads():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "课迹".encode("utf-8") in response.data
    assert "今日学习总览".encode("utf-8") in response.data
    assert "任务提醒与日历".encode("utf-8") in response.data
    assert "导出未完成任务ICS".encode("utf-8") in response.data


def test_stage_one_pages_load():
    app = create_app()

    pages = {
        "/tasks/new": "课迹助手",
        "/tasks/confirm": "AI识别确认",
        "/tasks": "任务中心",
        "/plan": "智能计划",
    }

    with app.test_client() as client:
        for path, expected_text in pages.items():
            response = client.get(path)

            assert response.status_code == 200
            assert expected_text.encode("utf-8") in response.data
    with app.test_client() as client:
        confirm_response = client.get("/tasks/confirm")
    assert "暂无草稿".encode("utf-8") in confirm_response.data


def test_add_task_page_shows_conversation_entry_points(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        response = client.get("/tasks/new")

    assert response.status_code == 200
    assert "课迹助手".encode("utf-8") in response.data
    assert "给课迹发送消息".encode("utf-8") in response.data
    assert "不想使用对话？展开手动填写".encode("utf-8") in response.data
    assert "上传图片".encode("utf-8") in response.data
    assert "上传Word".encode("utf-8") in response.data
    assert "上传PDF".encode("utf-8") in response.data
    assert "提取图片文字".encode("utf-8") in response.data
    assert "仅支持DOCX".encode("utf-8") in response.data


def test_app_reads_render_environment_and_creates_database_parent(monkeypatch, tmp_path):
    db_path = tmp_path / "render-data" / "tasks.sqlite3"
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("AI_API_KEY", "test-ai-key")
    monkeypatch.setenv("AI_API_BASE_URL", "https://api.example.test/v1/chat/completions")
    monkeypatch.setenv("AI_MODEL", "deepseek-test")
    monkeypatch.setenv("AI_TIMEOUT", "12")
    monkeypatch.setenv("AI_DEMO_MODE", "1")
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    app = create_app({"TESTING": True})

    assert app.config["SECRET_KEY"] == "test-secret"
    assert app.config["AI_API_KEY"] == "test-ai-key"
    assert app.config["AI_API_BASE_URL"] == "https://api.example.test/v1/chat/completions"
    assert app.config["AI_MODEL"] == "deepseek-test"
    assert app.config["AI_TIMEOUT"] == 12
    assert app.config["AI_DEMO_MODE"] is True
    assert app.config["APP_TIMEZONE"] == "UTC"
    assert app.config["DATABASE"] == str(db_path)
    assert db_path.parent.exists()
    assert db_path.exists()


def test_render_and_docker_configuration_are_safe_for_deployment():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    render_config = Path("render.yaml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "gunicorn" in requirements
    assert "runtime: docker" in render_config
    assert "dockerfilePath: ./Dockerfile" in render_config
    assert 'sh -c "gunicorn run:app --bind 0.0.0.0:$PORT"' in render_config
    assert "DATABASE_PATH" in render_config
    assert "/var/data/tasks.sqlite3" in render_config
    assert "AI_API_KEY" not in render_config
    assert "SECRET_KEY" not in render_config
    assert "tesseract-ocr" in dockerfile
    assert "tesseract-ocr-chi-sim" in dockerfile
    assert "gunicorn run:app --bind 0.0.0.0:${PORT}" in dockerfile
    assert ".env" in dockerignore
    assert "instance/" in dockerignore
    assert ".env" in gitignore


def test_chat_api_greeting_returns_chat_without_creating_task(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert response.get_json()["type"] == "chat"
    assert response.get_json()["reply"]
    assert _task_count(app) == 0


def test_chat_api_thanks_and_capability_questions_are_chat(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        thanks = client.post("/api/chat", json={"message": "谢谢"})
        capability = client.post("/api/chat", json={"message": "你能做什么"})

    assert thanks.status_code == 200
    assert thanks.get_json()["type"] == "chat"
    assert capability.status_code == 200
    assert capability.get_json()["type"] == "chat"
    assert _task_count(app) == 0


def test_chat_api_passes_previous_turn_history_to_model(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "AI_API_KEY": "test-key",
            "AI_API_BASE_URL": "https://api.example.test/v1/chat/completions",
            "AI_MODEL": "deepseek-test",
        },
    )
    calls = []

    def fake_call(message, notice_date, history):
        calls.append({"message": message, "history": history})
        if message == "老师说明天交作业":
            return json.dumps({"type": "chat", "reply": "还缺课程名称，我先记住这条线索。"}, ensure_ascii=False)
        return json.dumps({"type": "chat", "reply": "上一轮说的是明天交作业，现在补充了高等数学。"}, ensure_ascii=False)

    monkeypatch.setattr("app.ai_parser._call_chat_model", fake_call)

    with app.test_client() as client:
        first = client.post("/api/chat", json={"message": "老师说明天交作业"})
        second = client.post("/api/chat", json={"message": "高等数学"})
        with client.session_transaction() as session_data:
            history = session_data["chat_history"]

    assert first.status_code == 200
    assert first.get_json()["type"] == "chat"
    assert second.status_code == 200
    assert calls[0]["history"] == []
    assert calls[1]["history"] == [
        {"role": "user", "content": "老师说明天交作业"},
        {"role": "assistant", "content": "还缺课程名称，我先记住这条线索。"},
    ]
    assert history[-2:] == [
        {"role": "user", "content": "高等数学"},
        {"role": "assistant", "content": "上一轮说的是明天交作业，现在补充了高等数学。"},
    ]


def test_call_chat_model_includes_system_history_and_current_json(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "AI_API_KEY": "test-key",
            "AI_API_BASE_URL": "https://api.example.test/v1/chat/completions",
            "AI_MODEL": "deepseek-test",
        },
    )
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            content = json.dumps({"type": "chat", "reply": "收到。"}, ensure_ascii=False)
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("app.ai_parser.request.urlopen", fake_urlopen)

    history = [
        {"role": "user", "content": "老师说明天交作业"},
        {"role": "assistant", "content": "还缺课程名称。"},
    ]
    with app.app_context():
        _call_chat_model("高等数学", "", history)

    messages = captured["payload"]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1:3] == history
    assert messages[-1]["role"] == "user"
    current_message = json.loads(messages[-1]["content"])
    assert current_message["message"] == "高等数学"
    assert "required_task_fields" in current_message


def test_chat_history_trims_to_recent_eight_messages(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "AI_API_KEY": "test-key",
            "AI_API_BASE_URL": "https://api.example.test/v1/chat/completions",
            "AI_MODEL": "deepseek-test",
        },
    )

    def fake_call(message, notice_date, history):
        return json.dumps({"type": "chat", "reply": f"回复：{message}"}, ensure_ascii=False)

    monkeypatch.setattr("app.ai_parser._call_chat_model", fake_call)

    with app.test_client() as client:
        for index in range(6):
            response = client.post("/api/chat", json={"message": f"第{index}轮"})
            assert response.status_code == 200
        with client.session_transaction() as session_data:
            history = session_data["chat_history"]

    assert len(history) == 8
    assert history[0] == {"role": "user", "content": "第2轮"}
    assert history[-1] == {"role": "assistant", "content": "回复：第5轮"}


def test_chat_reset_clears_history_and_pending_task(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        with client.session_transaction() as session_data:
            session_data["chat_history"] = [{"role": "user", "content": "旧消息"}]
            session_data["pending_ai_task"] = {"form_data": {"title": "旧草稿"}}

        response = client.post("/api/chat/reset")

        with client.session_transaction() as session_data:
            assert "chat_history" not in session_data
            assert "pending_ai_task" not in session_data

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_chat_api_task_returns_preview_and_pending_confirm_without_save(tmp_path):
    app = make_app(tmp_path, {"AI_DEMO_MODE": True})
    message = "课程：软件工程；任务：完成测试报告；2026-07-01 18:00前提交"

    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": message})
        confirm = client.get("/tasks/confirm")

    data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["type"] == "task"
    assert data["task_preview"]
    assert data["confirm_url"] == "/tasks/confirm"
    assert "完成测试报告".encode("utf-8") in confirm.data
    assert "确认并保存".encode("utf-8") in confirm.data
    assert _task_count(app) == 0


def test_chat_api_short_task_text_returns_task_without_direct_save(tmp_path):
    app = make_app(tmp_path, {"AI_DEMO_MODE": True})

    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "高数第三章作业周五前交"})

    assert response.status_code == 200
    assert response.get_json()["type"] == "task"
    assert response.get_json()["task_preview"]
    assert _task_count(app) == 0


def test_chat_api_preserves_import_source_for_confirm(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "IMPORT_UPLOAD_DIR": str(tmp_path / "imports"),
            "AI_DEMO_MODE": True,
        },
    )
    monkeypatch.setattr(
        "app.file_importer.extract_docx_text",
        lambda path: "课程：软件工程\n任务：DOCX导入任务\n请在2099-06-20 20:00前提交。",
    )

    with app.test_client() as client:
        import_response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_docx_bytes()), "notice.docx")},
            content_type="multipart/form-data",
        )
        chat_response = client.post(
            "/api/chat",
            json={
                "message": "课程：软件工程\n任务：DOCX导入任务\n请在2099-06-20 20:00前提交。",
                "source_type": "Word",
                "source_filename": "notice.docx",
                "source_pages": "",
            },
        )
        confirm = client.get("/tasks/confirm")

    assert import_response.status_code == 200
    assert chat_response.status_code == 200
    assert "notice.docx".encode("utf-8") in confirm.data
    assert "DOCX导入任务".encode("utf-8") in confirm.data


def test_chat_api_rejects_empty_non_json_and_too_long_messages(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        empty = client.post("/api/chat", json={"message": "   "})
        non_json = client.post("/api/chat", data="hello")
        too_long = client.post("/api/chat", json={"message": "x" * 10001})

    assert empty.status_code == 400
    assert empty.get_json()["ok"] is False
    assert non_json.status_code == 400
    assert non_json.get_json()["ok"] is False
    assert too_long.status_code == 400
    assert too_long.get_json()["ok"] is False


def test_chat_api_invalid_model_json_returns_structured_error(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "AI_API_KEY": "test-key",
            "AI_API_BASE_URL": "https://api.example.test/v1/chat/completions",
            "AI_MODEL": "deepseek-test",
        },
    )
    monkeypatch.setattr("app.ai_parser._call_chat_model", lambda message, notice_date, history: "not-json")

    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 502
    assert response.is_json
    assert response.get_json()["ok"] is False
    assert "合法JSON" in response.get_json()["error"]


def test_chat_api_model_request_failure_returns_structured_error(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "AI_API_KEY": "test-key",
            "AI_API_BASE_URL": "https://api.example.test/v1/chat/completions",
            "AI_MODEL": "deepseek-test",
        },
    )

    def fail_request(message, notice_date, history):
        raise AIParseError("AI接口请求失败，请稍后重试。")

    monkeypatch.setattr("app.ai_parser._call_chat_model", fail_request)

    with app.test_client() as client:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 502
    assert response.is_json
    assert response.get_json() == {"ok": False, "error": "AI接口请求失败，请稍后重试。"}


def test_manual_entry_form_still_creates_task(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        page_response = client.get("/tasks/new")
        assert page_response.status_code == 200
        assert 'action="/tasks"'.encode("utf-8") in page_response.data

        create_response = client.post(
            "/tasks",
            data={
                "course_name": "软件工程",
                "title": "需求分析作业",
                "task_type": "作业",
                "description": "完成第一版需求说明",
                "deadline": "2099-06-20T20:00",
                "estimated_minutes": "60",
                "priority": "中",
                "status": "未开始",
                "submission_requirements": "提交PDF",
                "source_text": "老师课堂通知",
            },
            follow_redirects=True,
        )

    assert create_response.status_code == 200
    assert "需求分析作业".encode("utf-8") in create_response.data
    assert "软件工程".encode("utf-8") in create_response.data


def test_task_create_success_clears_pending_ai_task_and_confirm_is_empty(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        with client.session_transaction() as session_data:
            session_data["pending_ai_task"] = {"form_data": {"title": "旧草稿"}}

        create_response = client.post(
            "/tasks",
            data={
                "course_name": "软件工程",
                "title": "保存后清草稿",
                "task_type": "作业",
                "description": "验证保存成功后清理session草稿",
                "deadline": "2099-06-20T20:00",
                "estimated_minutes": "60",
                "priority": "中",
                "status": "未开始",
            },
        )
        confirm_response = client.get("/tasks/confirm")

        with client.session_transaction() as session_data:
            assert "pending_ai_task" not in session_data

    assert create_response.status_code == 302
    assert confirm_response.status_code == 200
    assert "暂无草稿".encode("utf-8") in confirm_response.data


def test_task_create_validation_failure_keeps_pending_ai_task(tmp_path):
    app = make_app(tmp_path)
    pending_task = {"form_data": {"title": "保留草稿"}}

    with app.test_client() as client:
        with client.session_transaction() as session_data:
            session_data["pending_ai_task"] = pending_task

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

        with client.session_transaction() as session_data:
            assert session_data["pending_ai_task"] == pending_task

    assert response.status_code == 400
    assert "任务名称不能为空".encode("utf-8") in response.data


def test_ai_text_parse_complete_notice_requires_confirmation_before_save(tmp_path):
    app = make_app(tmp_path, {"AI_DEMO_MODE": True})
    notice = (
        "课程：软件工程\n"
        "作业：需求分析报告\n"
        "请在2099-06-20 20:00前提交PDF到课程平台。"
        "需要材料：课堂笔记、案例资料。预计90分钟。"
    )

    with app.test_client() as client:
        parse_response = client.post(
            "/tasks/ai/parse",
            data={"notice_text": notice},
        )
        tasks_response = client.get("/tasks")

        save_response = client.post(
            "/tasks",
            data={
                "course_name": "软件工程",
                "title": "需求分析报告",
                "task_type": "作业",
                "description": notice,
                "deadline": "2099-06-20T20:00",
                "estimated_minutes": "90",
                "priority": "中",
                "status": "未开始",
                "submission_requirements": "请在2099-06-20 20:00前提交PDF到课程平台",
                "required_materials": "课堂笔记\n案例资料",
                "suggested_materials": "",
                "source_text": notice,
                "source_quote": "请在2099-06-20 20:00前提交PDF到课程平台",
                "source_type": "AI文字解析",
                "confidence": "本地演示",
                "uncertain_fields": "",
            },
            follow_redirects=True,
        )

    assert parse_response.status_code == 200
    assert "结构化任务卡片".encode("utf-8") in parse_response.data
    assert "确认并保存".encode("utf-8") in parse_response.data
    assert "需求分析报告".encode("utf-8") not in tasks_response.data
    assert save_response.status_code == 200
    assert "需求分析报告".encode("utf-8") in save_response.data
    assert "课堂笔记".encode("utf-8") in save_response.data
    assert "AI文字解析".encode("utf-8") in save_response.data


def test_ai_text_parse_missing_deadline_marks_uncertain(tmp_path):
    app = make_app(tmp_path, {"AI_DEMO_MODE": True})
    notice = "课程：数据库系统\n作业：索引实验\n完成实验报告并上传课程平台。"

    with app.test_client() as client:
        response = client.post("/tasks/ai/parse", data={"notice_text": notice})

    assert response.status_code == 200
    assert "需要重点确认：deadline".encode("utf-8") in response.data
    assert "信息缺失".encode("utf-8") in response.data


def test_ai_text_parse_relative_date_with_notice_date(tmp_path):
    app = make_app(tmp_path, {"AI_DEMO_MODE": True})
    notice = "课程：算法设计\n作业：动态规划练习\n下周三18:00前提交。"

    with app.app_context():
        result = parse_text_notice(notice, "2026-06-18")

    assert result["data"]["deadline"] == "2026-06-24T18:00"
    assert result["data"]["confidence"] == "AI推断"


def test_ai_parse_rejects_illegal_json():
    try:
        parse_model_json("not-json")
    except AIParseError as exc:
        assert "合法JSON" in exc.message
    else:
        raise AssertionError("非法JSON应被拒绝")


def test_health_check_returns_ok():
    app = create_app()

    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "service": "课迹"}


def test_planner_orders_multiple_tasks_by_deadline():
    today = date.today()
    settings = {"horizon_days": 7, "finish_early_days": 0, "weekend_extra": 0}
    availability = _availability(today, [120, 120, 120, 120, 120, 120, 120])
    tasks = [
        _planner_task(1, "期末论文", "课程论文", today + timedelta(days=5), 180, "中"),
        _planner_task(2, "算法练习", "日常练习", today + timedelta(days=2), 80, "高"),
    ]

    plan = generate_plan(tasks, availability, settings, today=today)
    items = plan["items"]

    assert items
    assert items[0]["task_id"] == 2
    assert all(date.fromisoformat(item["scheduled_date"]) <= today + timedelta(days=5) for item in items)
    assert any("优先级高" in item["reason"] for item in items if item["task_id"] == 2)


def test_planner_spreads_large_task_across_days():
    today = date.today()
    settings = {"horizon_days": 7, "finish_early_days": 0, "weekend_extra": 0}
    availability = _availability(today, [120, 120, 120, 120, 120, 120, 120])
    task = _planner_task(1, "编程大作业", "编程作业", today + timedelta(days=4), 260, "高")

    plan = generate_plan([task], availability, settings, today=today)
    scheduled_dates = {item["scheduled_date"] for item in plan["items"]}

    assert len(scheduled_dates) > 1
    assert all(day["scheduled_minutes"] <= day["capacity_minutes"] for day in plan["days"])


def test_planner_warns_when_capacity_is_not_enough():
    today = date.today()
    settings = {"horizon_days": 2, "finish_early_days": 0, "weekend_extra": 0}
    availability = _availability(today, [30, 30])
    task = _planner_task(1, "考试复习", "考试复习", today + timedelta(days=1), 300, "高")

    plan = generate_plan([task], availability, settings, today=today)

    assert any("超出当前可用容量" in warning for warning in plan["warnings"])


def test_planner_respects_blocked_day():
    today = date.today()
    settings = {"horizon_days": 4, "finish_early_days": 0, "weekend_extra": 0}
    availability = _availability(today, [120, 120, 120, 120], blocked_indexes={0})
    task = _planner_task(1, "PPT展示", "PPT展示", today + timedelta(days=3), 120, "中")

    plan = generate_plan([task], availability, settings, today=today)

    assert plan["days"][0]["items"] == []
    assert all(item["scheduled_date"] != today.isoformat() for item in plan["items"])


def test_plan_page_generates_preview_and_saves_confirmed_items(tmp_path):
    app = make_app(tmp_path)
    deadline = (date.today() + timedelta(days=3)).strftime("%Y-%m-%dT20:00")

    with app.test_client() as client:
        create_response = client.post(
            "/tasks",
            data={
                "course_name": "软件工程",
                "title": "编程作业",
                "task_type": "编程作业",
                "description": "实现并测试功能",
                "deadline": deadline,
                "estimated_minutes": "120",
                "priority": "高",
                "status": "未开始",
            },
        )
        assert create_response.status_code == 302

        preview_response = client.post("/plan/generate")
        save_response = client.post(
            "/plan/confirm",
            follow_redirects=True,
        )

    assert preview_response.status_code == 200
    assert "待确认计划".encode("utf-8") in preview_response.data
    assert b"plan_payload" not in preview_response.data
    assert "安排原因".encode("utf-8") not in preview_response.data
    assert save_response.status_code == 200
    assert "计划已确认并保存".encode("utf-8") in save_response.data
    assert "编程作业".encode("utf-8") in save_response.data


def test_plan_confirm_ignores_forged_payload_and_saves_server_plan(tmp_path):
    app = make_app(tmp_path)
    deadline_day = date.today() + timedelta(days=3)
    deadline = deadline_day.strftime("%Y-%m-%dT20:00")

    with app.test_client() as client:
        create_response = client.post(
            "/tasks",
            data={
                "course_name": "软件工程",
                "title": "安全修复作业",
                "task_type": "编程作业",
                "description": "修复确认流程",
                "deadline": deadline,
                "estimated_minutes": "120",
                "priority": "高",
                "status": "未开始",
            },
        )
        task_id = int(create_response.headers["Location"].rsplit("/", 1)[-1])
        forged_payload = (
            '[{"task_id":999999,"scheduled_date":"2099-12-31",'
            '"title":"伪造计划","minutes":999999,"reason":"篡改"}]'
        )

        save_response = client.post(
            "/plan/confirm",
            data={"plan_payload": forged_payload},
            follow_redirects=True,
        )

    with app.app_context():
        rows = get_db().execute(
            "SELECT task_id, scheduled_date, title, minutes, reason FROM plan_items"
        ).fetchall()

    assert save_response.status_code == 200
    assert rows
    assert all(row["task_id"] == task_id for row in rows)
    assert all(row["minutes"] < 999999 for row in rows)
    assert all(row["title"] != "伪造计划" for row in rows)
    assert all(date.fromisoformat(row["scheduled_date"]) <= deadline_day for row in rows)
    assert any("每日最多占用80%可用时间" in row["reason"] for row in rows)


def test_image_import_accepts_png_jpg_and_jpeg(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "IMPORT_UPLOAD_DIR": str(tmp_path / "imports"),
            "AI_DEMO_MODE": True,
        },
    )
    monkeypatch.setattr(
        "app.file_importer.extract_image_text",
        lambda path: "课程：软件工程\n作业：图片导入任务\n请在2099-06-20 20:00前提交。",
    )

    with app.test_client() as client:
        for filename, content in (
            ("notice.png", _png_bytes()),
            ("notice.jpg", _jpeg_bytes()),
            ("notice.jpeg", _jpeg_bytes()),
        ):
            response = client.post(
                "/tasks/import",
                data={"attachment": (io.BytesIO(content), filename)},
                content_type="multipart/form-data",
            )

            assert response.status_code == 200
            assert "图片文字已提取".encode("utf-8") in response.data
            assert "图片导入任务".encode("utf-8") in response.data


def test_fake_image_extension_is_rejected_and_temp_file_cleaned(monkeypatch, tmp_path):
    upload_dir = tmp_path / "imports"
    app = make_app(tmp_path, {"IMPORT_UPLOAD_DIR": str(upload_dir)})
    called = {"ocr": False}

    def fail_if_called(path):
        called["ocr"] = True
        return "不应调用"

    monkeypatch.setattr("app.file_importer.extract_image_text", fail_if_called)

    with app.test_client() as client:
        response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(b"not-a-real-image"), "fake.png")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert "有效PNG".encode("utf-8") in response.data
    assert not called["ocr"]
    assert _directory_is_empty(upload_dir)


def test_docx_import_extracts_body_and_table(monkeypatch, tmp_path):
    app = make_app(tmp_path, {"IMPORT_UPLOAD_DIR": str(tmp_path / "imports")})

    def fake_docx_extract(path):
        return "正文段落\n课程 | 软件工程\n任务 | DOCX表格任务"

    monkeypatch.setattr("app.file_importer.extract_docx_text", fake_docx_extract)

    with app.test_client() as client:
        response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_docx_bytes()), "notice.docx")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert "Word文字已提取".encode("utf-8") in response.data
    assert "正文段落".encode("utf-8") in response.data
    assert "DOCX表格任务".encode("utf-8") in response.data


def test_pdf_import_extracts_text_and_page_sources(monkeypatch, tmp_path):
    app = make_app(tmp_path, {"IMPORT_UPLOAD_DIR": str(tmp_path / "imports")})
    monkeypatch.setitem(sys.modules, "fitz", _fake_fitz_module(["第一页任务文字", "第二页提交要求"]))

    with app.test_client() as client:
        response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_pdf_bytes()), "notice.pdf")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    assert "PDF文字已提取".encode("utf-8") in response.data
    assert "第一页任务文字".encode("utf-8") in response.data
    assert "第1页、第2页".encode("utf-8") in response.data


def test_scanned_pdf_without_text_shows_clear_message(monkeypatch, tmp_path):
    app = make_app(tmp_path, {"IMPORT_UPLOAD_DIR": str(tmp_path / "imports")})
    monkeypatch.setitem(sys.modules, "fitz", _fake_fitz_module(["", ""]))

    with app.test_client() as client:
        response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_pdf_bytes()), "scan.pdf")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert "扫描型PDF".encode("utf-8") in response.data
    assert "不对整份PDF执行OCR".encode("utf-8") in response.data


def test_import_rejects_oversized_and_unsupported_files(tmp_path):
    app = make_app(
        tmp_path,
        {
            "IMPORT_UPLOAD_DIR": str(tmp_path / "imports"),
            "MAX_IMPORT_FILE_BYTES": 10,
        },
    )

    with app.test_client() as client:
        oversized = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_png_bytes() + b"0123456789"), "big.png")},
            content_type="multipart/form-data",
        )
        unsupported = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(b"hello"), "notice.doc")},
            content_type="multipart/form-data",
        )

    assert oversized.status_code == 400
    assert "文件大小不能超过".encode("utf-8") in oversized.data
    assert unsupported.status_code == 400
    assert "仅支持 PNG".encode("utf-8") in unsupported.data


def test_import_failure_cleans_temp_files(monkeypatch, tmp_path):
    upload_dir = tmp_path / "imports"
    app = make_app(tmp_path, {"IMPORT_UPLOAD_DIR": str(upload_dir)})

    def broken_ocr(path):
        raise RuntimeError("ocr failed")

    monkeypatch.setattr("app.file_importer.extract_image_text", broken_ocr)

    with app.test_client() as client:
        response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_png_bytes()), "notice.png")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert "文件解析失败".encode("utf-8") in response.data
    assert _directory_is_empty(upload_dir)


def test_imported_text_can_be_edited_and_sent_to_existing_ai_confirm(monkeypatch, tmp_path):
    app = make_app(
        tmp_path,
        {
            "IMPORT_UPLOAD_DIR": str(tmp_path / "imports"),
            "AI_DEMO_MODE": True,
        },
    )
    monkeypatch.setattr(
        "app.file_importer.extract_image_text",
        lambda path: "课程：软件工程\n作业：原始图片任务\n2099-06-20 20:00前提交。",
    )

    with app.test_client() as client:
        import_response = client.post(
            "/tasks/import",
            data={"attachment": (io.BytesIO(_png_bytes()), "notice.png")},
            content_type="multipart/form-data",
        )
        parse_response = client.post(
            "/tasks/ai/parse",
            data={
                "notice_text": "课程：软件工程\n作业：修改后的图片任务\n请在2099-06-20 20:00前提交。",
                "source_type": "图片",
                "source_filename": "notice.png",
            },
        )

    assert import_response.status_code == 200
    assert parse_response.status_code == 200
    assert "结构化任务卡片".encode("utf-8") in parse_response.data
    assert "修改后的图片任务".encode("utf-8") in parse_response.data
    assert "notice.png".encode("utf-8") in parse_response.data


def test_frontend_task_success_clears_attachment_context_but_chat_does_not():
    script = Path("app/static/js/main.js").read_text(encoding="utf-8")

    task_branch_start = script.index('if (data.type === "task") {')
    task_branch_end = script.index('            chatInput.value = "";', task_branch_start)
    task_branch = script[task_branch_start:task_branch_end]

    assert "const clearAttachmentContext = () => {" in script
    assert 'sourceType.value = "";' in script
    assert 'sourceFilename.value = "";' in script
    assert 'sourcePages.value = "";' in script
    assert 'document.querySelector("[data-import-preview]")' in script
    assert "preview.remove();" in script
    assert "clearAttachmentContext();" in task_branch
    assert script.count("clearAttachmentContext();") == 1


def test_frontend_reset_button_clears_thread_and_restores_welcome():
    template = Path("app/templates/add_task.html").read_text(encoding="utf-8")
    script = Path("app/static/js/main.js").read_text(encoding="utf-8")

    assert "data-chat-reset" in template
    assert 'fetch("/api/chat/reset", {method: "POST"})' in script
    assert "const restoreWelcomeMessage = () => {" in script
    assert 'chatThread.innerHTML = "";' in script
    assert "你好，我可以帮你整理课程通知、作业和考试安排。" in script


def test_replan_after_unfinished_feedback_replaces_only_active_items(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "未完成重规划", date.today() + timedelta(days=5), 180)
        rows = _plan_rows(app)
        first_id = rows[0]["id"]

        feedback_response = client.post(
            f"/plan/items/{first_id}/feedback",
            data={"status": "未完成", "incomplete_reason": "临时没有时间"},
            follow_redirects=True,
        )
        replan_response = client.post("/plan/replan", follow_redirects=True)

    replanned_rows = _plan_rows(app)
    history_rows = _history_rows(app)

    assert feedback_response.status_code == 200
    assert replan_response.status_code == 200
    assert replanned_rows
    assert first_id not in {row["id"] for row in replanned_rows}
    assert any(row["action"] == "重新规划" for row in history_rows)


def test_partial_completion_replans_only_remaining_minutes(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "部分完成作业", date.today() + timedelta(days=5), 160)
        before_rows = _plan_rows(app)
        first = before_rows[0]
        before_total = sum(row["minutes"] for row in before_rows)
        client.post(
            f"/plan/items/{first['id']}/feedback",
            data={
                "status": "部分完成",
                "completion_ratio": "50",
                "incomplete_reason": "任务比预计更难",
            },
            follow_redirects=True,
        )
        client.post("/plan/replan", follow_redirects=True)

    after_total = sum(row["minutes"] for row in _plan_rows(app))

    assert after_total > before_total - first["minutes"]
    assert after_total < before_total + first["minutes"]


def test_update_estimated_minutes_affects_replan(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "时长调整作业", date.today() + timedelta(days=5), 120)
        before_rows = _plan_rows(app)
        first = before_rows[0]
        before_total = sum(row["minutes"] for row in before_rows)
        client.post(
            f"/plan/items/{first['id']}/minutes",
            data={"minutes": str(first["minutes"] + 45)},
            follow_redirects=True,
        )
        client.post("/plan/replan", follow_redirects=True)

    after_total = sum(row["minutes"] for row in _plan_rows(app))

    assert after_total == before_total + 45


def test_replan_reports_capacity_risk(tmp_path):
    app = make_app(tmp_path)
    today = date.today()

    with app.test_client() as client:
        _save_availability(client, today, [180, 180, 180], horizon_days=7)
        _create_task_and_confirm_plan(client, "容量风险作业", today + timedelta(days=2), 300)
        _save_availability(client, today, [30, 30, 30], horizon_days=7)
        response = client.post("/plan/replan", follow_redirects=True)

    assert "超出当前可用容量".encode("utf-8") in response.data


def test_completed_plan_item_is_preserved_during_replan(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "完成保护作业", date.today() + timedelta(days=5), 160)
        completed = _plan_rows(app)[0]
        client.post(
            f"/plan/items/{completed['id']}/feedback",
            data={"status": "已完成"},
            follow_redirects=True,
        )
        client.post("/plan/replan", follow_redirects=True)

    row = _plan_row(app, completed["id"])

    assert row is not None
    assert row["scheduled_date"] == completed["scheduled_date"]
    assert row["minutes"] == completed["minutes"]
    assert row["status"] == "已完成"
    assert row["completed_minutes"] == completed["minutes"]


def test_replan_does_not_schedule_after_deadline(tmp_path):
    app = make_app(tmp_path)
    deadline_day = date.today() + timedelta(days=2)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "截止保护作业", deadline_day, 180)
        first = _plan_rows(app)[0]
        client.post(
            f"/plan/items/{first['id']}/feedback",
            data={"status": "未完成", "incomplete_reason": "临时没有时间"},
            follow_redirects=True,
        )
        client.post("/plan/replan", follow_redirects=True)

    assert all(date.fromisoformat(row["scheduled_date"]) <= deadline_day for row in _plan_rows(app))


def test_delay_one_day_cannot_exceed_deadline(tmp_path):
    app = make_app(tmp_path)
    deadline_day = date.today()

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "当天截止作业", deadline_day, 60)
        item = _plan_rows(app)[0]
        response = client.post(f"/plan/items/{item['id']}/delay", follow_redirects=True)

    row = _plan_row(app, item["id"])

    assert "超过任务截止日期".encode("utf-8") in response.data
    assert row["scheduled_date"] == item["scheduled_date"]


def test_dashboard_classifies_today_tomorrow_upcoming_overdue_and_plan_items(tmp_path):
    app = make_app(tmp_path)
    today = date.today()
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)

    with app.app_context():
        db = get_db()
        today_id = _insert_task(db, "今日截止任务", today.strftime("%Y-%m-%dT23:59"))
        tomorrow_id = _insert_task(db, "明日截止任务", (today + timedelta(days=1)).strftime("%Y-%m-%dT12:00"))
        _insert_task(db, "即将截止任务", (today + timedelta(days=5)).strftime("%Y-%m-%dT12:00"))
        _insert_task(db, "已逾期任务", (today - timedelta(days=1)).strftime("%Y-%m-%dT12:00"))
        _insert_plan_item(db, today_id, today, "今日计划项", 45)
        _insert_plan_item(db, tomorrow_id, today + timedelta(days=1), "明日计划项", 30)
        data = reminder_dashboard_data(db, now=now)

    assert {task["title"] for task in data["today_tasks"]} == {"今日截止任务"}
    assert {task["title"] for task in data["tomorrow_tasks"]} == {"明日截止任务"}
    assert {item["title"] for item in data["today_plan_items"]} == {"今日计划项"}
    assert {item["title"] for item in data["tomorrow_plan_items"]} == {"明日计划项"}
    assert any(task["title"] == "即将截止任务" for task in data["upcoming_tasks"])
    assert any(task["title"] == "已逾期任务" for task in data["overdue_tasks"])


def test_dashboard_uses_app_timezone_for_classification_reminders_and_countdown(tmp_path):
    app = make_app(tmp_path, {"APP_TIMEZONE": "UTC"})
    now = datetime(2026, 6, 20, 16, 30, tzinfo=timezone.utc)

    with app.app_context():
        db = get_db()
        today_id = _insert_task(db, "应用时区今日任务", "2026-06-20T18:00")
        tomorrow_id = _insert_task(db, "应用时区明日任务", "2026-06-21T00:15")
        _insert_task(db, "应用时区逾期任务", "2026-06-19T23:59")
        _insert_plan_item(db, today_id, date(2026, 6, 20), "应用时区今日计划项", 45)
        _insert_plan_item(db, tomorrow_id, date(2026, 6, 21), "应用时区明日计划项", 30)
        db.execute(
            "INSERT INTO task_reminders (task_id, days_before, created_at, updated_at) VALUES (?, 1, 'now', 'now')",
            (tomorrow_id,),
        )
        db.commit()
        data = reminder_dashboard_data(db, now=now)

    assert {task["title"] for task in data["today_tasks"]} == {"应用时区今日任务"}
    assert {task["title"] for task in data["tomorrow_tasks"]} == {"应用时区明日任务"}
    assert {item["title"] for item in data["today_plan_items"]} == {"应用时区今日计划项"}
    assert {item["title"] for item in data["tomorrow_plan_items"]} == {"应用时区明日计划项"}
    assert {reminder["title"] for reminder in data["reminders"]} == {"应用时区明日任务"}
    assert data["today_tasks"][0]["countdown"] == "今天18:00截止"
    assert {task["title"] for task in data["overdue_tasks"]} == {"应用时区逾期任务"}


def test_completed_tasks_do_not_create_active_reminders(tmp_path):
    app = make_app(tmp_path)
    today = date.today()
    now = datetime.combine(today, datetime.min.time()).replace(hour=10)

    with app.app_context():
        db = get_db()
        task_id = _insert_task(
            db,
            "已完成提醒任务",
            today.strftime("%Y-%m-%dT23:59"),
            status="已完成",
        )
        db.execute(
            "INSERT INTO task_reminders (task_id, days_before, created_at, updated_at) VALUES (?, 1, 'now', 'now')",
            (task_id,),
        )
        db.commit()
        data = reminder_dashboard_data(db, now=now)

    assert data["reminders"] == []
    assert all(task["title"] != "已完成提醒任务" for task in data["upcoming_tasks"])


def test_reminder_settings_save_and_render(tmp_path):
    app = make_app(tmp_path)
    deadline = (date.today() + timedelta(days=7)).strftime("%Y-%m-%dT20:00")

    with app.test_client() as client:
        task_id = _create_task(client, "提醒设置任务", deadline)
        response = client.post(
            f"/tasks/{task_id}/reminders",
            data={"reminder_days": ["7", "1"], "custom_days": "5"},
            follow_redirects=True,
        )

    with app.app_context():
        rows = get_db().execute(
            "SELECT days_before FROM task_reminders WHERE task_id = ? ORDER BY days_before DESC",
            (task_id,),
        ).fetchall()

    assert response.status_code == 200
    assert "提醒设置已保存".encode("utf-8") in response.data
    assert [row["days_before"] for row in rows] == [7, 5, 1]
    assert "提前7天".encode("utf-8") in response.data


def test_invalid_custom_reminder_days_are_rejected(tmp_path):
    app = make_app(tmp_path)
    deadline = (date.today() + timedelta(days=7)).strftime("%Y-%m-%dT20:00")

    with app.test_client() as client:
        task_id = _create_task(client, "非法提醒任务", deadline)
        response = client.post(
            f"/tasks/{task_id}/reminders",
            data={"custom_days": "366"},
            follow_redirects=True,
        )

    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM task_reminders WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]

    assert response.status_code == 200
    assert "提醒提前天数必须是0到365之间的整数".encode("utf-8") in response.data
    assert count == 0


def test_duplicate_reminder_settings_are_not_generated_twice(tmp_path):
    app = make_app(tmp_path)
    deadline = (date.today() + timedelta(days=3)).strftime("%Y-%m-%dT20:00")

    with app.test_client() as client:
        task_id = _create_task(client, "重复提醒任务", deadline)
        client.post(
            f"/tasks/{task_id}/reminders",
            data={"reminder_days": ["3", "3"], "custom_days": "3"},
            follow_redirects=True,
        )

    with app.app_context():
        rows = get_db().execute(
            "SELECT days_before FROM task_reminders WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        data = reminder_dashboard_data(get_db(), now=datetime.now())

    assert [row["days_before"] for row in rows] == [3]
    assert sum(1 for reminder in data["reminders"] if reminder["task_id"] == task_id) <= 1


def test_countdown_labels_are_clear():
    now = datetime(2026, 6, 20, 10, 0)

    assert countdown_label(datetime(2026, 6, 20, 18, 0), now) == "今天18:00截止"
    assert countdown_label(datetime(2026, 6, 22, 13, 0), now) == "剩余2天3小时"
    assert countdown_label(datetime(2026, 6, 19, 9, 0), now) == "已逾期1天"


def test_single_task_ics_export_contains_required_fields_and_alarm(tmp_path):
    app = make_app(tmp_path)
    deadline_day = date.today() + timedelta(days=3)

    with app.test_client() as client:
        _create_task_and_confirm_plan(client, "ICS单任务", deadline_day, 60)
        task_id = _task_id_by_title(app, "ICS单任务")
        client.post(f"/tasks/{task_id}/reminders", data={"reminder_days": ["1"]})
        response = client.get(f"/tasks/{task_id}/calendar.ics")

    data = response.data.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in data
    assert "BEGIN:VEVENT" in data
    assert "UID:task-" in data
    assert "DTSTAMP:" in data
    assert "SUMMARY:ICS单任务" in data
    assert "DESCRIPTION:" in data
    assert "DTSTART;TZID=Asia/Shanghai:" in data
    assert "DTEND;TZID=Asia/Shanghai:" in data
    assert "BEGIN:VALARM" in data
    assert "TRIGGER:-P1D" in data


def test_single_task_ics_export_without_deadline_or_plan_shows_message(tmp_path):
    app = make_app(tmp_path)

    with app.app_context():
        task_id = _insert_task(get_db(), "无截止无计划任务", "")

    with app.test_client() as client:
        response = client.get(f"/tasks/{task_id}/calendar.ics", follow_redirects=True)

    data = response.data.decode("utf-8")

    assert response.status_code == 200
    assert not response.headers["Content-Type"].startswith("text/calendar")
    assert "没有未完成计划项，也没有有效截止时间" in data
    assert "DTSTART" not in data
    assert date.today().strftime("%Y%m%dT235900") not in data


def test_all_tasks_ics_export_excludes_completed_tasks_by_default(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        active_id = _create_task(client, "未完成导出任务", "2099-06-20T20:00")
        completed_id = _create_task(client, "已完成导出任务", "2099-06-21T20:00", status="已完成")
        client.post(f"/tasks/{active_id}/reminders", data={"reminder_days": ["0"]})
        response = client.get("/calendar/tasks.ics")

    data = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "未完成导出任务" in data
    assert "已完成导出任务" not in data
    assert f"task-{completed_id}" not in data


def test_all_tasks_ics_skips_tasks_without_deadline_or_plan(tmp_path):
    app = make_app(tmp_path)

    with app.app_context():
        db = get_db()
        _insert_task(db, "批量跳过无日期任务", "")
        _insert_task(db, "批量保留有效任务", "2099-06-20T20:00")

    with app.test_client() as client:
        response = client.get("/calendar/tasks.ics")

    data = response.data.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/calendar")
    assert "批量保留有效任务" in data
    assert "批量跳过无日期任务" not in data


def test_all_tasks_ics_without_exportable_events_shows_message(tmp_path):
    app = make_app(tmp_path)

    with app.app_context():
        _insert_task(get_db(), "批量无有效事件任务", "")

    with app.test_client() as client:
        response = client.get("/calendar/tasks.ics", follow_redirects=True)

    data = response.data.decode("utf-8")

    assert response.status_code == 200
    assert not response.headers["Content-Type"].startswith("text/calendar")
    assert "当前没有可导出的日历事件" in data
    assert "DTSTART" not in data


def test_ics_escapes_chinese_punctuation_backslash_and_newlines(tmp_path):
    app = make_app(tmp_path)

    with app.app_context():
        db = get_db()
        task_id = _insert_task(
            db,
            "中文,分号;反斜杠\\任务",
            "2099-06-20T20:00",
            description="第一行\n第二行,含逗号;含分号\\含反斜杠",
            submission_requirements="提交,PDF;保留\\记录",
        )
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        ics = app.test_client().get(f"/tasks/{task_id}/calendar.ics").data.decode("utf-8")

    assert task is not None
    unfolded = _unfold_ics(ics)

    assert "SUMMARY:中文\\,分号\\;反斜杠\\\\任务" in unfolded
    assert "第一行\\n第二行\\,含逗号\\;含分号\\\\含反斜杠" in unfolded
    assert "提交\\,PDF\\;保留\\\\记录" in unfolded


def test_ics_uses_timezone_and_default_time_rules_for_task_without_plan(tmp_path):
    app = make_app(tmp_path)

    with app.app_context():
        db = get_db()
        task_id = _insert_task(db, "无计划项导出", "2099-06-20")
        db.commit()

    with app.test_client() as client:
        response = client.get(f"/tasks/{task_id}/calendar.ics")

    data = response.data.decode("utf-8")

    assert "X-WR-TIMEZONE:Asia/Shanghai" in data
    assert "DTSTART;TZID=Asia/Shanghai:20990620T235900" in data
    assert "DTEND;TZID=Asia/Shanghai:20990621T002900" in data


def test_plan_item_ics_uses_default_start_and_plan_minutes(tmp_path):
    app = make_app(tmp_path)
    today = date.today()

    with app.app_context():
        db = get_db()
        task_id = _insert_task(db, "计划项时间导出", (today + timedelta(days=3)).strftime("%Y-%m-%dT20:00"))
        _insert_plan_item(db, task_id, today + timedelta(days=1), "计划项时间导出 - 推进", 45)

    with app.test_client() as client:
        response = client.get(f"/tasks/{task_id}/calendar.ics")

    data = response.data.decode("utf-8")
    start_text = (today + timedelta(days=1)).strftime("%Y%m%dT090000")
    end_text = (today + timedelta(days=1)).strftime("%Y%m%dT094500")

    assert f"DTSTART;TZID=Asia/Shanghai:{start_text}" in data
    assert f"DTEND;TZID=Asia/Shanghai:{end_text}" in data


def test_ics_excludes_completed_plan_items_but_keeps_deadline_event(tmp_path):
    app = make_app(tmp_path)
    scheduled_day = date(2099, 6, 19)

    with app.app_context():
        db = get_db()
        task_id = _insert_task(db, "已完成计划项导出任务", "2099-06-20T20:00")
        completed_item_id = _insert_plan_item(
            db,
            task_id,
            scheduled_day,
            "已完成计划项不应导出",
            45,
            status="已完成",
        )

    with app.test_client() as client:
        response = client.get(f"/tasks/{task_id}/calendar.ics")

    data = _unfold_ics(response.data.decode("utf-8"))

    assert response.status_code == 200
    assert f"task-{task_id}-plan-{completed_item_id}@keji" not in data
    assert "已完成计划项不应导出" not in data
    assert f"UID:task-{task_id}@keji" in data
    assert "DTSTART;TZID=Asia/Shanghai:20990620T200000" in data


def test_ics_includes_incomplete_plan_items(tmp_path):
    app = make_app(tmp_path)
    scheduled_day = date(2099, 6, 19)

    with app.app_context():
        db = get_db()
        task_id = _insert_task(db, "未完成计划项导出任务", "2099-06-20T20:00")
        item_id = _insert_plan_item(
            db,
            task_id,
            scheduled_day,
            "未完成计划项仍导出",
            45,
            status="未完成",
        )

    with app.test_client() as client:
        response = client.get(f"/tasks/{task_id}/calendar.ics")

    data = _unfold_ics(response.data.decode("utf-8"))

    assert response.status_code == 200
    assert f"UID:task-{task_id}-plan-{item_id}@keji" in data
    assert "未完成计划项仍导出" in data
    assert "DTSTART;TZID=Asia/Shanghai:20990619T090000" in data


def test_delete_task_cleans_task_reminders(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        task_id = _create_task(client, "删除提醒清理任务", "2099-06-20T20:00")
        client.post(f"/tasks/{task_id}/reminders", data={"reminder_days": ["7", "1"]})
        response = client.post(f"/tasks/{task_id}/delete", follow_redirects=True)

    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM task_reminders WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]

    assert response.status_code == 200
    assert "任务已删除".encode("utf-8") in response.data
    assert count == 0


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


def _create_task(client, title, deadline, status="未开始"):
    response = client.post(
        "/tasks",
        data={
            "course_name": "软件工程",
            "title": title,
            "task_type": "作业",
            "description": title,
            "deadline": deadline,
            "estimated_minutes": "60",
            "priority": "中",
            "status": status,
            "submission_requirements": "提交PDF",
        },
    )
    assert response.status_code == 302
    return int(response.headers["Location"].rsplit("/", 1)[-1])


def _insert_task(
    db,
    title,
    deadline,
    status="未开始",
    description=None,
    submission_requirements="提交PDF",
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = db.execute(
        """
        INSERT INTO tasks (
            course_name, title, task_type, description, deadline,
            estimated_minutes, priority, status, submission_requirements,
            required_materials, suggested_materials, source_type, source_text,
            source_quote, source_filename, source_pages, confidence,
            uncertain_fields, created_at, updated_at
        )
        VALUES ('软件工程', ?, '作业', ?, ?, 60, '中', ?, ?, '', '', '手动填写', '',
            '', '', '', '人工录入', '', ?, ?)
        """,
        (title, description or title, deadline, status, submission_requirements, now, now),
    )
    db.commit()
    return cursor.lastrowid


def _insert_plan_item(db, task_id, scheduled_day, title, minutes, status="未开始"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = db.execute(
        """
        INSERT INTO plan_items (
            task_id, scheduled_date, title, minutes, reason, status, created_at
        )
        VALUES (?, ?, ?, ?, '测试计划项', ?, ?)
        """,
        (task_id, scheduled_day.isoformat(), title, minutes, status, now),
    )
    db.commit()
    return cursor.lastrowid


def _task_id_by_title(app, title):
    with app.app_context():
        row = get_db().execute("SELECT id FROM tasks WHERE title = ?", (title,)).fetchone()
    assert row is not None
    return row["id"]


def _task_count(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]


def _unfold_ics(text):
    return text.replace("\r\n ", "")


def _planner_task(task_id, title, task_type, deadline_day, minutes, priority, status="未开始"):
    deadline_dt = datetime.combine(deadline_day, datetime.min.time()).replace(hour=20)
    return {
        "id": task_id,
        "title": title,
        "task_type": task_type,
        "description": title,
        "deadline": deadline_dt.strftime("%Y-%m-%dT%H:%M"),
        "deadline_dt": deadline_dt,
        "estimated_minutes": minutes,
        "priority": priority,
        "status": status,
    }


def _availability(start_day, minutes_by_day, blocked_indexes=None):
    blocked_indexes = blocked_indexes or set()
    return [
        {
            "date": (start_day + timedelta(days=index)).isoformat(),
            "label": str(index),
            "available_minutes": minutes,
            "is_blocked": 1 if index in blocked_indexes else 0,
        }
        for index, minutes in enumerate(minutes_by_day)
    ]


def _create_task_and_confirm_plan(client, title, deadline_day, minutes):
    response = client.post(
        "/tasks",
        data={
            "course_name": "软件工程",
            "title": title,
            "task_type": "编程作业",
            "description": title,
            "deadline": deadline_day.strftime("%Y-%m-%dT20:00"),
            "estimated_minutes": str(minutes),
            "priority": "高",
            "status": "未开始",
        },
    )
    assert response.status_code == 302
    confirm_response = client.post("/plan/confirm", follow_redirects=True)
    assert confirm_response.status_code == 200


def _save_availability(client, start_day, minutes_by_day, horizon_days=7):
    data = {"horizon_days": str(horizon_days), "finish_early_days": "0"}
    for index in range(horizon_days):
        day = start_day + timedelta(days=index)
        minutes = minutes_by_day[index] if index < len(minutes_by_day) else minutes_by_day[-1]
        data[f"minutes_{day.isoformat()}"] = str(minutes)
    response = client.post("/plan/settings", data=data, follow_redirects=True)
    assert response.status_code == 200


def _plan_rows(app):
    with app.app_context():
        return get_db().execute("SELECT * FROM plan_items ORDER BY id ASC").fetchall()


def _plan_row(app, item_id):
    with app.app_context():
        return get_db().execute("SELECT * FROM plan_items WHERE id = ?", (item_id,)).fetchone()


def _history_rows(app):
    with app.app_context():
        return get_db().execute("SELECT * FROM plan_item_history ORDER BY id ASC").fetchall()


def _png_bytes():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _pdf_bytes():
    return b"%PDF-1.4\n% test pdf\n"


def _docx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", "<w:document></w:document>")
    return buffer.getvalue()


def _fake_fitz_module(page_texts):
    class FakePage:
        def __init__(self, text):
            self.text = text

        def get_text(self, mode):
            assert mode == "text"
            return self.text

    class FakeDocument:
        def __iter__(self):
            return iter(FakePage(text) for text in page_texts)

        def close(self):
            pass

    return types.SimpleNamespace(open=lambda path: FakeDocument())


def _directory_is_empty(path):
    if not path.exists():
        return True
    return not any(path.iterdir())
