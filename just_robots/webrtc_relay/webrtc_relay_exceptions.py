import typing as t

class StateException(Exception):
    """Used when a command comes in while the server is in a state where it cannot be processed"""


def recreate_and_raise_exception(err_json: dict[str, t.Any]) -> t.NoReturn:
    if 'detail' not in err_json:
        raise Exception(f"detail and type unknown")
    
    detail = err_json['detail']
    if 'exception_type' not in err_json:
        raise Exception(f"type unknown. {detail=}")
    
    exception_type = err_json['exception_type']
    match exception_type:
        case 'runtime_error':
            raise RuntimeError(detail)
        case 'value_error':
            raise ValueError(detail)
        case 'index_error':
            raise IndexError(detail)
        case 'key_error':
            raise KeyError(detail)
        case 'state_exception':
            raise StateException(detail)
        case 'timeout_error':
            raise TimeoutError(detail)
        case 'asyncio_timeout_error':
            raise TimeoutError(f"asyncio.TimeoutError, {detail=}")
        case _:
            raise Exception(f"{exception_type=} : {detail=}")