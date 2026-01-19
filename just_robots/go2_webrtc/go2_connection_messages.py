from __future__ import annotations

import typing as t

from pydantic import BaseModel


class ConNotifyReply(BaseModel):
    code: int = 0
    msg: str = "ok"
    data1: str


class ConIngArgs(BaseModel):
    data1: str
    data2: str


class WebRtcAnswer(BaseModel):
    sdp: str
    type: str


class ValidationMessage(BaseModel):
    type: t.Literal["validation"] = "validation"
    topic: str = ""
    data: str

    @staticmethod
    def validation_ok() -> ValidationMessage:
        return ValidationMessage(data="Validation Ok.")


class SubscribeMessage(BaseModel):
    type: t.Literal["subscribe"] = "subscribe"
    topic: str


class UnsubscribeMessage(BaseModel):
    type: t.Literal["unsubscribe"] = "unsubscribe"
    topic: str


class VideoMessage(BaseModel):
    type: t.Literal["vid"] = "vid"
    topic: t.Literal[""] = ""
    data: t.Literal["on", "off"]

    @staticmethod
    def video_on() -> VideoMessage:
        return VideoMessage(data="on")

    @staticmethod
    def video_off() -> VideoMessage:
        return VideoMessage(data="off")


class MessageMessage(BaseModel):
    type: t.Literal["msg"] = "msg"
    topic: str
    data: str | dict[str, t.Any]


class GenericMessage(BaseModel):
    type: str
    topic: str = ""
    data: str | dict[str, t.Any] | None = None
