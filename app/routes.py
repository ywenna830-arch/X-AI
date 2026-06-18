from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from .ai_parser import AIParseError, parse_text_notice
from .tasks import (
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    create_task,
    dashboard_data,
    delete_task,
    get_task,
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
                form_data={},
                statuses=ALLOWED_STATUSES,
                priorities=ALLOWED_PRIORITIES,
            ),
            400,
        )

    form_data = result["data"]
    form_data["source_text"] = notice_text.strip()
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
    return render_template("plan.html", active_page="plan")


@main_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "课迹"})
