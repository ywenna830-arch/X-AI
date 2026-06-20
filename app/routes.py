import os

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from .ai_parser import AIParseError, parse_text_notice
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
from .tasks import (
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    create_task,
    dashboard_data,
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


@main_bp.get("/")
def index():
    return render_template(
        "index.html",
        active_page="home",
        dashboard=dashboard_data(),
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


@main_bp.get("/tasks/confirm")
def ai_confirm():
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
    return render_template(
        "task_detail.html",
        active_page="tasks",
        task=task,
        statuses=ALLOWED_STATUSES,
    )


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
