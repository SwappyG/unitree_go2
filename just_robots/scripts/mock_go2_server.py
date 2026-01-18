from __future__ import annotations

import json
import typing as t
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse

from just_robots.utils.logging import logging
from just_robots.go2_webrtc.mock_go2.mock_go2 import MockGo2

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# FastAPI app with lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App lifespan:
    - create MockGo2
    - start it when app starts
    - stop it when app shuts down
    """
    logger.info("Starting MockGo2 server")
    mock_go2 = MockGo2()
    logger.info("MockGo2 created")
    app.state.mock_go2 = mock_go2  # stash it on app.state

    try:
        yield
    finally:
        logger.info("Stopping MockGo2")
        await mock_go2.stop()
        logger.info("Tearing down MockGo2 server")


app = FastAPI(lifespan=lifespan)


def get_mock_go2(request: Request) -> MockGo2:
    return request.app.state.mock_go2


@app.post("/con_notify", response_class=PlainTextResponse)
async def con_notify(
    mock_go2: t.Annotated[MockGo2, Depends(get_mock_go2)],
) -> str:
    """
    Mirrors MockGo2.on_con_notify

    - Takes no request body
    - Returns ConNotifyReply(code=0, msg="ok", data1=<plain_text_str>)
    """
    return await mock_go2.on_con_notify()


# @app.post(
#     "/con_ing/{path_ending}",
#     response_class=PlainTextResponse,
# )
# async def con_ing(
#     path_ending: str,
#     args: ConIngArgs,
#     mock_go2: t.Annotated[MockGo2, Depends(get_mock_go2)],
# ) -> str:
#     """
#     Mirrors MockGo2.on_con_ing

#     - path_ending comes from URL
#     - offer_json = args.data1
#     - aes_key    = args.data2
#     - returns AES-encrypted answer as plain text
#     """
#     return await mock_go2.on_con_ing(
#         path_ending=path_ending,
#         offer_json=args.data1,
#         aes_key=args.data2,
#     )


@app.post(
    "/con_ing_{path_ending}",
    response_class=PlainTextResponse,
)
async def con_ing_underscore(
    path_ending: str,
    request: Request,
    mock_go2: t.Annotated[MockGo2, Depends(get_mock_go2)],
) -> str:
    """
    Handle HttpClient format: /con_ing_{path_ending}

    HttpClient sends JSON body with Content-Type: application/x-www-form-urlencoded.
    The body is actually JSON (json.dumps), so we parse it as JSON.
    """
    # Read raw body and parse as JSON
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    encrypted_data = json.loads(body_str)

    # Extract data1 and data2, ensuring they're strings
    data1 = encrypted_data.get("data1", "")
    data2 = encrypted_data.get("data2", "")
    if isinstance(data1, list):
        data1 = data1[0] if data1 else ""
    if isinstance(data2, list):
        data2 = data2[0] if data2 else ""

    return await mock_go2.on_con_ing(
        path_ending=path_ending,
        offer_json=str(data1),
        aes_key=str(data2),
    )


if __name__ == "__main__":
    uvicorn.run("just_robots.scripts.mock_go2_server:app", host="127.0.0.1", port=9991, reload=False)
