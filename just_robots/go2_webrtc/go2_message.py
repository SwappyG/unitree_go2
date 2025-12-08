import typing as t

from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from pydantic import BaseModel, Field


class MotorState(BaseModel):
    q: float = 0.0
    qd: float = 0.0
    qdd: float = 0.0
    tau: float = 0.0


class LowState(BaseModel):
    motor_state: list[MotorState] = Field(
        default_factory=lambda: [MotorState() for _ in range(12)]
    )


"""
"mode": "mock",
        "progress": 0,
        "gait_type": "mock",
        "position": [0.0, 0.0, 0.0],
        "body_height": 0.0,
        "velocity": 0.0,
        "range_obstacle": [],
        "foot_force": 0.0,
        "foot_position_body": [0.0, 0.0, 0.0, 0.0],
        "foot_speed_body": [0.0, 0.0, 0.0, 0.0],
        "imu_state": {
            "quaternion": [0.0, 0.0, 0.0, 1.0],
            "accelerometer": [0.0, 0.0, 0.0],
            "gyroscope": [0.0, 0.0, 0.0],
            "rpy": [0.0, 0.0, 0.0],
            "temperature": 0.0,
        },
"""


class ImuState(BaseModel):
    quaternion: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    accelerometer: tuple[float, float, float] = (0, 0, 0)
    gyroscope: tuple[float, float, float] = (0, 0, 0)
    rpy: tuple[float, float, float] = (0, 0, 0)
    temperature: float = 0.0


class SportModeState(BaseModel):
    mode: str = "mock"
    progress: float = 0.0
    gait_type: str = "mock"
    position: tuple[float, float, float] = (0, 0, 0)
    body_height: float = 0.0
    velocity: float = 0.0
    range_obstacle: list[t.Any] = Field(default_factory=list)
    foot_force: float = 0.0
    foot_position_body: list[float] = Field(default_factory=lambda: [0, 0, 0, 0])
    foot_speed_body: list[float] = Field(default_factory=lambda: [0, 0, 0, 0])
    imu_state: ImuState = Field(default_factory=ImuState)


class Translation(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Quaternion(BaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class Pose(BaseModel):
    position: Translation = Field(default_factory=Translation)
    orientation: Quaternion = Field(default_factory=Quaternion)


class RobotOdometry(BaseModel):
    pose: Pose = Field(default_factory=Pose)


TOPIC_TO_MESSAGE_TYPE = {
    RTC_TOPIC["LOW_STATE"]: LowState,
    RTC_TOPIC["SPORT_MOD_STATE"]: SportModeState,
    RTC_TOPIC["ROBOTODOM"]: RobotOdometry,
}
