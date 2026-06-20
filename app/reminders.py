from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from .planner import generate_plan, get_availability, get_plan_settings
from .tasks import DATE_FORMAT, decorate_task, format_deadline, list_tasks, parse_deadline


PRESET_REMINDER_DAYS = (7, 3, 1, 0)
DEFAULT_UPCOMING_DAYS = 7
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_PLAN_START = time(9, 0)
DEFAULT_DEADLINE_TIME = time(23, 59)
DEFAULT_EVENT_MINUTES = 30
MAX_CUSTOM_REMINDER_DAYS = 365


def init_reminder_db(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS task_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            days_before INTEGER NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (days_before >= 0),
            CHECK (days_before <= 365),
            CHECK (is_read IN (0, 1)),
            UNIQUE (task_id, days_before),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
        """
    )


def validate_reminder_form(form):
    errors = []
    days = []
    for raw in form.getlist("reminder_days"):
        _append_days(raw, days, errors)

    custom_value = form.get("custom_days", "").strip()
    if custom_value:
        _append_days(custom_value, days, errors)

    if errors:
        return errors, []
    return [], sorted(set(days), reverse=True)


def save_task_reminders(db, task_id, days_before):
    now = _now()
    db.execute("DELETE FROM task_reminders WHERE task_id = ?", (task_id,))
    for days in sorted(set(days_before), reverse=True):
        db.execute(
            """
            INSERT OR IGNORE INTO task_reminders (
                task_id, days_before, is_read, created_at, updated_at
            )
            VALUES (?, ?, 0, ?, ?)
            """,
            (task_id, days, now, now),
        )
    db.commit()


def get_task_reminders(db, task_id):
    rows = db.execute(
        "SELECT * FROM task_reminders WHERE task_id = ? ORDER BY days_before DESC",
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_reminder_read(db, reminder_id):
    db.execute(
        "UPDATE task_reminders SET is_read = 1, updated_at = ? WHERE id = ?",
        (_now(), reminder_id),
    )
    db.commit()


def dashboard_data(db, now=None):
    now = now or datetime.now()
    tasks = list_tasks()
    active_tasks = [task for task in tasks if task["status"] != "已完成"]
    today = now.date()
    tomorrow = today + timedelta(days=1)

    today_tasks = _tasks_due_on(active_tasks, today, now)
    tomorrow_tasks = _tasks_due_on(active_tasks, tomorrow, now)
    today_plan_items = _plan_items_for_day(db, today)
    tomorrow_plan_items = _plan_items_for_day(db, tomorrow)
    upcoming_tasks = _upcoming_tasks(db, active_tasks, now)
    overdue_tasks = [task for task in active_tasks if task["is_overdue"]]
    reminders = _active_reminders(db, active_tasks, now)
    capacity_risks = _capacity_risks(db, tasks)

    completed_count = sum(1 for task in tasks if task["status"] == "已完成")
    return {
        "today_tasks": today_tasks,
        "today_plan_items": today_plan_items,
        "tomorrow_tasks": tomorrow_tasks,
        "tomorrow_plan_items": tomorrow_plan_items,
        "upcoming_tasks": upcoming_tasks,
        "overdue_tasks": overdue_tasks,
        "reminders": reminders,
        "capacity_risks": capacity_risks,
        "completed_count": completed_count,
        "overdue_count": len(overdue_tasks),
        "total_count": len(tasks),
    }


def countdown_label(deadline_dt, now=None):
    if deadline_dt is None:
        return "未设置截止时间"
    now = now or datetime.now()
    if deadline_dt.date() == now.date() and deadline_dt >= now:
        return f"今天{deadline_dt.strftime('%H:%M')}截止"
    if deadline_dt < now:
        delta = now - deadline_dt
        if delta.days >= 1:
            return f"已逾期{delta.days}天"
        hours = max(1, int(delta.total_seconds() // 3600))
        return f"已逾期{hours}小时"
    delta = deadline_dt - now
    days = delta.days
    hours = delta.seconds // 3600
    if days == 0:
        return f"剩余{max(1, hours)}小时"
    return f"剩余{days}天{hours}小时"


def build_task_ics(db, tasks, now=None):
    now = now or datetime.now(timezone.utc)
    tz_name = current_app.config.get("APP_TIMEZONE", DEFAULT_TIMEZONE)
    tz = _load_timezone(tz_name)
    events = []
    for task in tasks:
        if task["status"] == "已完成":
            continue
        plan_items = _plan_items_for_task(db, task["id"])
        reminders = get_task_reminders(db, task["id"])
        if plan_items:
            for item in plan_items:
                events.append(_event_for_plan_item(task, item, reminders, now, tz_name, tz))
        else:
            events.append(_event_for_task_deadline(task, reminders, now, tz_name, tz))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Keji//Task Calendar//CN",
        "CALSCALE:GREGORIAN",
        f"X-WR-TIMEZONE:{tz_name}",
    ]
    for event in events:
        lines.extend(event)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"


def decorated_task_rows(rows):
    return [decorate_task(row) for row in rows]


def _append_days(raw, days, errors):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        errors.append("提醒提前天数必须是0到365之间的整数。")
        return
    if value < 0 or value > MAX_CUSTOM_REMINDER_DAYS:
        errors.append("提醒提前天数必须是0到365之间的整数。")
        return
    days.append(value)


def _tasks_due_on(tasks, target_day, now):
    result = []
    for task in tasks:
        if task["deadline_dt"] and task["deadline_dt"].date() == target_day:
            result.append(_with_countdown(task, now))
    return sorted(result, key=lambda task: (task["deadline_dt"], task["id"]))


def _upcoming_tasks(db, tasks, now):
    max_days = _max_reminder_days(db) or DEFAULT_UPCOMING_DAYS
    end_dt = now + timedelta(days=max(DEFAULT_UPCOMING_DAYS, max_days))
    result = [
        _with_countdown(task, now)
        for task in tasks
        if task["deadline_dt"] and now <= task["deadline_dt"] <= end_dt
    ]
    return sorted(result, key=lambda task: (task["deadline_dt"], task["id"]))


def _active_reminders(db, tasks, now):
    task_map = {task["id"]: task for task in tasks}
    rows = db.execute(
        """
        SELECT task_reminders.*, tasks.title, tasks.deadline, tasks.course_name, tasks.status
        FROM task_reminders
        JOIN tasks ON tasks.id = task_reminders.task_id
        WHERE task_reminders.is_read = 0
          AND tasks.status != '已完成'
        ORDER BY tasks.deadline ASC, task_reminders.days_before DESC
        """
    ).fetchall()
    reminders = []
    seen = set()
    for row in rows:
        task = task_map.get(row["task_id"])
        deadline_dt = parse_deadline(row["deadline"])
        if deadline_dt is None:
            continue
        reminder_at = deadline_dt - timedelta(days=row["days_before"])
        key = (row["task_id"], row["days_before"])
        if now >= reminder_at and key not in seen:
            seen.add(key)
            reminders.append(
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "title": row["title"],
                    "course_name": row["course_name"],
                    "days_before": row["days_before"],
                    "deadline_label": format_deadline(deadline_dt),
                    "countdown": countdown_label(deadline_dt, now),
                    "task": task,
                }
            )
    return reminders


def _capacity_risks(db, tasks):
    settings = get_plan_settings(db)
    availability = get_availability(db, settings["horizon_days"])
    preview = generate_plan(tasks, availability, settings)
    return preview["warnings"]


def _plan_items_for_day(db, target_day):
    rows = db.execute(
        """
        SELECT plan_items.*, tasks.title AS task_title, tasks.course_name, tasks.deadline
        FROM plan_items
        JOIN tasks ON tasks.id = plan_items.task_id
        WHERE plan_items.scheduled_date = ?
          AND tasks.status != '已完成'
        ORDER BY plan_items.id ASC
        """,
        (target_day.isoformat(),),
    ).fetchall()
    return [dict(row) for row in rows]


def _plan_items_for_task(db, task_id):
    rows = db.execute(
        """
        SELECT * FROM plan_items
        WHERE task_id = ?
        ORDER BY scheduled_date ASC, id ASC
        """,
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _max_reminder_days(db):
    row = db.execute("SELECT MAX(days_before) AS max_days FROM task_reminders").fetchone()
    return row["max_days"] if row and row["max_days"] is not None else 0


def _with_countdown(task, now):
    return {**task, "countdown": countdown_label(task["deadline_dt"], now)}


def _event_for_plan_item(task, item, reminders, now, tz_name, tz):
    start = datetime.combine(date.fromisoformat(item["scheduled_date"]), DEFAULT_PLAN_START, tz)
    minutes = item["minutes"] or DEFAULT_EVENT_MINUTES
    end = start + timedelta(minutes=minutes)
    description = _task_description(task, f"计划项：{item['title']}，预计{minutes}分钟")
    return _event_lines(
        uid=f"task-{task['id']}-plan-{item['id']}@keji",
        summary=task["title"],
        description=description,
        start=start,
        end=end,
        reminders=reminders,
        now=now,
        tz_name=tz_name,
    )


def _event_for_task_deadline(task, reminders, now, tz_name, tz):
    deadline_dt = _deadline_for_ics(task["deadline"], tz)
    end = deadline_dt + timedelta(minutes=DEFAULT_EVENT_MINUTES)
    description = _task_description(task, "未生成计划项，事件时间使用任务截止时间。")
    return _event_lines(
        uid=f"task-{task['id']}@keji",
        summary=task["title"],
        description=description,
        start=deadline_dt,
        end=end,
        reminders=reminders,
        now=now,
        tz_name=tz_name,
    )


def _event_lines(uid, summary, description, start, end, reminders, now, tz_name):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_format_utc(now)}",
        f"SUMMARY:{_escape_ics(summary)}",
        f"DESCRIPTION:{_escape_ics(description)}",
        f"DTSTART;TZID={tz_name}:{_format_local(start)}",
        f"DTEND;TZID={tz_name}:{_format_local(end)}",
    ]
    seen = set()
    for reminder in reminders:
        days = reminder["days_before"]
        if days in seen:
            continue
        seen.add(days)
        trigger = "-PT0M" if days == 0 else f"-P{days}D"
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape_ics('任务提醒：' + summary)}",
                f"TRIGGER:{trigger}",
                "END:VALARM",
            ]
        )
    lines.append("END:VEVENT")
    return lines


def _task_description(task, note):
    parts = [
        f"课程：{task['course_name'] or '未填写'}",
        f"任务说明：{task['description'] or '暂无说明'}",
        f"提交要求：{task['submission_requirements'] or '未填写'}",
        f"截止时间：{task['deadline_label']}",
        note,
    ]
    return "\n".join(parts)


def _deadline_for_ics(value, tz):
    parsed = parse_deadline(value)
    if parsed is not None:
        return parsed.replace(tzinfo=tz)
    try:
        deadline_date = date.fromisoformat(value)
    except ValueError:
        deadline_date = date.today()
    return datetime.combine(deadline_date, DEFAULT_DEADLINE_TIME, tz)


def _load_timezone(tz_name):
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        if tz_name == DEFAULT_TIMEZONE:
            return timezone(timedelta(hours=8), tz_name)
        return timezone.utc


def _format_local(value):
    return value.strftime("%Y%m%dT%H%M%S")


def _format_utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape_ics(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold_line(line):
    output = []
    remaining = line
    first_limit = 75
    while len(remaining.encode("utf-8")) > first_limit:
        split_at = _byte_split_index(remaining, first_limit)
        output.append(remaining[:split_at])
        remaining = " " + remaining[split_at:]
        first_limit = 75
    output.append(remaining)
    return "\r\n".join(output)


def _byte_split_index(text, limit):
    total = 0
    for index, char in enumerate(text):
        total += len(char.encode("utf-8"))
        if total > limit:
            return max(1, index)
    return len(text)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
