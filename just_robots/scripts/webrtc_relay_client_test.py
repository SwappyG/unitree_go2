import asyncio
from just_robots.webrtc_relay.webrtc_relay_client import WebRTCRelayClient
from just_robots.utils.settings import get_just_robots_settings
from go2_robot_sdk.domain.entities.robot_config import RobotConfig
from go2_robot_sdk.domain.entities.robot_data import RobotData
from aiortc import MediaStreamTrack
import typing as t
from just_robots_firebase_client.firebase_client_authenticated import FirebaseClientAuthenticated
from just_robots_firebase_client.firebase_client import FirebaseClient
import getpass
from just_robots.utils.package_paths import get_package_root

async def on_robot_data(robot_data: RobotData):
    print(f"Robot data: {robot_data}")

async def on_video_track(video_track: MediaStreamTrack):
    print(f"Video track: {video_track}")

async def on_lidar_frame(lidar_frame: dict[str, t.Any]):
    print(f"Lidar frame: {lidar_frame}")

async def main():
    _settings = get_just_robots_settings()
    # email = getpass.getpass("Enter your email: ")
    # password = getpass.getpass("Enter your password: ")
    email = "abc@gmail.com"
    password = "123456"
    auth_cli = await FirebaseClientAuthenticated.from_sign_in_with_email_and_password_reply(
        config=get_package_root() / _settings.FIREBASE_CONFIG_PATH,
        email=email,
        password=password,
    )
    async with WebRTCRelayClient(
        relay_url="http://localhost:8000",
        robot_config=RobotConfig.from_params(
            robot_ip="localhost",
            token="",
            conn_type="webrtc",
            enable_video=False,
            decode_lidar=False,
            publish_raw_voxel=False,
            obstacle_avoidance=False,
        ),
        on_robot_data=on_robot_data,
        on_video_track=on_video_track,
        on_lidar_frame=on_lidar_frame,
        firebase_client=auth_cli,
    ) as client:
        await client.connect_to_go2()
        await client.add_topic_to_subscriptions("rt/utlidar/lowstate")
        await asyncio.to_thread(lambda : input("Press Enter to exit..."))

if __name__ == "__main__":
    settings = get_just_robots_settings()
    asyncio.run(main())