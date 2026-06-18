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
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    from .tasks import close_db, init_db

    init_db(app)
    app.teardown_appcontext(close_db)

    from .routes import main_bp

    app.register_blueprint(main_bp)
    return app
