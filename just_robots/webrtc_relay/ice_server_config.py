"""
Utility module for configuring WebRTC ICE servers (STUN/TURN) from environment variables.

This module helps configure ICE servers for WebRTC peer connections when clients
and relays are on different networks, enabling NAT traversal.

Configuration Examples:
-----------------------

1. STUN servers only (for NAT discovery):
   export WEBRTC_STUN_SERVERS="stun:stun.l.google.com:19302,stun:stun1.example.com:3478"

2. TURN servers (for relay when direct connection fails):
   export WEBRTC_TURN_SERVERS="turn:turn.example.com:3478,username:password"

3. Multiple TURN servers:
   export WEBRTC_TURN_SERVERS="turn:turn1.example.com:3478,user1:pass1|turn:turn2.example.com:3478,user2:pass2"

4. Combined STUN and TURN:
   export WEBRTC_STUN_SERVERS="stun:stun.l.google.com:19302"
   export WEBRTC_TURN_SERVERS="turn:turn.example.com:3478,username:password"

5. JSON format for complex configurations:
   export WEBRTC_ICE_SERVERS='[{"urls":"stun:stun.l.google.com:19302"},{"urls":"turn:turn.example.com:3478","username":"user","credential":"pass"}]'

If no servers are configured, a default Google STUN server will be used.
"""

import os
import logging
from typing import List
from aiortc import RTCConfiguration, RTCIceServer  # type: ignore

logger = logging.getLogger(__name__)


def parse_ice_servers_from_env() -> List[dict]:
    """
    Parse ICE servers from environment variables.
    
    Supports multiple formats:
    1. STUN servers: WEBRTC_STUN_SERVERS="stun:stun1.example.com:3478,stun:stun2.example.com:3478"
    2. TURN servers: WEBRTC_TURN_SERVERS="turn:turn1.example.com:3478,username1:password1|turn:turn2.example.com:3478,username2:password2"
    3. Combined: WEBRTC_ICE_SERVERS (JSON format for complex configurations)
    
    Returns:
        List of ICE server dictionaries compatible with aiortc RTCConfiguration
    """
    ice_servers: List[dict] = []
    
    # Parse STUN servers (comma-separated list of URLs)
    stun_servers = os.getenv("WEBRTC_STUN_SERVERS", "")
    if stun_servers:
        for stun_url in stun_servers.split(","):
            stun_url = stun_url.strip()
            if stun_url:
                # Ensure URL starts with stun: protocol
                if not stun_url.startswith("stun:"):
                    if "://" in stun_url:
                        # Extract host:port if full URL provided
                        stun_url = "stun:" + stun_url.split("://", 1)[1]
                    else:
                        stun_url = f"stun:{stun_url}"
                ice_servers.append({"urls": stun_url})
                logger.info(f"Added STUN server: {stun_url}")
    
    # Parse TURN servers (pipe-separated list, each with format: "url,username:password")
    turn_servers = os.getenv("WEBRTC_TURN_SERVERS", "")
    if turn_servers:
        for turn_config in turn_servers.split("|"):
            turn_config = turn_config.strip()
            if not turn_config:
                continue
                
            parts = turn_config.split(",")
            if len(parts) >= 2:
                turn_url = parts[0].strip()
                creds = parts[1].strip()
                
                # Ensure URL starts with turn: protocol
                if not turn_url.startswith("turn:"):
                    if "://" in turn_url:
                        turn_url = "turn:" + turn_url.split("://", 1)[1]
                    else:
                        turn_url = f"turn:{turn_url}"
                
                # Parse username:password
                if ":" in creds:
                    username, password = creds.split(":", 1)
                    ice_servers.append({
                        "urls": turn_url,
                        "username": username.strip(),
                        "credential": password.strip()
                    })
                    logger.info(f"Added TURN server: {turn_url} (username: {username.strip()})")
                else:
                    logger.warning(f"Invalid TURN server format (missing username:password): {turn_config}")
            else:
                logger.warning(f"Invalid TURN server format (expected url,username:password): {turn_config}")
    
    # Parse combined JSON format for complex configurations
    ice_servers_json = os.getenv("WEBRTC_ICE_SERVERS", "")
    if ice_servers_json:
        try:
            import json
            parsed_servers = json.loads(ice_servers_json)
            if isinstance(parsed_servers, list):
                # Normalize each server: ensure urls is a string, not a list
                for server in parsed_servers:
                    if isinstance(server, dict):
                        normalized_server = server.copy()
                        # Handle urls field - it can be a string or list in JSON
                        if 'urls' in normalized_server:
                            urls_value = normalized_server['urls']
                            if isinstance(urls_value, list):
                                # If it's a list, take the first URL or join them
                                if urls_value:
                                    normalized_server['urls'] = urls_value[0]
                                else:
                                    logger.warning(f"Skipping ICE server with empty urls list: {server}")
                                    continue
                            elif not isinstance(urls_value, str):
                                logger.warning(f"Skipping ICE server with invalid urls type: {server}")
                                continue
                        ice_servers.append(normalized_server)
                    else:
                        logger.warning(f"Skipping non-dict ICE server: {server}")
                logger.info(f"Added {len([s for s in parsed_servers if isinstance(s, dict)])} ICE servers from JSON configuration")
            else:
                logger.warning("WEBRTC_ICE_SERVERS must be a JSON array")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse WEBRTC_ICE_SERVERS JSON: {e}")
    
    # Add default Google STUN server if no servers configured
    if not ice_servers:
        default_stun = {"urls": "stun:stun.l.google.com:19302"}
        ice_servers.append(default_stun)
        logger.info("No ICE servers configured, using default Google STUN server")
    else:
        logger.info(f"Configured {len(ice_servers)} ICE server(s)")
    
    return ice_servers


