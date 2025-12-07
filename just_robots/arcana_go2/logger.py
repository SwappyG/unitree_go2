import logging
import typing as t
import os
import enum

T = t.TypeVar("T", bound=Exception)


# LogLevels = t.Literal["DEBUG", "INFO", "WARNING", "ERROR"]
class LogLevel(enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

DEFAULT_LOG_LEVEL = LogLevel.INFO


class Logger:
    def __init__(self, module_name: str, log_level: LogLevel):
        self._logger = logging.getLogger(module_name)
        self._logger.setLevel(log_level.value)

    def debug(self, message, *args, **kwargs):
        self._logger.debug(message)


    def info(self, message, *args, **kwargs):
        self._logger.info(message, *args, **kwargs)


    def warning(self, message, *args, **kwargs):
        self._logger.warning(message, *args, **kwargs)


    def error(self, message, *args, **kwargs):
        self._logger.error(message, *args, **kwargs)


    def info_raise(self, exception_type: t.Type[T], message, *args, **kwargs):
        self._logger.info(message, *args, *kwargs)
        raise exception_type(message, args, kwargs)


    def warn_raise(self, exception_type: t.Type[T], message, *args, **kwargs):
        self._logger.warning(message, *args, *kwargs)
        raise exception_type(message, args, kwargs)


    def error_raise(self, exception_type: t.Type[T], message, *args, **kwargs):
        self._logger.error(message, *args, *kwargs)
        raise exception_type(message, args, kwargs)
    

def make_logger(module_name: str, log_level: LogLevel | None = None) -> Logger:
    tokens = module_name.upper().split(".")
    log_level = log_level if log_level is not None else DEFAULT_LOG_LEVEL
    for ii, _token in enumerate(tokens):
        env_var_name = '_'.join(tokens[:ii+1]) + "_LOG_LEVEL"
        env_value = os.environ.get(env_var_name)
        if env_value is not None:
            try:
                log_level = LogLevel(env_value)
            except ValueError:
                logging.warning(f"got invalid logging level {env_value=} from env var while trying to create logger for {module_name=}. Using default level")
    
    return Logger(module_name, log_level=log_level)


