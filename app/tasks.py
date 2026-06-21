import sqlite3
from datetime import datetime, timedelta

from flask import current_app, g

from .time_utils import app_now


ALLOWED_STATUSES = ("未开始", "进行中", "待提交", "已完成")
ALLOWED_PRIORITIES = ("低", "中", "高")
DATE_FORMAT = "%Y-%m-%dT%H:%M"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    with app.app_context():
        db = get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_name TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                estimated_minutes INTEGER NOT NULL DEFAULT 0,
                priority TEXT NOT NULL DEFAULT '中',
                status TEXT NOT NULL DEFAULT '未开始',
                submission_requirements TEXT NOT NULL DEFAULT '',
                required_materials TEXT NOT NULL DEFAULT '',
                suggested_materials TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '手动填写',
                source_text TEXT NOT NULL DEFAULT '',
                source_quote TEXT NOT NULL DEFAULT '',
                source_filename TEXT NOT NULL DEFAULT '',
                source_pages TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT '人工录入',
                uncertain_fields TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (estimated_minutes >= 0),
                CHECK (priority IN ('低', '中', '高')),
                CHECK (status IN ('未开始', '进行中', '待提交', '已完成'))
            )
            """
        )
        _ensure_task_columns(db)
        db.commit()


def validate_task_form(form):
    errors = []
    title = form.get("title", "").strip()
    deadline = form.get("deadline", "").strip()
    estimated_minutes = form.get("estimated_minutes", "0").strip() or "0"
    priority = form.get("priority", "中").strip()
    status = form.get("status", "未开始").strip()

    if not title:
        errors.append("任务名称不能为空。")

    if deadline:
        try:
            datetime.strptime(deadline, DATE_FORMAT)
        except ValueError:
            errors.append("截止时间格式无效。")

    try:
        estimated_value = int(estimated_minutes)
        if estimated_value < 0:
            errors.append("预计时长必须为非负整数。")
    except ValueError:
        estimated_value = 0
        errors.append("预计时长必须为非负整数。")

    if status not in ALLOWED_STATUSES:
        errors.append("任务状态无效。")

    if priority not in ALLOWED_PRIORITIES:
        errors.append("优先级无效。")

    data = {
        "course_name": form.get("course_name", "").strip(),
        "title": title,
        "task_type": form.get("task_type", "").strip(),
        "description": form.get("description", "").strip(),
        "deadline": deadline,
        "estimated_minutes": estimated_value,
        "priority": priority,
        "status": status,
        "submission_requirements": form.get("submission_requirements", "").strip(),
        "required_materials": form.get("required_materials", "").strip(),
        "suggested_materials": form.get("suggested_materials", "").strip(),
        "source_type": form.get("source_type", "手动填写").strip() or "手动填写",
        "source_text": form.get("source_text", "").strip(),
        "source_quote": form.get("source_quote", "").strip(),
        "source_filename": form.get("source_filename", "").strip(),
        "source_pages": form.get("source_pages", "").strip(),
        "confidence": form.get("confidence", "人工录入").strip() or "人工录入",
        "uncertain_fields": form.get("uncertain_fields", "").strip(),
    }
    return errors, data


def create_task(data):
    now = _now()
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO tasks (
            course_name, title, task_type, description, deadline,
            estimated_minutes, priority, status, submission_requirements,
            required_materials, suggested_materials, source_type, source_text,
            source_quote, source_filename, source_pages, confidence,
            uncertain_fields, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["course_name"],
            data["title"],
            data["task_type"],
            data["description"],
            data["deadline"],
            data["estimated_minutes"],
            data["priority"],
            data["status"],
            data["submission_requirements"],
            data["required_materials"],
            data["suggested_materials"],
            data["source_type"],
            data["source_text"],
            data["source_quote"],
            data["source_filename"],
            data["source_pages"],
            data["confidence"],
            data["uncertain_fields"],
            now,
            now,
        ),
    )
    db.commit()
    return cursor.lastrowid


def update_task(task_id, data):
    db = get_db()
    db.execute(
        """
        UPDATE tasks
        SET course_name = ?, title = ?, task_type = ?, description = ?,
            deadline = ?, estimated_minutes = ?, priority = ?, status = ?,
            submission_requirements = ?, required_materials = ?,
            suggested_materials = ?, source_type = ?, source_text = ?,
            source_quote = ?, source_filename = ?, source_pages = ?,
            confidence = ?, uncertain_fields = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            data["course_name"],
            data["title"],
            data["task_type"],
            data["description"],
            data["deadline"],
            data["estimated_minutes"],
            data["priority"],
            data["status"],
            data["submission_requirements"],
            data["required_materials"],
            data["suggested_materials"],
            data["source_type"],
            data["source_text"],
            data["source_quote"],
            data["source_filename"],
            data["source_pages"],
            data["confidence"],
            data["uncertain_fields"],
            _now(),
            task_id,
        ),
    )
    db.commit()


