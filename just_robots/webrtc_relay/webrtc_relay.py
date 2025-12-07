import asyncio
import logging
import os
from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import typing as t
from dotenv import load_dotenv
from go2_robot_sdk.webrtc_relay.webrtc_relay_app_state import WebRTCRelayAppState, get_app_state
from go2_robot_sdk.webrtc_relay.webrtc_relay_endpoint_go2 import router as go2_router
from go2_robot_sdk.webrtc_relay.webrtc_relay_endpoint_webrtc import router as webrtc_router
from go2_robot_sdk.webrtc_relay.webrtc_relay_exceptions import StateException
from go2_robot_sdk.webrtc_relay.firebase_auth_server import initialize_firebase_auth


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    load_dotenv()
    logger.info("starting fastapi")
    
    # Initialize Firebase authentication
    firebase_config_path = os.getenv("FIREBASE_CONFIG_PATH")
    authorized_users_env = os.getenv("FIREBASE_AUTHORIZED_USERS")
    authorized_users = None
    if authorized_users_env:
        authorized_users = [uid.strip() for uid in authorized_users_env.split(",")]
    
    logger.info(f"Firebase config path: {firebase_config_path}")
    logger.info(f"Authorized users: {authorized_users}")
    
    firebase_auth_config = initialize_firebase_auth(
        firebase_config_path=firebase_config_path,
        authorized_users=authorized_users,
        enabled=True,
    )
    
    if firebase_auth_config.enabled:
        logger.info(f"Firebase authentication enabled. Authorized users: {len(firebase_auth_config.authorized_users)}")
        if firebase_auth_config._firebase_app is None:
            logger.error("Firebase Admin SDK was not initialized! Authentication will fail.")
    else:
        logger.warning("Firebase authentication is disabled. All requests will be allowed.")
    
    fastapi_app.state.state = WebRTCRelayAppState()
    # clean shutdown
    try:
        logger.info("yielding fastapi app")
        yield
    finally:
        logger.info("cleaning up fastapi")
        # if fastapi_app.state.state.display_task:
        #     if fastapi_app.state.state.display_task.cancel():
        #         await fastapi_app.state.state.display_task
        #         fastapi_app.state.state.display_task = None

        if fastapi_app.state.state.go2:
            await fastapi_app.state.state.go2.disconnect()

        # Close PC connection if present
        if fastapi_app.state.state.relay_rtc_peer_connection:
            await fastapi_app.state.state.relay_rtc_peer_connection.close()
            fastapi_app.state.state.relay_rtc_peer_connection = None
            fastapi_app.state.state.relay_rtc_data_channel = None
        # Close Go2 connection if present
        if fastapi_app.state.state.go2:
            await fastapi_app.state.state.go2.disconnect()
            fastapi_app.state.state.go2 = None
            fastapi_app.state.state.go2_video_track = None


app = FastAPI(lifespan=lifespan)
app.include_router(go2_router, prefix="/go2")
app.include_router(webrtc_router, prefix="/webrtc")


@app.get("/stats/webrtc")
async def get_webrtc_stats(state: t.Annotated[WebRTCRelayAppState, Depends(get_app_state)]):
    """Get current WebRTC statistics for all connections"""
    stats = {}
    
    if state.relay_stats_monitor:
        stats["relay_to_client"] = await state.relay_stats_monitor.get_current_stats()
    
    if state.client_to_relay_stats_monitor:
        stats["client_to_relay"] = await state.client_to_relay_stats_monitor.get_current_stats()
    
    if state.go2_stats_monitor:
        stats["go2_to_relay"] = await state.go2_stats_monitor.get_current_stats()
    
    return stats


@app.exception_handler(StateException)
def _app_state_exception_handler(_request: Request, exc: StateException):  # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=409, content={"detail": str(exc), "exception_type": "state_exception"})

@app.exception_handler(ValueError)
def _app_value_error_handler(_request: Request, exc: ValueError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=422, content={"detail": str(exc), "exception_type": "value_error"})

@app.exception_handler(KeyError)
def _app_key_error_handler(_request: Request, exc: KeyError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=422, content={"detail": str(exc), "exception_type": "key_error"})

@app.exception_handler(IndexError)
def _app_index_error_handler(_request: Request, exc: IndexError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=422, content={"detail": str(exc), "exception_type": "index_error"})

@app.exception_handler(RuntimeError)
def _app_runtime_error_handler(_request: Request, exc: RuntimeError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=500, content={"detail": str(exc), "exception_type": "runtime_error"})

@app.exception_handler(TimeoutError)
def _app_timeout_error_handler(_request: Request, exc: TimeoutError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=504, content={"detail": str(exc), "exception_type": "timeout_error"})

@app.exception_handler(asyncio.TimeoutError)
def _app_asyncio_timeout_error_handler(_request: Request, exc: asyncio.TimeoutError): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=504, content={"detail": str(exc), "exception_type": "asyncio_timeout_error"})

@app.exception_handler(Exception)
def _app_unhandled_error_handler(_request: Request, exc: Exception): # pyright: ignore[reportUnusedFunction]
    return JSONResponse(status_code=500, content={"detail": str(exc), "exception_type": type(exc).__name__})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("go2_robot_sdk.webrtc_relay.webrtc_relay:app", host="localhost", port=8000, reload=True, log_level="info")