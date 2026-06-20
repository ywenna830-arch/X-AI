import json
from datetime import date, datetime, timedelta

from .tasks import format_deadline, parse_deadline


PRIORITY_WEIGHT = {"高": 0, "中": 1, "低": 2}
PROGRESS_FACTORS = {"未开始": 1.0, "进行中": 0.6, "待提交": 0.15}
DEFAULT_SETTINGS = {"horizon_days": 7, "finish_early_days": 1, "weekend_extra": 0}
TEMPLATE_DEFAULT_MINUTES = 120
PLAN_STATUSES = ("未开始", "已完成", "部分完成", "未完成")
ACTIVE_PLAN_STATUSES = ("未开始", "部分完成", "未完成")
INCOMPLETE_REASONS = (
    "任务比预计更难",
    "临时没有时间",
    "今日安排过多",
    "任务要求发生变化",
    "已完成但忘记标记",
)

TASK_TEMPLATES = {
    "课程论文": [("资料整理", 0.2), ("提纲与论点", 0.2), ("初稿写作", 0.4), ("修改定稿", 0.15), ("检查提交", 0.05)],
    "PPT展示": [("内容整理", 0.25), ("结构脚本", 0.25), ("制作PPT", 0.35), ("演练检查", 0.15)],
    "编程作业": [("需求理解", 0.15), ("编码实现", 0.45), ("调试测试", 0.3), ("整理提交", 0.1)],
    "在线课程": [("观看课程", 0.6), ("笔记整理", 0.2), ("测验提交", 0.2)],
    "日常练习": [("完成练习", 0.7), ("订正总结", 0.3)],
    "考试复习": [("梳理范围", 0.2), ("分块复习", 0.45), ("刷题巩固", 0.25), ("考前检查", 0.1)],
    "通用任务": [("推进任务", 0.8), ("检查提交", 0.2)],
}


