import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from just_robots.utils.package_paths import get_config_folder

logger = logging.getLogger(__name__)


def _env_filepath():
    return get_config_folder() / ".just_robots.env"


class JustRobotsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_filepath(), env_prefix="JUST_ROBOTS_"
    )

    FIREBASE_CONFIG_PATH: Path
    RELAY_SERVER_URL: str
    # WEBRTC_STUN_SERVERS: str
    # WEBRTC_TURN_SERVERS: str
    RELAY_IDLE_TIMEOUT_SECONDS: float = 300.0
    RELAY_NO_PEERS_TIMEOUT_SECONDS: float = 120.0


def get_just_robots_settings():
    return JustRobotsSettings()  # pyright: ignore[reportCallIssue] # BaseSettings supports no args
