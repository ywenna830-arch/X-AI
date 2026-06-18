from flask import Blueprint, jsonify, render_template


main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    return render_template("index.html", active_page="home")


@main_bp.get("/tasks/new")
def add_task():
    return render_template("add_task.html", active_page="add_task")


@main_bp.get("/tasks/confirm")
def ai_confirm():
    return render_template("ai_confirm.html", active_page="ai_confirm")


@main_bp.get("/tasks")
def task_center():
    return render_template("task_center.html", active_page="tasks")


@main_bp.get("/plan")
def plan():
    return render_template("plan.html", active_page="plan")


@main_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "课迹"})
