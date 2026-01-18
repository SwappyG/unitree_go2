import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from just_robots_firebase_client.firebase_client import FirebaseClient

from just_robots.fastapi_utils.fastapi_server_utils import make_app
from just_robots.utils.package_paths import get_package_root
from just_robots.utils.settings import get_just_robots_settings
from just_robots.webrtc_relay.webrtc_relay import WebRTCRelay
from just_robots.webrtc_relay.webrtc_relay_endpoint_go2 import router as go2_router
from just_robots.webrtc_relay.webrtc_relay_endpoint_webrtc import (
    router as webrtc_router,
)
from just_robots.fastapi_utils.fastapi_exceptions import StateException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    just_robots_settings = get_just_robots_settings()
    config_path = get_package_root() / just_robots_settings.FIREBASE_CONFIG_PATH
    logger.info("starting fastapi")

    # Initialize Firebase authentication
    logger.info(f"Firebase config path: {config_path}")

    firebase_client = await FirebaseClient.from_config(config=config_path)
    fastapi_app.state.state = WebRTCRelay(
        firebase_client=firebase_client, settings=just_robots_settings
    )

    # clean shutdown
    try:
        logger.debug("yielding fastapi app")
        yield
    finally:
        logger.debug("cleaning up fastapi")
        await fastapi_app.state.state.shutdown()


app = make_app(
    lifespan=lifespan,
    routers=[
        (go2_router, "/go2"),
        (webrtc_router, "/webrtc"),
    ],
    title="Webrtc Relay Server",
    allowed_origins=["*"],
    allowed_headers=["*"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "just_robots.scripts.webrtc_relay_server:app",
        host="localhost",
        port=8000,
        reload=False,
        log_level="info",
    )
