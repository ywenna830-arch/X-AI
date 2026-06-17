import os

from flask import Flask
from dotenv import load_dotenv


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
    )

    from .routes import main_bp

    app.register_blueprint(main_bp)
    return app
