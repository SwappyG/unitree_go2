from __future__ import annotations

import typing as t
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse

from just_robots.go2_webrtc.go2_connection_messages import ConIngArgs
from just_robots.go2_webrtc.mock_go2.mock_go2 import MockGo2

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
    mock_go2 = MockGo2()
    app.state.mock_go2 = mock_go2  # stash it on app.state

    try:
        yield
    finally:
        await mock_go2.stop()


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


@app.post(
    "/con_ing/{path_ending}",
    response_class=PlainTextResponse,
)
async def con_ing(
    path_ending: str,
    args: ConIngArgs,
    mock_go2: t.Annotated[MockGo2, Depends(get_mock_go2)],
) -> str:
    """
    Mirrors MockGo2.on_con_ing

    - path_ending comes from URL
    - offer_json = args.data1
    - aes_key    = args.data2
    - returns AES-encrypted answer as plain text
    """
    return await mock_go2.on_con_ing(
        path_ending=path_ending,
        offer_json=args.data1,
        aes_key=args.data2,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9991, reload=True)
