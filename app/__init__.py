import os

from flask import Flask
from dotenv import load_dotenv


def create_app(test_config=None):
    load_dotenv()

    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
        DATABASE=os.getenv(
            "DATABASE_PATH",
            os.path.join(app.instance_path, "tasks.sqlite3"),
        ),
        AI_API_KEY=os.getenv("AI_API_KEY", ""),
        AI_API_BASE_URL=os.getenv("AI_API_BASE_URL", ""),
        AI_MODEL=os.getenv("AI_MODEL", ""),
        AI_TIMEOUT=int(os.getenv("AI_TIMEOUT", "20")),
        AI_DEMO_MODE=os.getenv("AI_DEMO_MODE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    from .planner import init_plan_db
    from .tasks import close_db, get_db, init_db

    app.teardown_appcontext(close_db)
    init_db(app)
    with app.app_context():
        init_plan_db(get_db())
        get_db().commit()

    from .routes import main_bp

    app.register_blueprint(main_bp)
    return app