def init_plan_db(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS study_availability (
            study_date TEXT PRIMARY KEY,
            available_minutes INTEGER NOT NULL DEFAULT 0,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            CHECK (available_minutes >= 0),
            CHECK (is_blocked IN (0, 1))
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS planner_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            horizon_days INTEGER NOT NULL DEFAULT 7,
            finish_early_days INTEGER NOT NULL DEFAULT 1,
            weekend_extra INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            CHECK (horizon_days IN (7, 14)),
            CHECK (finish_early_days >= 0),
            CHECK (weekend_extra IN (0, 1))
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            scheduled_date TEXT NOT NULL,
            title TEXT NOT NULL,
            minutes INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '未开始',
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
        """
    )
    _ensure_plan_columns(db)
    db.execute("UPDATE plan_items SET status = '未开始' WHERE status IN ('待确认', '待执行')")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_item_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_item_id INTEGER,
            action TEXT NOT NULL,
            old_status TEXT NOT NULL DEFAULT '',
            new_status TEXT NOT NULL DEFAULT '',
            feedback_status TEXT NOT NULL DEFAULT '',
            completed_minutes INTEGER NOT NULL DEFAULT 0,
            completion_ratio INTEGER NOT NULL DEFAULT 0,
            incomplete_reason TEXT NOT NULL DEFAULT '',
            before_plan TEXT NOT NULL DEFAULT '',
            after_plan TEXT NOT NULL DEFAULT '',
            adjustment_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO planner_settings (
            id, horizon_days, finish_early_days, weekend_extra, updated_at
        )
        VALUES (1, 7, 1, 0, ?)
        """,
        (_now(),),
    )


def get_plan_settings(db):
    row = db.execute("SELECT * FROM planner_settings WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {
        "horizon_days": row["horizon_days"],
        "finish_early_days": row["finish_early_days"],
        "weekend_extra": row["weekend_extra"],
    }


def save_plan_settings(db, form, today=None):
    today = today or date.today()
    errors = []
    settings = _parse_settings(form, errors)
    days = _date_range(today, settings["horizon_days"])
    blocked_dates = set(form.getlist("blocked_dates"))
    availability = []

    for day in days:
        date_text = day.isoformat()
        raw_minutes = form.get(f"minutes_{date_text}", "0").strip() or "0"
        try:
            minutes = int(raw_minutes)
            if minutes < 0 or minutes > 1440:
                errors.append(f"{date_text} 可用时间必须在 0 到 1440 分钟之间。")
                minutes = 0
        except ValueError:
            errors.append(f"{date_text} 可用时间必须是整数分钟。")
            minutes = 0
        availability.append(
            {
                "date": date_text,
                "available_minutes": minutes,
                "is_blocked": 1 if date_text in blocked_dates else 0,
            }
        )

    if errors:
        return errors

    db.execute(
        """
        UPDATE planner_settings
        SET horizon_days = ?, finish_early_days = ?, weekend_extra = ?, updated_at = ?
        WHERE id = 1
        """,
        (
            settings["horizon_days"],
            settings["finish_early_days"],
            settings["weekend_extra"],
            _now(),
        ),
    )
    for day in availability:
        db.execute(
            """
            INSERT INTO study_availability (
                study_date, available_minutes, is_blocked, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(study_date) DO UPDATE SET
                available_minutes = excluded.available_minutes,
                is_blocked = excluded.is_blocked,
                updated_at = excluded.updated_at
            """,
            (day["date"], day["available_minutes"], day["is_blocked"], _now()),
        )
    db.commit()
    return []


def get_availability(db, horizon_days, today=None):
    today = today or date.today()
    rows = db.execute(
        "SELECT * FROM study_availability WHERE study_date >= ? ORDER BY study_date ASC",
        (today.isoformat(),),
    ).fetchall()
    by_date = {row["study_date"]: row for row in rows}
    availability = []
    for day in _date_range(today, horizon_days):
        date_text = day.isoformat()
        row = by_date.get(date_text)
        availability.append(
            {
                "date": date_text,
                "label": _day_label(day),
                "available_minutes": row["available_minutes"] if row else 120,
                "is_blocked": row["is_blocked"] if row else 0,
            }
        )
    return availability


def generate_plan(tasks, availability, settings, today=None):
    today = today or date.today()
    days = [_prepare_day(day, settings) for day in availability]
    day_map = {day["date"]: day for day in days}
    warnings = []

    for task in _sortable_tasks(tasks):
        deadline_dt = task.get("deadline_dt") or parse_deadline(task.get("deadline", ""))
        if deadline_dt is None:
            warnings.append(f"{task['title']} 缺少明确截止时间，未纳入本次规划。")
            continue
        deadline_date = deadline_dt.date()
        if deadline_date < today:
            warnings.append(f"{task['title']} 已超过截止日期，未安排到未来计划。")
            continue

        finish_by = deadline_date - timedelta(days=settings["finish_early_days"])
        if finish_by < today:
            finish_by = deadline_date
        candidate_days = [
            day
            for day in days
            if today <= date.fromisoformat(day["date"]) <= finish_by
            and date.fromisoformat(day["date"]) <= deadline_date
            and not day["is_blocked"]
        ]
        remaining_minutes = _remaining_minutes(task)
        steps = _build_steps(task, remaining_minutes)
        unscheduled = _schedule_steps(task, steps, candidate_days, settings)
        if unscheduled > 0:
            warnings.append(f"{task['title']} 仍有 {unscheduled} 分钟超出当前可用容量。")

    return {"days": days, "warnings": warnings, "items": _flatten_items(day_map)}


def save_plan_items(db, items):
    db.execute(
        f"DELETE FROM plan_items WHERE status IN ({','.join('?' for _ in ACTIVE_PLAN_STATUSES)})",
        ACTIVE_PLAN_STATUSES,
    )
    now = _now()
    for item in items:
        db.execute(
            """
            INSERT INTO plan_items (
                task_id, scheduled_date, title, minutes, reason, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, '未开始', ?)
            """,
            (
                item["task_id"],
                item["scheduled_date"],
                item["title"],
                item["minutes"],
                item["reason"],
                now,
            ),
        )
    db.commit()


def load_saved_plan(db, horizon_days, today=None):
    today = today or date.today()
    end_day = today + timedelta(days=horizon_days - 1)
    rows = db.execute(
        """
        SELECT plan_items.*, tasks.title AS task_title, tasks.course_name
        FROM plan_items
        JOIN tasks ON tasks.id = plan_items.task_id
        WHERE scheduled_date >= ? AND scheduled_date <= ?
        ORDER BY scheduled_date ASC, id ASC
        """,
        (today.isoformat(), end_day.isoformat()),
    ).fetchall()
    plan = {day.isoformat(): [] for day in _date_range(today, horizon_days)}
    for row in rows:
        plan.setdefault(row["scheduled_date"], []).append(dict(row))
    return plan


def record_plan_feedback(db, item_id, form):
    item = get_plan_item(db, item_id)
    if item is None:
        return ["计划项不存在。"]
    if item["status"] == "已完成":
        return ["已完成计划项会保留记录，不再修改。"]

    status = form.get("status", "").strip()
    if status not in PLAN_STATUSES:
        return ["计划项状态无效。"]
    if status == "已完成":
        completed_minutes = item["minutes"]
        completion_ratio = 100
    else:
        completed_minutes, completion_ratio, errors = _parse_completion(form, item["minutes"])
        if errors:
            return errors
    if status == "部分完成" and completed_minutes <= 0:
        return ["部分完成时请填写已完成分钟数或比例。"]
    incomplete_reason = form.get("incomplete_reason", "").strip()
    if status in ("部分完成", "未完成") and incomplete_reason not in INCOMPLETE_REASONS:
        return ["请选择未完成原因。"]
    if status == "未完成" and completed_minutes >= item["minutes"]:
        completed_minutes = 0
        completion_ratio = 0
    feedback_note = form.get("feedback_note", "").strip()
    now = _now()
    before_plan = _snapshot_items([item])

    db.execute(
        """
        UPDATE plan_items
        SET status = ?, completed_minutes = ?, completion_ratio = ?,
            incomplete_reason = ?, feedback_note = ?, updated_at = ?,
            completed_at = ?
        WHERE id = ?
        """,
        (
            status,
            completed_minutes,
            completion_ratio,
            incomplete_reason if status in ("部分完成", "未完成") else "",
            feedback_note,
            now,
            now if status == "已完成" else "",
            item_id,
        ),
    )
    _insert_history(
        db,
        item_id=item_id,
        action="反馈",
        old_status=item["status"],
        new_status=status,
        feedback_status=status,
        completed_minutes=completed_minutes,
        completion_ratio=completion_ratio,
        incomplete_reason=incomplete_reason,
        before_plan=before_plan,
        after_plan=_snapshot_query(db, "WHERE id = ?", (item_id,)),
        adjustment_reason=_feedback_adjustment_reason(status, incomplete_reason),
    )
    db.commit()
    return []


def delay_plan_item(db, item_id, settings):
    item = get_plan_item(db, item_id)
    if item is None:
        return ["计划项不存在。"]
    if item["status"] == "已完成":
        return ["已完成计划项会保留记录，不再延后。"]
    deadline_dt = parse_deadline(item["deadline"])
    if deadline_dt is None:
        return ["任务缺少明确截止时间，暂不能延后。"]
    new_date = date.fromisoformat(item["scheduled_date"]) + timedelta(days=1)
    if new_date > deadline_dt.date():
        return ["延后一天会超过任务截止日期，请调整预计时长或重新规划。"]
    availability = _availability_for_date(db, new_date, settings)
    used = _scheduled_minutes_on_date(db, new_date.isoformat(), exclude_item_id=item_id)
    if availability["is_blocked"] or used + item["minutes"] > availability["capacity_minutes"]:
        return ["延后一天会超过当天可用容量，请先调整可用时间或重新规划。"]

    before_plan = _snapshot_items([item])
    db.execute(
        """
        UPDATE plan_items
        SET scheduled_date = ?, reason = ?, adjustment_reason = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            new_date.isoformat(),
            f"{item['reason']} 调整：用户选择延后一天，且未超过截止日期和每日容量。",
            "用户选择延后一天。",
            _now(),
            item_id,
        ),
    )
    _insert_history(
        db,
        item_id=item_id,
        action="延后一天",
        old_status=item["status"],
        new_status=item["status"],
        before_plan=before_plan,
        after_plan=_snapshot_query(db, "WHERE id = ?", (item_id,)),
        adjustment_reason="用户选择延后一天，已检查截止日期和每日容量。",
    )
    db.commit()
    return []


def update_plan_item_minutes(db, item_id, minutes):
    item = get_plan_item(db, item_id)
    if item is None:
        return ["计划项不存在。"]
    if item["status"] == "已完成":
        return ["已完成计划项会保留记录，不再修改预计时长。"]
    try:
        minutes_value = int(minutes)
    except ValueError:
        return ["预计时长必须是正整数分钟。"]
    if minutes_value <= 0 or minutes_value > 1440:
        return ["预计时长必须在 1 到 1440 分钟之间。"]
    before_plan = _snapshot_items([item])
    db.execute(
        """
        UPDATE plan_items
        SET minutes = ?, adjustment_reason = ?, updated_at = ?
        WHERE id = ?
        """,
        (minutes_value, "用户修改预计时长。", _now(), item_id),
    )
    _insert_history(
        db,
        item_id=item_id,
        action="修改预计时长",
        old_status=item["status"],
        new_status=item["status"],
        before_plan=before_plan,
        after_plan=_snapshot_query(db, "WHERE id = ?", (item_id,)),
        adjustment_reason=f"预计时长由 {item['minutes']} 分钟调整为 {minutes_value} 分钟。",
    )
    db.commit()
    return []


def replan_remaining_items(db, settings, availability, today=None):
    today = today or date.today()
    active_items = _active_plan_items(db)
    if not active_items:
        return {"warnings": ["没有需要重新规划的剩余计划。"], "items": []}

    before_plan = _snapshot_items(active_items)
    tasks = [_task_from_plan_item(item) for item in active_items if _remaining_from_plan_item(item) > 0]
    if not tasks:
        _insert_history(
            db,
            item_id=None,
            action="重新规划",
            before_plan=before_plan,
            after_plan="[]",
            adjustment_reason="反馈显示剩余计划已完成，未新增安排。",
        )
        db.execute(
            f"DELETE FROM plan_items WHERE status IN ({','.join('?' for _ in ACTIVE_PLAN_STATUSES)})",
            ACTIVE_PLAN_STATUSES,
        )
        db.commit()
        return {"warnings": [], "items": []}
    adjusted_availability = _adjust_availability_for_feedback(availability, active_items)
    preview = generate_plan(tasks, adjusted_availability, settings, today=today)
    after_items = preview["items"]
    now = _now()

    db.execute(
        f"DELETE FROM plan_items WHERE status IN ({','.join('?' for _ in ACTIVE_PLAN_STATUSES)})",
        ACTIVE_PLAN_STATUSES,
    )
    for item in after_items:
        db.execute(
            """
            INSERT INTO plan_items (
                task_id, scheduled_date, title, minutes, reason, status,
                completed_minutes, completion_ratio, incomplete_reason,
                feedback_note, adjustment_reason, created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, '未开始', 0, 0, '', '', ?, ?, ?, '')
            """,
            (
                item["task_id"],
                item["scheduled_date"],
                item["title"],
                item["minutes"],
                f"{item['reason']} 调整：根据执行反馈重新安排剩余计划。",
                "根据执行反馈重新规划剩余计划。",
                now,
                now,
            ),
        )
    after_plan = _snapshot_query(
        db,
        f"WHERE status IN ({','.join('?' for _ in ACTIVE_PLAN_STATUSES)})",
        ACTIVE_PLAN_STATUSES,
    )
    _insert_history(
        db,
        item_id=None,
        action="重新规划",
        before_plan=before_plan,
        after_plan=after_plan,
        adjustment_reason="保留已完成记录，仅根据反馈重新安排剩余计划。",
    )
    db.commit()
    return {"warnings": preview["warnings"], "items": after_items}


def get_plan_item(db, item_id):
    row = db.execute(
        """
        SELECT plan_items.*, tasks.title AS task_title, tasks.deadline,
            tasks.priority, tasks.task_type, tasks.description, tasks.course_name
        FROM plan_items
        JOIN tasks ON tasks.id = plan_items.task_id
        WHERE plan_items.id = ?
        """,
        (item_id,),
    ).fetchone()
    return row


def _ensure_plan_columns(db):
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(plan_items)").fetchall()
    }
    columns = {
        "completed_minutes": "INTEGER NOT NULL DEFAULT 0",
        "completion_ratio": "INTEGER NOT NULL DEFAULT 0",
        "incomplete_reason": "TEXT NOT NULL DEFAULT ''",
        "feedback_note": "TEXT NOT NULL DEFAULT ''",
        "adjustment_reason": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "completed_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing_columns:
            db.execute(f"ALTER TABLE plan_items ADD COLUMN {name} {definition}")


def _parse_completion(form, planned_minutes):
    errors = []
    raw_minutes = form.get("completed_minutes", "").strip()
    raw_ratio = form.get("completion_ratio", "").strip()
    completed_minutes = 0
    completion_ratio = 0

    if raw_ratio and (not raw_minutes or raw_minutes == "0"):
        try:
            completion_ratio = int(raw_ratio)
        except ValueError:
            errors.append("已完成比例必须是0到100之间的整数。")
        if completion_ratio < 0 or completion_ratio > 100:
            errors.append("已完成比例必须是0到100之间的整数。")
        completed_minutes = int(round(planned_minutes * completion_ratio / 100))
    elif raw_minutes:
        try:
            completed_minutes = int(raw_minutes)
        except ValueError:
            errors.append("已完成分钟数必须是非负整数。")
        if completed_minutes < 0 or completed_minutes > planned_minutes:
            errors.append("已完成分钟数不能小于0或超过计划分钟数。")
        if planned_minutes > 0:
            completion_ratio = int(round(completed_minutes * 100 / planned_minutes))
    return completed_minutes, completion_ratio, errors


def _feedback_adjustment_reason(status, incomplete_reason):
    if status == "已完成":
        return "用户标记已完成，保留该计划记录。"
    if status == "部分完成":
        return f"根据部分完成反馈调整剩余计划：{incomplete_reason}。"
    if status == "未完成":
        return f"根据未完成反馈保留剩余工作量：{incomplete_reason}。"
    return "用户更新计划项状态。"


def _active_plan_items(db):
    rows = db.execute(
        f"""
        SELECT plan_items.*, tasks.title AS task_title, tasks.deadline,
            tasks.priority, tasks.task_type, tasks.description, tasks.course_name
        FROM plan_items
        JOIN tasks ON tasks.id = plan_items.task_id
        WHERE plan_items.status IN ({','.join('?' for _ in ACTIVE_PLAN_STATUSES)})
        ORDER BY plan_items.scheduled_date ASC, plan_items.id ASC
        """,
        ACTIVE_PLAN_STATUSES,
    ).fetchall()
    return rows


def _task_from_plan_item(item):
    remaining = _remaining_from_plan_item(item)
    return {
        "id": item["task_id"],
        "title": item["title"],
        "task_type": item["task_type"],
        "description": item["description"],
        "deadline": item["deadline"],
        "deadline_dt": parse_deadline(item["deadline"]),
        "estimated_minutes": remaining,
        "priority": item["priority"],
        "status": "未开始",
        "allow_small_workload": True,
    }


def _remaining_from_plan_item(item):
    completed = min(item["completed_minutes"], item["minutes"])
    remaining = max(0, item["minutes"] - completed)
    if item["status"] == "未完成":
        remaining = item["minutes"]
    if item["incomplete_reason"] in ("任务比预计更难", "任务要求发生变化"):
        remaining = int(round(remaining * 1.25))
    if item["incomplete_reason"] == "已完成但忘记标记":
        remaining = 0
    return max(0, remaining)


def _adjust_availability_for_feedback(availability, active_items):
    reduce_capacity = any(item["incomplete_reason"] == "今日安排过多" for item in active_items)
    if not reduce_capacity:
        return availability
    adjusted = []
    for day in availability:
        adjusted.append({**day, "available_minutes": int(day["available_minutes"] * 0.8)})
    return adjusted


def _availability_for_date(db, target_date, settings):
    row = db.execute(
        "SELECT * FROM study_availability WHERE study_date = ?",
        (target_date.isoformat(),),
    ).fetchone()
    day = {
        "date": target_date.isoformat(),
        "label": _day_label(target_date),
        "available_minutes": row["available_minutes"] if row else 120,
        "is_blocked": row["is_blocked"] if row else 0,
    }
    return _prepare_day(day, settings)


def _scheduled_minutes_on_date(db, scheduled_date, exclude_item_id=None):
    row = db.execute(
        """
        SELECT COALESCE(SUM(minutes), 0) AS total
        FROM plan_items
        WHERE scheduled_date = ? AND id != ?
        """,
        (scheduled_date, exclude_item_id or 0),
    ).fetchone()
    return row["total"]


def _snapshot_items(items):
    return json.dumps([_snapshot_item(item) for item in items], ensure_ascii=False)


def _snapshot_query(db, where_sql, values):
    rows = db.execute(f"SELECT * FROM plan_items {where_sql} ORDER BY scheduled_date ASC, id ASC", values).fetchall()
    return _snapshot_items(rows)


def _snapshot_item(item):
    return {
        "id": item["id"],
        "task_id": item["task_id"],
        "scheduled_date": item["scheduled_date"],
        "title": item["title"],
        "minutes": item["minutes"],
        "status": item["status"],
        "completed_minutes": item["completed_minutes"],
        "completion_ratio": item["completion_ratio"],
        "incomplete_reason": item["incomplete_reason"],
        "adjustment_reason": item["adjustment_reason"],
    }


def _insert_history(
    db,
    item_id,
    action,
    old_status="",
    new_status="",
    feedback_status="",
    completed_minutes=0,
    completion_ratio=0,
    incomplete_reason="",
    before_plan="",
    after_plan="",
    adjustment_reason="",
):
    db.execute(
        """
        INSERT INTO plan_item_history (
            plan_item_id, action, old_status, new_status, feedback_status,
            completed_minutes, completion_ratio, incomplete_reason,
            before_plan, after_plan, adjustment_reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            action,
            old_status,
            new_status,
            feedback_status,
            completed_minutes,
            completion_ratio,
            incomplete_reason,
            before_plan,
            after_plan,
            adjustment_reason,
            _now(),
        ),
    )


def _parse_settings(form, errors):
    try:
        horizon_days = int(form.get("horizon_days", "7"))
    except ValueError:
        horizon_days = 7
        errors.append("计划范围必须是 7 天或 14 天。")
    if horizon_days not in (7, 14):
        errors.append("计划范围必须是 7 天或 14 天。")
        horizon_days = 7

    try:
        finish_early_days = int(form.get("finish_early_days", "1"))
    except ValueError:
        finish_early_days = 1
        errors.append("提前完成天数必须是非负整数。")
    if finish_early_days < 0 or finish_early_days > 14:
        errors.append("提前完成天数必须在 0 到 14 天之间。")
        finish_early_days = 1

    return {
        "horizon_days": horizon_days,
        "finish_early_days": finish_early_days,
        "weekend_extra": 1 if form.get("weekend_extra") == "1" else 0,
    }


def _prepare_day(day, settings):
    day_date = date.fromisoformat(day["date"])
    available = int(day["available_minutes"])
    if settings["weekend_extra"] and day_date.weekday() >= 5:
        available = int(round(available * 1.2))
    capacity = 0 if day["is_blocked"] else int(available * 0.8)
    return {
        **day,
        "available_minutes": available,
        "capacity_minutes": capacity,
        "scheduled_minutes": 0,
        "items": [],
    }


def _sortable_tasks(tasks):
    active_tasks = [task for task in tasks if task.get("status") != "已完成"]
    return sorted(
        active_tasks,
        key=lambda task: (
            task.get("deadline_dt") or parse_deadline(task.get("deadline", "")) or datetime.max,
            PRIORITY_WEIGHT.get(task.get("priority", "中"), 1),
            task.get("id", 0),
        ),
    )


def _remaining_minutes(task):
    estimated = int(task.get("estimated_minutes") or 0) or TEMPLATE_DEFAULT_MINUTES
    factor = PROGRESS_FACTORS.get(task.get("status", "未开始"), 1.0)
    if task.get("allow_small_workload"):
        return max(1, int(round(estimated * factor)))
    return max(15, int(round(estimated * factor)))


def _build_steps(task, total_minutes):
    template = TASK_TEMPLATES[_select_template(task)]
    steps = []
    remaining = total_minutes
    for index, (title, weight) in enumerate(template):
        if index == len(template) - 1:
            minutes = remaining
        else:
            minutes = min(remaining, max(1, int(round(total_minutes * weight))))
            remaining -= minutes
        steps.append({"title": title, "minutes": minutes})
    return [step for step in steps if step["minutes"] > 0]


def _select_template(task):
    text = f"{task.get('task_type', '')} {task.get('title', '')} {task.get('description', '')}"
    for keyword, template_name in (
        ("课程论文", "课程论文"),
        ("论文", "课程论文"),
        ("小论文", "课程论文"),
        ("PPT", "PPT展示"),
        ("展示", "PPT展示"),
        ("演示", "PPT展示"),
        ("编程", "编程作业"),
        ("代码", "编程作业"),
        ("程序", "编程作业"),
        ("在线课程", "在线课程"),
        ("网课", "在线课程"),
        ("练习", "日常练习"),
        ("考试", "考试复习"),
        ("复习", "考试复习"),
    ):
        if keyword in text:
            return template_name
    return "通用任务"


def _schedule_steps(task, steps, candidate_days, settings):
    remaining = sum(step["minutes"] for step in steps)
    for step in steps:
        step_left = step["minutes"]
        while step_left > 0:
            open_days = [day for day in candidate_days if day["scheduled_minutes"] < day["capacity_minutes"]]
            if not open_days:
                return step_left + sum(
                    later_step["minutes"]
                    for later_step in steps[steps.index(step) + 1 :]
                )
            per_day_target = max(20, int((remaining + len(open_days) - 1) / len(open_days)))
            target_day = open_days[0]
            free_minutes = target_day["capacity_minutes"] - target_day["scheduled_minutes"]
            chunk = min(step_left, free_minutes, per_day_target)
            if chunk <= 0:
                return step_left
            item = _plan_item(task, step["title"], chunk, target_day["date"], settings)
            target_day["items"].append(item)
            target_day["scheduled_minutes"] += chunk
            step_left -= chunk
            remaining -= chunk
    return 0


def _plan_item(task, step_title, minutes, scheduled_date, settings):
    deadline_label = format_deadline(task.get("deadline_dt") or parse_deadline(task.get("deadline", "")))
    progress_text = {
        "未开始": "从完整工作量开始安排",
        "进行中": "按剩余约60%工作量安排",
        "待提交": "仅保留检查和提交工作量",
    }.get(task.get("status", "未开始"), "按当前剩余工作量安排")
    reason = (
        f"截止 {deadline_label}；优先级{task.get('priority', '中')}；{progress_text}；"
        f"每日最多占用80%可用时间；提前{settings['finish_early_days']}天完成。"
    )
    return {
        "task_id": task["id"],
        "task_title": task["title"],
        "scheduled_date": scheduled_date,
        "title": f"{task['title']} - {step_title}",
        "minutes": minutes,
        "reason": reason,
    }


def _flatten_items(day_map):
    items = []
    for date_text in sorted(day_map):
        items.extend(day_map[date_text]["items"])
    return items


def _date_range(start_day, days):
    return [start_day + timedelta(days=offset) for offset in range(days)]


def _day_label(day):
    names = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    return f"{day.strftime('%m-%d')} {names[day.weekday()]}"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