def get_rtc_configuration() -> RTCConfiguration:
    """
    Get RTCConfiguration with ICE servers parsed from environment variables.
    
    Returns:
        RTCConfiguration object with ICE servers configured
    """
    ice_servers = parse_ice_servers_from_env()
    
    # Convert dictionaries to RTCIceServer objects (aiortc expects RTCIceServer objects, not dicts)
    rtc_ice_servers = []
    for server in ice_servers:
        if isinstance(server, dict):
            try:
                # Extract urls - handle both string and list formats
                urls_value = server.get('urls', '')
                if isinstance(urls_value, list):
                    urls = urls_value[0] if urls_value else ''
                elif isinstance(urls_value, str):
                    urls = urls_value
                else:
                    logger.warning(f"Skipping ICE server with invalid urls type: {server}")
                    continue
                
                if not urls:
                    logger.warning(f"Skipping ICE server with empty urls: {server}")
                    continue
                
                # Extract optional credentials for TURN servers
                username = server.get('username', None)
                credential = server.get('credential', None)
                
                # Create RTCIceServer object
                if username and credential:
                    rtc_ice_server = RTCIceServer(
                        urls=urls,
                        username=username,
                        credential=credential
                    )
                else:
                    rtc_ice_server = RTCIceServer(urls=urls)
                
                rtc_ice_servers.append(rtc_ice_server)
            except Exception as e:
                logger.warning(f"Failed to create RTCIceServer from {server}: {e}")
                continue
        else:
            logger.warning(f"Skipping non-dict ICE server: {server}")
    
    try:
        return RTCConfiguration(iceServers=rtc_ice_servers)
    except Exception as e:
        logger.error(f"Failed to create RTCConfiguration with ICE servers: {e}")
        logger.error(f"ICE servers that caused error: {len(rtc_ice_servers)} servers")
        # Fallback to empty configuration
        return RTCConfiguration(iceServers=[])


def get_ice_servers_list() -> List[dict]:
    """
    Get list of ICE servers parsed from environment variables (useful for logging).
    
    Returns:
        List of ICE server dictionaries
    """
    return parse_ice_servers_from_env()