def update_task_status(task_id, status):
    if status not in ALLOWED_STATUSES:
        raise ValueError("任务状态无效。")
    db = get_db()
    db.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), task_id),
    )
    db.commit()


def delete_task(task_id):
    db = get_db()
    db.execute("DELETE FROM task_reminders WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()


def get_task(task_id):
    task = get_db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        return None
    return decorate_task(task)


def list_tasks(filters=None):
    filters = filters or {}
    clauses = []
    values = []

    course = filters.get("course", "").strip()
    if course:
        clauses.append("course_name LIKE ?")
        values.append(f"%{course}%")

    status = filters.get("status", "").strip()
    if status and status != "全部":
        clauses.append("status = ?")
        values.append(status)

    priority = filters.get("priority", "").strip()
    if priority and priority != "全部":
        clauses.append("priority = ?")
        values.append(priority)

    deadline = filters.get("deadline", "").strip()
    now = app_now()
    if deadline == "today":
        clauses.append("deadline >= ? AND deadline <= ?")
        values.extend(
            [
                now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(DATE_FORMAT),
                now.replace(hour=23, minute=59, second=0, microsecond=0).strftime(DATE_FORMAT),
            ]
        )
    elif deadline == "week":
        clauses.append("deadline >= ? AND deadline <= ?")
        values.extend([now.strftime(DATE_FORMAT), (now + timedelta(days=7)).strftime(DATE_FORMAT)])
    elif deadline == "overdue":
        clauses.append("deadline != '' AND deadline < ? AND status != ?")
        values.extend([now.strftime(DATE_FORMAT), "已完成"])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = get_db().execute(
        f"SELECT * FROM tasks {where_sql} ORDER BY deadline = '', deadline ASC, id DESC",
        values,
    ).fetchall()
    return [decorate_task(row) for row in rows]


def dashboard_data():
    tasks = list_tasks()
    now = app_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=0, microsecond=0)
    upcoming_end = now + timedelta(days=7)

    today_tasks = [
        task
        for task in tasks
        if task["deadline_dt"] and today_start <= task["deadline_dt"] <= today_end
    ]
    upcoming_tasks = [
        task
        for task in tasks
        if task["deadline_dt"] and now <= task["deadline_dt"] <= upcoming_end
    ][:5]
    completed_count = sum(1 for task in tasks if task["status"] == "已完成")
    overdue_count = sum(1 for task in tasks if task["is_overdue"])
    return {
        "today_tasks": today_tasks,
        "upcoming_tasks": upcoming_tasks,
        "completed_count": completed_count,
        "overdue_count": overdue_count,
        "total_count": len(tasks),
    }


def group_tasks_by_status(tasks):
    groups = {status: [] for status in ALLOWED_STATUSES}
    groups["已逾期"] = []
    for task in tasks:
        if task["is_overdue"]:
            groups["已逾期"].append(task)
        else:
            groups[task["status"]].append(task)
    return groups


def decorate_task(row):
    task = dict(row)
    deadline_dt = parse_deadline(task["deadline"])
    task["deadline_dt"] = deadline_dt
    task["deadline_label"] = format_deadline(deadline_dt)
    task["is_overdue"] = (
        deadline_dt is not None
        and deadline_dt < app_now()
        and task["status"] != "已完成"
    )
    return task


def parse_deadline(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        return None


def format_deadline(value):
    if value is None:
        return "未设置"
    return value.strftime("%Y-%m-%d %H:%M")


def _now():
    return app_now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_task_columns(db):
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(tasks)").fetchall()
    }
    columns = {
        "required_materials": "TEXT NOT NULL DEFAULT ''",
        "suggested_materials": "TEXT NOT NULL DEFAULT ''",
        "source_quote": "TEXT NOT NULL DEFAULT ''",
        "source_filename": "TEXT NOT NULL DEFAULT ''",
        "source_pages": "TEXT NOT NULL DEFAULT ''",
        "uncertain_fields": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing_columns:
            db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
