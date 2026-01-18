#!/usr/bin/env python3
"""
Launcher script for the GO2 PyQt5 GUI Client.

This script provides an easy way to launch the GUI client with common configurations.
"""
import sys
import subprocess
import argparse


def check_requirements():
    """Check if required packages are installed."""
    try:
        import PySide6
        import qasync
        import aiortc
        import open3d
        import cv2
        return True
    except ImportError as e:
        print(f"Missing required package: {e.name}")
        print("\nPlease install requirements:")
        print("  pip install -r requirements.txt")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Launch GO2 Robot PyQt5 GUI Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Connect to robot on default IP
  python launch_gui.py
  
  # Connect to robot with specific IP
  python launch_gui.py --robot-ip 192.168.12.1
  
  # Connect to relay server on different host
  python launch_gui.py --api http://10.0.0.5:8000
  
  # With authentication token
  python launch_gui.py --token your_token_here
        """
    )
    
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="WebRTC relay server URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--robot-ip",
        default="192.168.12.1",
        help="GO2 robot IP address (default: 192.168.12.1)"
    )
    parser.add_argument(
        "--token",
        default="",
        help="Robot authentication token (default: empty)"
    )
    parser.add_argument(
        "--firebase-id-token",
        default=None,
        help="Firebase ID token for authentication (or set FIREBASE_ID_TOKEN env var)"
    )
    parser.add_argument(
        "--firebase-config",
        default=None,
        help="Path to Firebase service account JSON file"
    )
    parser.add_argument(
        "--firebase-api-key",
        default=None,
        help="Firebase API key for user authentication"
    )
    parser.add_argument(
        "--firebase-email",
        default=None,
        help="Firebase email for user authentication"
    )
    parser.add_argument(
        "--firebase-password",
        default=None,
        help="Firebase password for user authentication"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check requirements, don't launch"
    )
    
    args = parser.parse_args()
    
    # Check requirements
    if not check_requirements():
        return 1
    
    if args.check_only:
        print("All requirements satisfied!")
        return 0
    
    # Launch GUI client
    print("Launching GO2 GUI Client...")
    print(f"  Relay Server: {args.api}")
    print(f"  Robot IP: {args.robot_ip}")
    print(f"  Token: {'<set>' if args.token else '<not set>'}")
    print()
    
    from go2_robot_sdk.webrtc_relay.gui_client import main as gui_main
    
    # Override sys.argv for the GUI client
    sys_argv = [
        sys.argv[0],
        "--api", args.api,
        "--robot-ip", args.robot_ip,
        "--token", args.token,
    ]
    
    # Add Firebase auth arguments if provided
    if args.firebase_id_token:
        sys_argv.extend(["--firebase-id-token", args.firebase_id_token])
    if args.firebase_config:
        sys_argv.extend(["--firebase-config", args.firebase_config])
    if args.firebase_api_key:
        sys_argv.extend(["--firebase-api-key", args.firebase_api_key])
    if args.firebase_email:
        sys_argv.extend(["--firebase-email", args.firebase_email])
    if args.firebase_password:
        sys_argv.extend(["--firebase-password", args.firebase_password])
    
    sys.argv = sys_argv
    
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
