import dataclasses
import logging
import os
from pathlib import Path

import dotenv
from pydantic import BaseModel

from just_robots.utils.package_paths import get_config_folder

logger = logging.getLogger(__name__)


class JustRobotsConfig(BaseModel):
    JUST_ROBOTS_FIREBASE_CONFIG_PATH: Path | None = None
    JUST_ROBOTS_FIREBASE_AUTH_ENABLED: bool
    JUST_ROBOTS_FIREBASE_API_KEY: str
    JUST_ROBOTS_RELAY_SERVER_URL: str


def _env_filepath():
    return get_config_folder() / ".env"


def load_just_robots_env_vars():
    env_filepath = _env_filepath()
    if not dotenv.load_dotenv(env_filepath):
        logger.error(f"failed to load .env file from {env_filepath=}")


def get_config_from_env() -> JustRobotsConfig:
    env_filepath = _env_filepath()
    env_vars = {
        **dotenv.dotenv_values(env_filepath),
        **os.environ,
    }
    return JustRobotsConfig.model_validate(env_vars, extra="ignore")
