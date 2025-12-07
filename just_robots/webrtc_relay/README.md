# WebRTC Relay Server - Complete Setup Guide

This guide provides comprehensive instructions for setting up and configuring the WebRTC relay server and client, including Firebase authentication and TURN/STUN server configuration for internet connectivity.

## Table of Contents

1. [Overview](#overview)
2. [Server-Side Configuration](#server-side-configuration)
3. [Client-Side Configuration](#client-side-configuration)
---

## Overview

The WebRTC relay server enables remote access to Unitree Go2 robots over the internet. It acts as an intermediary between clients (your computer) and the Go2 robot, relaying video, lidar data, and control commands.

**Key Features:**
- Secure authentication via Firebase
- Video streaming from Go2 camera
- Lidar data streaming
- Robot control and telemetry
- Internet connectivity via TURN/STUN servers
- Real-time statistics monitoring

---

## Server-Side Configuration

Create a `.env` file in the server's working directory (where you run the relay server).

### Complete Server `.env` Example

```bash
# Firebase Configuration
FIREBASE_CONFIG_PATH=/home/user/go2_ros2_sdk/go2_robot_sdk/go2_robot_sdk/webrtc_relay/firebase-credentials.json
FIREBASE_AUTH_ENABLED=true

# TURN/STUN Servers (for internet connections) - GET THESE PARAMS FROM BITWARDEN
WEBRTC_STUN_SERVERS=stun:stun.l.google.com:19302 # THIS IS IN BITWARDEN
WEBRTC_TURN_SERVERS=turn:your-turn-server.com:3478,myuser:mypassword123 # ALSO IN BITWARDEN

```

---

## Client-Side Configuration

Create a `.env` file in the client's working directory (where you run the client application).

### Required Configuration

### Complete Client `.env` Example

```bash
# Relay Server Configuration. # This will be public url
RELAY_SERVER_URL=https://perlpi5.just-robots.com

# Firebase Authentication (choose one method)

# Method: Email/Password (uncomment to use instead) Only users added to firebase will be selected
FIREBASE_API_KEY=your-firebase-api-key


# TURN/STUN Servers (for internet connections)
# ADD THE INFO ABOUT THE STUN AND TURN SERVER
WEBRTC_STUN_SERVERS=stun:stun.l.google.com:19302
WEBRTC_TURN_SERVERS=turn:your-turn-server.com:3478,myuser:mypassword123

```
---

