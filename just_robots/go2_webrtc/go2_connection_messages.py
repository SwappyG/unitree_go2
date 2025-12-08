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
    type: str = "validation"
    topic: str = ""
    data: str

    @staticmethod
    def validation_ok() -> ValidationMessage:
        return ValidationMessage(data="Validation Ok.")


class VideoMessage(BaseModel):
    type: str = "vid"
    topic: str = ""
    data: str

    @staticmethod
    def video_on() -> VideoMessage:
        return VideoMessage(data="on")

    @staticmethod
    def video_off() -> VideoMessage:
        return VideoMessage(data="off")


class GenericMessage(BaseModel):
    type: str
    topic: str = ""
    data: str | dict[str, t.Any]


class MessageMessage(BaseModel):
    type: str = ""
    topic: str
    data: str | dict[str, t.Any]
