import os

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from .ai_parser import AIParseError, parse_chat_message, parse_text_notice
from .file_importer import FileImportError, extract_uploaded_file
from .planner import (
    INCOMPLETE_REASONS,
    PLAN_STATUSES,
    delay_plan_item,
    generate_plan,
    get_availability,
    get_plan_settings,
    load_saved_plan,
    record_plan_feedback,
    replan_remaining_items,
    save_plan_items,
    save_plan_settings,
    update_plan_item_minutes,
)
from .reminders import (
    PRESET_REMINDER_DAYS,
    build_task_ics,
    dashboard_data,
    decorated_task_rows,
    get_task_reminders,
    mark_reminder_read,
    save_task_reminders,
    validate_reminder_form,
)
from .tasks import (
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    create_task,
    delete_task,
    get_task,
    get_db,
    group_tasks_by_status,
    list_tasks,
    update_task,
    update_task_status,
    validate_task_form,
)


main_bp = Blueprint("main", __name__)
MAX_CHAT_MESSAGE_LENGTH = 10000


@main_bp.get("/")
def index():
    return render_template(
        "index.html",
        active_page="home",
        dashboard=dashboard_data(get_db()),
    )


@main_bp.get("/tasks/new")
def add_task():
    return render_template(
        "add_task.html",
        active_page="add_task",
        errors=[],
        parse_errors=[],
        form_data={},
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@main_bp.post("/tasks/import")
def import_task_file_route():
    upload_dir = current_app.config["IMPORT_UPLOAD_DIR"]
    os.makedirs(upload_dir, exist_ok=True)
    try:
        imported = extract_uploaded_file(
            request.files.get("attachment"),
            upload_dir,
            current_app.config["MAX_IMPORT_FILE_BYTES"],
        )
    except FileImportError as exc:
        return (
            render_template(
                "add_task.html",
                active_page="add_task",
                errors=[],
                parse_errors=[exc.message],
                form_data={},
                statuses=ALLOWED_STATUSES,
                priorities=ALLOWED_PRIORITIES,
            ),
            400,
        )

    return render_template(
        "add_task.html",
        active_page="add_task",
        errors=[],
        parse_errors=[],
        form_data={},
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
        notice_text=imported.text,
        imported_file=imported,
    )


@main_bp.post("/tasks")
def create_task_route():
    errors, data = validate_task_form(request.form)
    if errors:
        return (
            render_template(
                "add_task.html",
                active_page="add_task",
                errors=errors,
                parse_errors=[],
                form_data=data,
                statuses=ALLOWED_STATUSES,
                priorities=ALLOWED_PRIORITIES,
            ),
            400,
        )

    task_id = create_task(data)
    flash("任务已新增。")
    return redirect(url_for("main.task_detail", task_id=task_id))


@main_bp.post("/tasks/ai/parse")
def parse_text_notice_route():
    notice_text = request.form.get("notice_text", "")
    notice_date = request.form.get("notice_date", "")
    source_type = request.form.get("source_type", "").strip()
    source_filename = request.form.get("source_filename", "").strip()
    source_pages = request.form.get("source_pages", "").strip()
    try:
        result = parse_text_notice(notice_text, notice_date)
    except AIParseError as exc:
        return (
            render_template(
                "add_task.html",
                active_page="add_task",
                errors=[],
                parse_errors=[exc.message],
                notice_text=notice_text,
                notice_date=notice_date,
                imported_file={
                    "source_type": source_type,
                    "source_filename": source_filename,
                    "source_pages": source_pages,
                } if source_type else None,
                form_data={},
                statuses=ALLOWED_STATUSES,
                priorities=ALLOWED_PRIORITIES,
            ),
            400,
        )

    form_data = result["data"]
    form_data["source_text"] = notice_text.strip()
    if source_type:
        form_data["source_type"] = source_type
        form_data["source_filename"] = source_filename
        form_data["source_pages"] = source_pages
    return render_template(
        "ai_confirm.html",
        active_page="ai_confirm",
        result=result,
        errors=[],
        form_data=form_data,
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@main_bp.post("/api/chat")
def chat_api():
    if not request.is_json:
        return jsonify({"ok": False, "error": "请使用JSON请求。"}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容不是合法JSON。"}), 400

    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"ok": False, "error": "消息不能为空。"}), 400
    if len(message) > MAX_CHAT_MESSAGE_LENGTH:
        return jsonify({"ok": False, "error": "单条消息不能超过10000个字符。"}), 400

    notice_date = str(payload.get("notice_date", "")).strip()
    source_type = str(payload.get("source_type", "")).strip()
    source_filename = str(payload.get("source_filename", "")).strip()
    source_pages = str(payload.get("source_pages", "")).strip()

    try:
        chat_result = parse_chat_message(message, notice_date)
    except AIParseError as exc:
        return jsonify({"ok": False, "error": exc.message}), 502

    if chat_result["type"] == "chat":
        return jsonify({"ok": True, "type": "chat", "reply": chat_result["reply"]})

    result = chat_result["result"]
    form_data = dict(result["data"])
    form_data["source_text"] = message
    if source_type:
        form_data["source_type"] = source_type
        form_data["source_filename"] = source_filename
        form_data["source_pages"] = source_pages

    session["pending_ai_task"] = {
        "result": result,
        "form_data": form_data,
    }

    return jsonify(
        {
            "ok": True,
            "type": "task",
            "reply": chat_result["reply"],
            "task_preview": {
                "course_name": form_data.get("course_name", ""),
                "title": form_data.get("title", ""),
                "deadline": form_data.get("deadline", ""),
                "confidence": form_data.get("confidence", ""),
            },
            "confirm_url": url_for("main.ai_confirm"),
        }
    )


@main_bp.get("/tasks/confirm")
def ai_confirm():
    pending_task = session.get("pending_ai_task")
    if pending_task:
        return render_template(
            "ai_confirm.html",
            active_page="ai_confirm",
            result=pending_task.get("result"),
            errors=[],
            form_data=pending_task.get("form_data", {}),
            statuses=ALLOWED_STATUSES,
            priorities=ALLOWED_PRIORITIES,
        )
    return render_template(
        "ai_confirm.html",
        active_page="ai_confirm",
        result=None,
        errors=[],
        form_data={},
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@main_bp.get("/tasks")
def task_center():
    filters = {
        "course": request.args.get("course", ""),
        "status": request.args.get("status", ""),
        "priority": request.args.get("priority", ""),
        "deadline": request.args.get("deadline", ""),
    }
    tasks = list_tasks(filters)
    return render_template(
        "task_center.html",
        active_page="tasks",
        filters=filters,
        groups=group_tasks_by_status(tasks),
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@main_bp.get("/tasks/<int:task_id>")
def task_detail(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)
    db = get_db()
    return render_template(
        "task_detail.html",
        active_page="tasks",
        task=task,
        statuses=ALLOWED_STATUSES,
        preset_reminder_days=PRESET_REMINDER_DAYS,
        reminders=get_task_reminders(db, task_id),
    )


@main_bp.post("/tasks/<int:task_id>/reminders")
def save_task_reminders_route(task_id):
    if get_task(task_id) is None:
        abort(404)
    errors, reminder_days = validate_reminder_form(request.form)
    if errors:
        for error in errors:
            flash(error)
    else:
        save_task_reminders(get_db(), task_id, reminder_days)
        flash("提醒设置已保存。")
    return redirect(url_for("main.task_detail", task_id=task_id))


@main_bp.post("/reminders/<int:reminder_id>/read")
def mark_reminder_read_route(reminder_id):
    mark_reminder_read(get_db(), reminder_id)
    flash("提醒已标记为已处理。")
    return redirect(request.referrer or url_for("main.index"))


@main_bp.get("/tasks/<int:task_id>/calendar.ics")
def export_task_calendar(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)
    ics = build_task_ics(get_db(), [task])
    if ics is None:
        flash("此任务没有可导出的日历事件：没有未完成计划项，也没有有效截止时间。")
        return redirect(url_for("main.task_detail", task_id=task_id))
    return _ics_response(ics, f"task-{task_id}.ics")


@main_bp.get("/calendar/tasks.ics")
def export_all_tasks_calendar():
    rows = get_db().execute(
        "SELECT * FROM tasks WHERE status != '已完成' ORDER BY deadline = '', deadline ASC, id ASC"
    ).fetchall()
    tasks = decorated_task_rows(rows)
    ics = build_task_ics(get_db(), tasks)
    if ics is None:
        flash("当前没有可导出的日历事件：无有效截止时间且无未完成计划项的任务已跳过。")
        return redirect(url_for("main.index"))
    return _ics_response(ics, "unfinished-tasks.ics")


@main_bp.get("/tasks/<int:task_id>/edit")
def edit_task(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)
    return render_template(
        "edit_task.html",
        active_page="tasks",
        task=task,
        errors=[],
        form_data=task,
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@main_bp.post("/tasks/<int:task_id>/edit")
def update_task_route(task_id):
    task = get_task(task_id)
    if task is None:
        abort(404)

    errors, data = validate_task_form(request.form)
    if errors:
        data["id"] = task_id
        return (
            render_template(
                "edit_task.html",
                active_page="tasks",
                task=task,
                errors=errors,
                form_data=data,
                statuses=ALLOWED_STATUSES,
                priorities=ALLOWED_PRIORITIES,
            ),
            400,
        )

    update_task(task_id, data)
    flash("任务已更新。")
    return redirect(url_for("main.task_detail", task_id=task_id))


@main_bp.post("/tasks/<int:task_id>/delete")
def delete_task_route(task_id):
    if get_task(task_id) is None:
        abort(404)
    delete_task(task_id)
    flash("任务已删除。")
    return redirect(url_for("main.task_center"))


@main_bp.post("/tasks/<int:task_id>/status")
def update_status_route(task_id):
    if get_task(task_id) is None:
        abort(404)
    status = request.form.get("status", "")
    try:
        update_task_status(task_id, status)
    except ValueError:
        abort(400)
    flash("任务状态已更新。")
    return redirect(request.referrer or url_for("main.task_center"))


@main_bp.get("/plan")
def plan():
    db = get_db()
    settings = get_plan_settings(db)
    availability = get_availability(db, settings["horizon_days"])
    saved_plan = load_saved_plan(db, settings["horizon_days"])
    return render_template(
        "plan.html",
        active_page="plan",
        settings=settings,
        availability=availability,
        saved_plan=saved_plan,
        preview=None,
        errors=[],
        replan_warnings=[],
        plan_statuses=PLAN_STATUSES,
        incomplete_reasons=INCOMPLETE_REASONS,
    )


@main_bp.post("/plan/settings")
def save_plan_settings_route():
    errors = save_plan_settings(get_db(), request.form)
    if errors:
        db = get_db()
        settings = get_plan_settings(db)
        availability = get_availability(db, settings["horizon_days"])
        saved_plan = load_saved_plan(db, settings["horizon_days"])
        return (
            render_template(
                "plan.html",
                active_page="plan",
                settings=settings,
                availability=availability,
                saved_plan=saved_plan,
                preview=None,
                errors=errors,
                replan_warnings=[],
                plan_statuses=PLAN_STATUSES,
                incomplete_reasons=INCOMPLETE_REASONS,
            ),
            400,
        )
    flash("可用时间和规划偏好已保存。")
    return redirect(url_for("main.plan"))


@main_bp.post("/plan/generate")
def generate_plan_route():
    db = get_db()
    settings = get_plan_settings(db)
    availability = get_availability(db, settings["horizon_days"])
    tasks = list_tasks()
    preview = generate_plan(tasks, availability, settings)
    saved_plan = load_saved_plan(db, settings["horizon_days"])
    return render_template(
        "plan.html",
        active_page="plan",
        settings=settings,
        availability=availability,
        saved_plan=saved_plan,
        preview=preview,
        errors=[],
        replan_warnings=[],
        plan_statuses=PLAN_STATUSES,
        incomplete_reasons=INCOMPLETE_REASONS,
    )


@main_bp.post("/plan/confirm")
def confirm_plan_route():
    db = get_db()
    settings = get_plan_settings(db)
    availability = get_availability(db, settings["horizon_days"])
    preview = generate_plan(list_tasks(), availability, settings)
    items = preview["items"]
    if not items:
        flash("没有可保存的计划项，请先生成待确认计划。")
        return redirect(url_for("main.plan"))
    save_plan_items(db, items)
    flash("计划已确认并保存。")
    return redirect(url_for("main.plan"))


@main_bp.post("/plan/items/<int:item_id>/feedback")
def record_plan_feedback_route(item_id):
    errors = record_plan_feedback(get_db(), item_id, request.form)
    if errors:
        for error in errors:
            flash(error)
    else:
        flash("执行反馈已记录。")
    return redirect(url_for("main.plan"))


@main_bp.post("/plan/items/<int:item_id>/delay")
def delay_plan_item_route(item_id):
    db = get_db()
    settings = get_plan_settings(db)
    errors = delay_plan_item(db, item_id, settings)
    if errors:
        for error in errors:
            flash(error)
    else:
        flash("计划项已延后一天。")
    return redirect(url_for("main.plan"))


@main_bp.post("/plan/items/<int:item_id>/minutes")
def update_plan_item_minutes_route(item_id):
    errors = update_plan_item_minutes(get_db(), item_id, request.form.get("minutes", ""))
    if errors:
        for error in errors:
            flash(error)
    else:
        flash("预计时长已更新。")
    return redirect(url_for("main.plan"))


@main_bp.post("/plan/replan")
def replan_remaining_route():
    db = get_db()
    settings = get_plan_settings(db)
    availability = get_availability(db, settings["horizon_days"])
    result = replan_remaining_items(db, settings, availability)
    for warning in result["warnings"]:
        flash(warning)
    flash("已根据执行反馈重新安排剩余计划。")
    return redirect(url_for("main.plan"))


@main_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "课迹"})


def _ics_response(content, filename):
    return Response(
        content,
        content_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
