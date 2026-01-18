import enum
from http import HTTPStatus
from json.decoder import JSONDecodeError
from pprint import pformat

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from httpx import Response as HttpxResponse
from requests import Response


class NotFoundException(Exception):
    """Use this exception when the request resource or item is not in the database or
    cache"""

    def __init__(self, item_name: str, message: str = ""):
        super().__init__(message)
        self.item_name = item_name

    def __str__(self) -> str:
        return f"{self.item_name} was not found. {self.args[0]})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(item_name={self.item_name}, message='{self.args[0]}')"


class StateException(Exception):
    """Use this exception when a request is well formed, but incompatible with the
    current state of the server"""

    def __init__(self, message: str = ""):
        super().__init__(message)

    def __str__(self) -> str:
        return f"Got request while in invalid state. {self.args[0]})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message='{self.args[0]}')"


class PreemptedException(Exception):
    """Use this exception when a new request cancels a previous one"""

    def __init__(self, message: str = ""):
        super().__init__(message)

    def __str__(self) -> str:
        return f"Task got preempted by something else. {self.args[0]})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message='{self.args[0]}')"


class ExceptionTypes(str, enum.Enum):
    KEY_ERROR = "key_error"
    VALUE_ERROR = "value_error"
    INDEX_ERROR = "index_error"
    PERMISSION_ERROR = "permission_error"
    NOT_FOUND_EXCEPTION = "not_found_exception"
    PREEMPTED_EXCEPTION = "preempted_exception"
    STATE_EXCEPTION = "state_exception"
    TIMEOUT_ERROR = "timeout_error"
    RUNTIME_ERROR = "runtime_error"
    REQUEST_VALIDATION_ERROR = "request_validation_error"
    UNKNOWN_ERROR = "unknown_error"


def make_json_response(status_code: int, exception: Exception) -> JSONResponse:
    if isinstance(exception, KeyError):
        exception_type = ExceptionTypes.KEY_ERROR.value

    elif isinstance(exception, ValueError):
        exception_type = ExceptionTypes.VALUE_ERROR.value

    elif isinstance(exception, IndexError):
        exception_type = ExceptionTypes.INDEX_ERROR.value

    elif isinstance(exception, PermissionError):
        exception_type = ExceptionTypes.PERMISSION_ERROR.value

    elif isinstance(exception, NotFoundException):
        exception_type = ExceptionTypes.NOT_FOUND_EXCEPTION.value

    elif isinstance(exception, PreemptedException):
        exception_type = ExceptionTypes.PREEMPTED_EXCEPTION.value

    elif isinstance(exception, StateException):
        exception_type = ExceptionTypes.STATE_EXCEPTION.value

    elif isinstance(exception, TimeoutError):
        exception_type = ExceptionTypes.TIMEOUT_ERROR.value

    elif isinstance(exception, RuntimeError):
        exception_type = ExceptionTypes.RUNTIME_ERROR.value

    elif isinstance(exception, RequestValidationError):
        exception_type = ExceptionTypes.REQUEST_VALIDATION_ERROR.value

    else:
        exception_type = "unknown_exception"

    return JSONResponse(
        status_code=status_code,
        content={
            "detail": str(exception),
            "exception_type": exception_type,
        },
    )


def raise_if_error(resp: Response | HttpxResponse) -> None:
    """checks the response object for an error. If there is one, converts it to a
    proper python exception and raises it"""
    if resp.status_code == HTTPStatus.OK:
        return None

    exception_type = ExceptionTypes.UNKNOWN_ERROR
    exception_value: str = resp.text
    try:
        resp_json = resp.json()
        if isinstance(resp_json, dict):
            if "detail" in resp_json:
                if isinstance(resp_json["detail"], str):
                    exception_value = str(resp_json["detail"])
                else:
                    exception_value = pformat(exception_value, indent=2)
            if "exception_type" in resp_json:
                if isinstance(resp_json["exception_type"], str):
                    exception_type = ExceptionTypes(resp_json["exception_type"])
    except JSONDecodeError:
        pass

    match resp.status_code:
        case 403:
            raise PermissionError(exception_value)
        case 404:
            raise NotFoundException(exception_value)
        case 409:
            match exception_type:
                case ExceptionTypes.STATE_EXCEPTION:
                    raise StateException(exception_value)
                case ExceptionTypes.PREEMPTED_EXCEPTION:
                    raise PreemptedException(exception_value)
                case _:
                    raise StateException(exception_value)
        case 422:
            match exception_type:
                case ExceptionTypes.INDEX_ERROR:
                    raise IndexError(exception_value)
                case ExceptionTypes.VALUE_ERROR:
                    raise ValueError(exception_value)
                case ExceptionTypes.KEY_ERROR:
                    raise KeyError(exception_value)
                case ExceptionTypes.REQUEST_VALIDATION_ERROR:
                    raise ValueError(exception_value)
                case ExceptionTypes.UNKNOWN_ERROR:
                    raise ValueError(exception_value)
                case _:
                    raise ValueError(exception_value)
        case 500:
            raise RuntimeError(exception_value)
        case 504:
            raise TimeoutError(exception_value)
        case _:
            raise RuntimeError(exception_value)
