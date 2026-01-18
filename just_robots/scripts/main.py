async def main(
    relay_url: str,
    config: RobotConfig,
    on_robot_data: t.Callable[[RobotData], t.Coroutine[None, None, None]],
    on_video_track: t.Callable[[MediaStreamTrack], t.Coroutine[None, None, None]],
    on_lidar_update: t.Callable[[dict[str, t.Any]], t.Coroutine[None, None, None]],
    topics_to_subscribe_to: set[str] | None = None,
    firebase_auth_manager: FirebaseClient | None = None,
):
    async with WebRTCRelayClient(
        relay_url=str(relay_url),
        robot_config=config,
        on_video_track=on_video_track,
        on_lidar_frame=on_lidar_update,
        on_robot_data=on_robot_data,
        topics_to_subscribe_to=topics_to_subscribe_to,
        firebase_client=firebase_auth_manager,
    ) as client:
        logger.info("created webrtc relay client, calling start")
        await client.start(True)
        logger.info("created webrtc relay client, started")

        # start keyboard control loop
        keyboard_task = asyncio.create_task(keyboard_control_loop(client))
        try:
            await keyboard_task
        finally:
            keyboard_task.cancel()
            with contextlib.suppress(Exception):
                await keyboard_task

        while True:
            await asyncio.sleep(5)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Simple PC client for GO2 Pi bridge")
    p.add_argument("--api", default="http://localhost:8000", help="Pi bridge base URL")
    p.add_argument(
        "--robot-ip", default="192.168.12.1", help="GO2 AP IP (optional: call /connect first)"
    )
    p.add_argument("--robot-num", type=int, default=0)
    p.add_argument("--token", default="")
    p.add_argument(
        "--firebase-id-token",
        default=None,
        help="Firebase ID token for authentication (or set FIREBASE_ID_TOKEN env var)",
    )
    p.add_argument(
        "--firebase-config", default=None, help="Path to Firebase service account JSON file"
    )
    p.add_argument(
        "--firebase-api-key", default=None, help="Firebase API key for user authentication"
    )
    p.add_argument("--firebase-email", default=None, help="Firebase email for user authentication")
    p.add_argument(
        "--firebase-password", default=None, help="Firebase password for user authentication"
    )
    p.add_argument(
        "--dump-lidar",
        action="store_true",
        dest="dump_lidar",
        help="Write lidar frames to lidar_dump.txt",
    )
    p.add_argument(
        "--send-ping", action="store_true", help="Send a small bytes payload on datachannel open"
    )
    p.add_argument(
        "--disconnect-on-exit", default=True, action="store_true", help="Call /disconnect on exit"
    )
    args = p.parse_args()

    # Initialize Firebase authentication if provided
    firebase_auth_manager = None
    if args.firebase_id_token or args.firebase_config or args.firebase_api_key:
        firebase_auth_manager = FirebaseClient(
            firebase_client_config_filepath=Path(),
            firebase_email=args.firebase_email,
            firebase_password=args.firebase_password,
        )
        if not firebase_auth_manager.is_authenticated():
            logger.warning("Firebase authentication configured but no valid token available")
        else:
            logger.info("Firebase authentication enabled")

    config = RobotConfig(
        robot_ip_list=[args.robot_ip],
        token=args.token,
        conn_type="webrtc",
        enable_video=True,
        decode_lidar=True,
        publish_raw_voxel=True,
        obstacle_avoidance=True,
        conn_mode="single",
    )

    # async def on_video_track(track: MediaStreamTrack):
    #     print(f"Video track received: {track}")

    # async def on_robot_data(robot_data: RobotData):
    #     print(f"Robot data received: {robot_data}")

    # asyncio.run(main(
    #     relay_url=args.api,
    #     config=config,
    #     on_robot_data=on_robot_data,
    #     on_video_track=on_video_track,
    #     on_lidar_update=on_lidar_update
    # ))

    display_task: asyncio.Task[None] | None = None
    try:

        async def on_video_track(track: MediaStreamTrack):
            logger.info(f"got video track: {track}")
            global display_task
            if display_task is not None:
                display_task.cancel()
                await display_task

            display_task = asyncio.create_task(display_video(track))

        async def on_lidar_update(lidar_frame: dict[str, t.Any]):
            print(
                f"Lidar frame received with {lidar_frame.get('decoded_data', {}).get('face_count', 0)} faces"
            )

        # vmv_viewer = vmv.VoxelMapViewer(flip_winding=False, compute_normals_every=1)
        # ff = open("lidar_dump.txt", mode="w+") if args.dump_lidar else None
        # num_writes = 0
        # try:
        #     vmv_viewer.start()
        #     async def on_lidar_update(lidar_frame: dict[str, t.Any]):
        #         global num_writes
        #         dec = lidar_frame["decoded_data"]
        #         meta = lidar_frame["data"]
        #         positions = dec["positions"]           # np.uint8, length = face_count*12
        #         face_count = int(dec["face_count"])
        #         vmv_viewer.submit_u8(
        #             positions_u8=positions,
        #             face_count=face_count,
        #             resolution=float(meta["resolution"]),
        #             origin_xyz=meta["origin"],
        #         )
        #         if ff and num_writes < 1000:
        #             num_writes += 1
        #             combined = lidar_frame["compressed_metadata"] + lidar_frame["compressed_data"]
        #             b64 = base64.b64encode(combined).decode("ascii")
        #             record = {"frame": b64}
        #             ff.write(json.dumps(record) + "\n")

        # New robot data hook
        # async def on_robot_data(robot_data):
        #     # logger.debug("on robot data")
        #     try:
        #         if robot_data and robot_data.odometry_data:
        #             odom = robot_data.odometry_data
        #             # vmv_viewer.submit_robot_pose(
        #             #     position=odom.position,         # {"x":..,"y":..,"z":..}
        #             #     orientation=odom.orientation,   # {"x":..,"y":..,"z":..,"w":..}
        #             # )
        #             logger.info("Odom position: %s", json.dumps(odom.position))
        #             logger.info("Odom orientation: %s", json.dumps(odom.orientation))
        #     except Exception as e:
        #         logger.warning(f"robot pose update failed: {e}")

        async def on_robot_data(robot_data: RobotData):
            logger.info(f"robot data received: {robot_data}")

        asyncio.run(
            main(
                relay_url=args.api,
                config=config,
                on_robot_data=on_robot_data,
                on_video_track=on_video_track,
                on_lidar_update=on_lidar_update,
                topics_to_subscribe_to=TOPICS_TO_SUBSCRIBE_TO,
                firebase_auth_manager=firebase_auth_manager,
            )
        )
        # finally:
        #     if ff:
        #         ff.close()
        #     vmv_viewer.close()
    finally:
        if display_task is not None:
            display_task.cancel()
            asyncio.wait_for(display_task, timeout=None)
