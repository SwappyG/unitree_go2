import typing as t

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel

from just_robots.fastapi_utils.fastapi_exceptions import (
    NotFoundException,
    PreemptedException,
    StateException,
    make_json_response,
)


def _add_exception_handlers(fastapi_app: FastAPI):
    @fastapi_app.exception_handler(ValueError)
    async def _value_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: ValueError
    ) -> JSONResponse:
        return make_json_response(422, exc)

    @fastapi_app.exception_handler(KeyError)
    async def _key_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: KeyError
    ) -> JSONResponse:
        return make_json_response(422, exc)

    @fastapi_app.exception_handler(IndexError)
    async def _index_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: IndexError
    ) -> JSONResponse:
        return make_json_response(422, exc)

    @fastapi_app.exception_handler(StateException)
    async def _state_exception_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: StateException
    ) -> JSONResponse:
        return make_json_response(409, exc)

    @fastapi_app.exception_handler(NotFoundException)
    async def _not_found_exception_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: NotFoundException
    ) -> JSONResponse:
        return make_json_response(404, exc)

    @fastapi_app.exception_handler(PermissionError)
    async def _permission_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: PermissionError
    ) -> JSONResponse:
        return make_json_response(403, exc)

    @fastapi_app.exception_handler(TimeoutError)
    async def _timeout_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: TimeoutError
    ) -> JSONResponse:
        return make_json_response(504, exc)

    @fastapi_app.exception_handler(RuntimeError)
    async def _runtime_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: RuntimeError
    ) -> JSONResponse:
        return make_json_response(500, exc)

    @fastapi_app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return make_json_response(422, exc)

    @fastapi_app.exception_handler(PreemptedException)
    async def _preempted_exception_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: PreemptedException
    ) -> JSONResponse:
        return make_json_response(409, exc)


HEARTBEAT_ROUTER_PREFIX = "/heartbeat"
HEARTBEAT_HEALTH_EP_LEAF = "/health"
HEARTBEAT_ECHO_EP_LEAF = "/echo"
HEARTBEAT_HEALTH_EP = HEARTBEAT_ROUTER_PREFIX + HEARTBEAT_HEALTH_EP_LEAF
HEARTBEAT_ECHO_EP = HEARTBEAT_ROUTER_PREFIX + HEARTBEAT_ECHO_EP_LEAF


class HealthReply(BaseModel):
    """Response for the Health endpoint"""


class EchoReply(BaseModel):
    """Response for the Echo endpoint"""

    echo: dict[str, t.Any]


def _make_heartbeat_router() -> APIRouter:
    # Add some common endpoints by default so client can ensure we're alive
    heartbeat_router = APIRouter()

    @heartbeat_router.get(HEARTBEAT_HEALTH_EP_LEAF, response_model=HealthReply)
    def _health_endpoint() -> HealthReply:  # pyright: ignore[reportUnusedFunction]
        return HealthReply()

    @heartbeat_router.get(HEARTBEAT_ECHO_EP_LEAF, response_model=EchoReply)
    def _echo_endpoint(  # pyright: ignore[reportUnusedFunction]
        args: dict[str, t.Any],
    ) -> EchoReply:
        return EchoReply(echo=args)

    return heartbeat_router


def make_app(
    lifespan: t.Callable[[FastAPI], t.AsyncContextManager[None]],
    routers: list[tuple[APIRouter, str]],
    title: str,
    allowed_origins: list[str],
    allowed_headers: list[str],
    allow_credentials: bool = False,
    **kwargs,
):
    """creates a fast api app, attaches routes, adds a default heartbeat route,"""
    # pylint: disable=too-many-positional-arguments
    app = FastAPI(title=title, lifespan=lifespan, **kwargs)
    heartbeat_router = _make_heartbeat_router()
    for router, prefix in routers:
        if prefix == "/heartbeat":
            heartbeat_router.include_router(router)
        else:
            app.include_router(router, prefix=prefix)
    app.include_router(heartbeat_router, prefix="/heartbeat")

    _add_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=allowed_headers,
    )

    return app
