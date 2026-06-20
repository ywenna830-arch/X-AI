from datetime import date, datetime, timedelta

from app import create_app
from app.ai_parser import AIParseError, parse_model_json, parse_text_notice
from app.planner import generate_plan
from app.tasks import get_db


def make_app(tmp_path, extra_config=None):
    config = {"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite3")}
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
    with app.test_client() as client:
        confirm_response = client.get("/tasks/confirm")
    assert "暂无草稿".encode("utf-8") in confirm_response.data


def test_add_task_page_shows_conversation_entry_points(tmp_path):
    app = make_app(tmp_path)

    with app.test_client() as client:
        response = client.get("/tasks/new")

    assert response.status_code == 200
    assert "对话输入区域".encode("utf-8") in response.data
    assert "把老师的文字通知粘贴给我".encode("utf-8") in response.data
    assert "文字解析已接入".encode("utf-8") in response.data
    assert "上传图片".encode("utf-8") in response.data
    assert "上传Word".encode("utf-8") in response.data
    assert "上传PDF".encode("utf-8") in response.data
    assert "阶段6接入".encode("utf-8") in response.data


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
