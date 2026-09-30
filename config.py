from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent


class Config:
    DEBUG = False
    SECRET_KEY = os.environ.get("DENGUE_SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DENGUE_DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'dengue.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    UPLOAD_DIRECTORY = os.environ.get("DENGUE_UPLOAD_DIRECTORY")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("DENGUE_SESSION_COOKIE_SECURE", "false").lower() == "true"


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = os.environ.get("DENGUE_SESSION_COOKIE_SECURE", "true").lower() == "true"
