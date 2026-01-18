from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from pydantic import BaseModel

TOPICS_TO_SUBSCRIBE_TO = [
    RTC_TOPIC["MULTIPLE_STATE"],
    RTC_TOPIC["SPORT_MOD_STATE"],
    RTC_TOPIC["LOW_STATE"],
    RTC_TOPIC["ULIDAR"],
    RTC_TOPIC["ULIDAR_ARRAY"],
    RTC_TOPIC["ULIDAR_STATE"],
    RTC_TOPIC["ROBOTODOM"],
]


class ConnectArgs(BaseModel):
    robot_ip: str = "192.168.12.1"
    robot_num: int = 0
    token: str = ""
    reconnect_to_go2: bool = False


class ConnectReply(BaseModel):
    robot_ip: str


class AddSubscriptionArgs(BaseModel):
    topic: str


class AddSubscriptionReply(BaseModel):
    pass


class RemoveSubscriptionArgs(BaseModel):
    topic: str


class RemoveSubscriptionReply(BaseModel):
    pass


class GetSubscriptionsArgs(BaseModel):
    pass


class GetSubscriptionsReply(BaseModel):
    subscribed_topics: list[str]


class DisconnectArgs(BaseModel):
    pass


class DisconnectReply(BaseModel):
    pass


class OfferArgs(BaseModel):
    offer_sdp: str
    offer_type: str


class OfferReply(BaseModel):
    offer_sdp: str
    offer_type: str
