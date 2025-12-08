from pathlib import Path

import just_robots


def get_package_root() -> Path:
    return Path(just_robots.__file__).parent


def get_config_folder() -> Path:
    return get_package_root() / "config"
