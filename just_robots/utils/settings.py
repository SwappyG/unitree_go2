import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from just_robots.utils.package_paths import get_config_folder

logger = logging.getLogger(__name__)


def _env_filepath():
    return get_config_folder() / ".just_robots.env"


class JustRobotsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_filepath(), env_prefix="JUST_ROBOTS_")

    FIREBASE_CONFIG_PATH: Path | None = None
    FIREBASE_AUTH_ENABLED: bool
    FIREBASE_API_KEY: str
    RELAY_SERVER_URL: str
    FIREBASE_AUTHORIZED_USERS: list[str] | None = None


def get_just_robots_settings():
    return JustRobotsSettings()  # pyright: ignore[reportCallIssue] # BaseSettings supports no args
