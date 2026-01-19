"""
WebRTC Statistics Monitor using W3C getStats() API

Provides real-time monitoring of WebRTC connection quality including:
- Round-trip time (RTT)
- Jitter
- Packet loss
- Bitrate (send/receive)
- Data channel statistics
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from collections import defaultdict, deque
import statistics
from aiortc import RTCPeerConnection

logger = logging.getLogger(__name__)


class WebRTCStatsMonitor:
    """Monitor WebRTC connection stats using getStats() API"""
    
    def __init__(self, connection_name: str, peer_connection: RTCPeerConnection, debug: bool = False):
        self.connection_name = connection_name
        self.pc = peer_connection
        self.running = False
        self.stats_task = None
        self.debug = debug  # Enable detailed debug logging
        
        # Store recent stats for trending
        self.rtt_history = deque(maxlen=100)
        self.jitter_history = deque(maxlen=100)
        self.packet_loss_history = deque(maxlen=100)
        self.bytes_sent_history = deque(maxlen=100)
        self.bytes_received_history = deque(maxlen=100)
        
        self.last_bytes_sent = 0
        self.last_bytes_received = 0
        self.last_timestamp = None
    
    async def start(self, interval_seconds: float = 5.0):
        """Start collecting stats periodically"""
        self.running = True
        self.stats_task = asyncio.create_task(self._collect_stats_loop(interval_seconds))
        logger.info(f"[{self.connection_name}] WebRTC stats monitoring started (interval={interval_seconds}s)")
    
    async def stop(self):
        """Stop collecting stats"""
        self.running = False
        if self.stats_task:
            self.stats_task.cancel()
            try:
                await self.stats_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[{self.connection_name}] WebRTC stats monitoring stopped")
    
    async def _collect_stats_loop(self, interval: float):
        """Periodically collect and log stats"""
        while self.running:
            try:
                await asyncio.sleep(interval)
                await self.collect_and_log_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.connection_name}] Stats collection error: {e}")
    
    async def collect_and_log_stats(self):
        """Collect current stats and log summary"""
        try:
            # Check connection state first - stats are only meaningful when connected
            if self.pc.connectionState != "connected":
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[{self.connection_name}] Connection not ready: {self.pc.connectionState}, skipping stats collection")
                return
            
            stats = await self.pc.getStats()
            
            # Debug: log what stats we're getting
            if self.debug or logger.isEnabledFor(logging.DEBUG):
                stats_types = [r.type for r in stats.values()]
                logger.info(f"[{self.connection_name}] Raw stats types: {stats_types}")
                
                # If no candidate-pair, log all available report types and their attributes
                if "candidate-pair" not in stats_types:
                    logger.warning(f"[{self.connection_name}] No candidate-pair found in stats!")
                    for report in stats.values():
                        attrs = {attr: getattr(report, attr, None) 
                                for attr in dir(report) 
                                if not attr.startswith('_') and not callable(getattr(report, attr, None))}
                        logger.info(f"[{self.connection_name}] {report.type} attributes: {list(attrs.keys())}")
                else:
                    # Log candidate-pair details
                    for report in stats.values():
                        if report.type == "candidate-pair":
                            attrs = {attr: getattr(report, attr, None) 
                                    for attr in dir(report) 
                                    if not attr.startswith('_') and not callable(getattr(report, attr, None))}
                            logger.info(f"[{self.connection_name}] candidate-pair attributes: {attrs}")
            
            # Parse stats from aiortc format
            metrics = self._parse_stats(stats)
            
            # Only log summary if we have some stats data
            has_data = (metrics.get("candidate_pair") or 
                       metrics.get("remote_inbound_rtp") or 
                       metrics.get("transport"))
            if not has_data:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[{self.connection_name}] No stats data available yet")
                return
            
            # Log summary
            self._log_stats_summary(metrics)
        except Exception as e:
            logger.debug(f"[{self.connection_name}] Could not collect stats: {e}")
    
    def _parse_stats(self, stats) -> Dict[str, Any]:
        """Parse aiortc stats into useful metrics"""
        metrics = {
            "connection": self.connection_name,
            "timestamp": None,
            "candidate_pair": {},
            "inbound_rtp": {},
            "remote_inbound_rtp": {},  # This contains RTT!
            "outbound_rtp": {},
            "data_channel": {}
        }
        
        # aiortc returns a list of RTCStatsReport objects
        for report in stats.values():
            report_type = report.type
            
            if report_type == "candidate-pair":
                # Check state - aiortc may use different attribute names
                state = getattr(report, "state", None)
                
                # Accept any active state, not just "succeeded"
                # States can be: "frozen", "waiting", "in-progress", "succeeded", "failed"
                # Also accept if state is None (might not be available in all aiortc versions)
                if state is None or state in ("succeeded", "in-progress"):
                    # Network metrics - try multiple possible attribute names for RTT
                    # WebRTC spec uses "currentRoundTripTime" but aiortc might use different names
                    rtt = (getattr(report, "currentRoundTripTime", None) or 
                           getattr(report, "roundTripTime", None) or
                           getattr(report, "rtt", None))
                    
                    # Get other metrics
                    bytes_sent = getattr(report, "bytesSent", None) or getattr(report, "bytes_sent", 0)
                    bytes_received = getattr(report, "bytesReceived", None) or getattr(report, "bytes_received", 0)
                    
                    # Only create candidate_pair entry if we have some data
                    if bytes_sent is not None or bytes_received is not None or rtt is not None:
                        metrics["candidate_pair"] = {
                            "rtt": rtt,  # in seconds
                            "state": state,
                            "bytes_sent": bytes_sent if bytes_sent is not None else 0,
                            "bytes_received": bytes_received if bytes_received is not None else 0,
                            "requests_sent": getattr(report, "requestsSent", None) or getattr(report, "requests_sent", 0),
                            "responses_received": getattr(report, "responsesReceived", None) or getattr(report, "responses_received", 0),
                        }
                        
                        # Calculate RTT in ms
                        if rtt is not None and rtt > 0:
                            rtt_ms = rtt * 1000
                            self.rtt_history.append(rtt_ms)
                            metrics["candidate_pair"]["rtt_ms"] = rtt_ms
                        elif logger.isEnabledFor(logging.DEBUG):
                            # Debug: log available attributes if RTT is missing
                            attrs = [attr for attr in dir(report) if not attr.startswith('_') and not callable(getattr(report, attr, None))]
                            logger.debug(
                                f"[{self.connection_name}] Candidate-pair found but no RTT. "
                                f"State={state}, Available attrs: {attrs}"
                            )
            
            elif report_type == "remote-inbound-rtp":
                # Remote inbound RTP contains RTT information!
                # This represents the remote peer's view of what we're sending to them
                # So if we're the relay and see remote-inbound-rtp, it means:
                # - We're sending to the remote peer (CLIENT or GO2)
                # - The remote peer is receiving and reporting RTT back to us
                rtt = getattr(report, "roundTripTime", None)
                metrics["remote_inbound_rtp"] = {
                    "rtt": rtt,  # in seconds
                    "packets_received": getattr(report, "packetsReceived", 0),
                    "packets_lost": getattr(report, "packetsLost", 0),
                    "fraction_lost": getattr(report, "fractionLost", 0),
                    "jitter": getattr(report, "jitter", None),
                    "kind": getattr(report, "kind", "unknown"),
                }
                
                # Extract RTT from remote-inbound-rtp (this is the actual RTT!)
                if rtt is not None and rtt > 0:
                    rtt_ms = rtt * 1000
                    self.rtt_history.append(rtt_ms)
                    # Also store in candidate_pair for compatibility
                    if not metrics["candidate_pair"]:
                        metrics["candidate_pair"] = {}
                    metrics["candidate_pair"]["rtt_ms"] = rtt_ms
                    metrics["candidate_pair"]["rtt"] = rtt
            
            elif report_type == "remote-outbound-rtp":
                # Remote outbound RTP - represents what the remote peer is sending to us
                # This doesn't contain RTT directly, but we can use it for other metrics
                metrics["remote_outbound_rtp"] = {
                    "packets_sent": getattr(report, "packetsSent", 0),
                    "bytes_sent": getattr(report, "bytesSent", 0),
                    "kind": getattr(report, "kind", "unknown"),
                }
            
            elif report_type == "inbound-rtp":
                # Receiving stream metrics
                # This represents what we're receiving from the remote peer
                # For GO2→RELAY: relay receives from GO2, so inbound-rtp shows GO2→RELAY metrics
                # For CLIENT→RELAY: relay receives from client, so inbound-rtp shows CLIENT→RELAY metrics
                raw_jitter = getattr(report, "jitter", None)
                metrics["inbound_rtp"] = {
                    "packets_received": getattr(report, "packetsReceived", 0),
                    "packets_lost": getattr(report, "packetsLost", 0),
                    "bytes_received": getattr(report, "bytesReceived", 0),
                    "jitter": raw_jitter,
                    "kind": getattr(report, "kind", "unknown"),  # video/audio
                }
                
                # Handle jitter conversion
                # WebRTC jitter is reported in RTP timestamp units, not seconds
                # Need to convert using clock rate (typically 90000 for video, 8000 for audio)
                if raw_jitter is not None and raw_jitter > 0:
                    # Get clock rate from report if available
                    clock_rate = getattr(report, "clockRate", None)
                    
                    # If no clock rate, try to infer from codec or use defaults
                    if clock_rate is None:
                        # Default clock rates: video=90000, audio=8000
                        if metrics["inbound_rtp"]["kind"] == "video":
                            clock_rate = 90000
                        else:
                            clock_rate = 8000
                    
                    # Check if jitter is already in a reasonable range (might be in seconds or ms already)
                    # If raw_jitter < 100, it might already be in seconds
                    # If raw_jitter < 100000, it might already be in milliseconds
                    if raw_jitter < 100:
                        # Likely already in seconds
                        jitter_ms = raw_jitter * 1000
                    elif raw_jitter < 100000:
                        # Might already be in milliseconds, but could also be RTP units
                        # Try RTP conversion first
                        jitter_seconds = raw_jitter / clock_rate
                        jitter_ms = jitter_seconds * 1000
                        
                        # If result is unreasonable, try treating as milliseconds
                        if jitter_ms > 10000:
                            jitter_ms = raw_jitter  # Assume already in ms
                    else:
                        # Large values are definitely RTP timestamp units
                        jitter_seconds = raw_jitter / clock_rate
                        jitter_ms = jitter_seconds * 1000
                    
                    # Sanity check: jitter should be reasonable (< 1000ms typically)
                    # Values > 10000ms are likely incorrect
                    if jitter_ms < 10000:  # Only log reasonable values
                        self.jitter_history.append(jitter_ms)
                        metrics["inbound_rtp"]["jitter_ms"] = jitter_ms
                    else:
                        # Log warning for unreasonable values and don't include in stats
                        logger.warning(
                            f"[{self.connection_name}] Ignoring unreasonable jitter: "
                            f"raw={raw_jitter}, clock_rate={clock_rate}, calculated={jitter_ms:.1f}ms. "
                            f"Jitter values > 10s are likely incorrect."
                        )
                        metrics["inbound_rtp"]["jitter_ms"] = None
                
                # Calculate packet loss rate
                total_packets = (metrics["inbound_rtp"]["packets_received"] + 
                               metrics["inbound_rtp"]["packets_lost"])
                if total_packets > 0:
                    loss_rate = metrics["inbound_rtp"]["packets_lost"] / total_packets * 100
                    self.packet_loss_history.append(loss_rate)
                    metrics["inbound_rtp"]["packet_loss_rate"] = loss_rate
            
            elif report_type == "transport":
                # Transport-level metrics (bytes sent/received)
                if not metrics.get("transport"):
                    metrics["transport"] = {}
                metrics["transport"].update({
                    "bytes_sent": getattr(report, "bytesSent", 0),
                    "bytes_received": getattr(report, "bytesReceived", 0),
                    "packets_sent": getattr(report, "packetsSent", 0),
                    "packets_received": getattr(report, "packetsReceived", 0),
                    "dtls_state": getattr(report, "dtlsState", None),
                })
                # Also store in candidate_pair for compatibility
                if not metrics["candidate_pair"]:
                    metrics["candidate_pair"] = {}
                metrics["candidate_pair"]["bytes_sent"] = getattr(report, "bytesSent", 0)
                metrics["candidate_pair"]["bytes_received"] = getattr(report, "bytesReceived", 0)
            
            elif report_type == "outbound-rtp":
                # Sending stream metrics
                metrics["outbound_rtp"] = {
                    "packets_sent": getattr(report, "packetsSent", 0),
                    "bytes_sent": getattr(report, "bytesSent", 0),
                    "kind": getattr(report, "kind", "unknown"),
                }
            
            elif report_type == "data-channel":
                # Data channel metrics
                metrics["data_channel"] = {
                    "messages_sent": getattr(report, "messagesSent", 0),
                    "messages_received": getattr(report, "messagesReceived", 0),
                    "bytes_sent": getattr(report, "bytesSent", 0),
                    "bytes_received": getattr(report, "bytesReceived", 0),
                }
        
        # Calculate bitrates from transport or candidate_pair
        current_time = asyncio.get_event_loop().time()
        bytes_sent = (metrics["candidate_pair"].get("bytes_sent") or 
                     metrics.get("transport", {}).get("bytes_sent"))
        bytes_received = (metrics["candidate_pair"].get("bytes_received") or 
                         metrics.get("transport", {}).get("bytes_received"))
        
        if self.last_timestamp and bytes_sent is not None:
            time_delta = current_time - self.last_timestamp
            if time_delta > 0:
                bytes_delta_sent = bytes_sent - self.last_bytes_sent
                bytes_delta_recv = bytes_received - self.last_bytes_received
                
                metrics["bitrate_mbps_sent"] = (bytes_delta_sent * 8 / time_delta) / 1_000_000
                metrics["bitrate_mbps_recv"] = (bytes_delta_recv * 8 / time_delta) / 1_000_000
        
        self.last_timestamp = current_time
        if bytes_sent is not None:
            self.last_bytes_sent = bytes_sent
            self.last_bytes_received = bytes_received
        
        return metrics
    
    def _log_stats_summary(self, metrics: Dict[str, Any]):
        """Log a readable summary of stats"""
        parts = [f"[{self.connection_name}] WebRTC:"]
        
        # Network stats - RTT can come from candidate_pair (via remote-inbound-rtp) or directly from remote_inbound_rtp
        rtt_ms = None
        if "rtt_ms" in metrics["candidate_pair"]:
            rtt_ms = metrics["candidate_pair"]["rtt_ms"]
        elif metrics.get("remote_inbound_rtp", {}).get("rtt"):
            rtt_ms = metrics["remote_inbound_rtp"]["rtt"] * 1000
        
        if rtt_ms is not None:
            parts.append(f"RTT={rtt_ms:.1f}ms")
        
        # Bitrate
        if "bitrate_mbps_sent" in metrics:
            parts.append(f"↑{metrics['bitrate_mbps_sent']:.2f}Mbps")
        if "bitrate_mbps_recv" in metrics:
            parts.append(f"↓{metrics['bitrate_mbps_recv']:.2f}Mbps")
        
        # Packet loss
        if "packet_loss_rate" in metrics["inbound_rtp"]:
            loss = metrics["inbound_rtp"]["packet_loss_rate"]
            parts.append(f"Loss={loss:.2f}%")
            if loss > 5:
                parts.append("⚠️ HIGH")
        
        # Jitter
        if "jitter_ms" in metrics["inbound_rtp"] and metrics["inbound_rtp"]["jitter_ms"] is not None:
            jitter = metrics["inbound_rtp"]["jitter_ms"]
            parts.append(f"Jitter={jitter:.1f}ms")
            if jitter > 30:
                parts.append("⚠️ HIGH")
        
        logger.info(" ".join(parts))
        
        # Log averages periodically
        if len(self.rtt_history) >= 20 and len(self.rtt_history) % 20 == 0:
            self._log_statistics()
    
    def _log_statistics(self):
        """Log statistical summary"""
        if self.rtt_history:
            rtt_list = list(self.rtt_history)
            logger.info(
                f"[{self.connection_name}] RTT Stats (n={len(rtt_list)}): "
                f"avg={statistics.mean(rtt_list):.1f}ms, "
                f"p50={statistics.median(rtt_list):.1f}ms, "
                f"p95={sorted(rtt_list)[int(len(rtt_list)*0.95)]:.1f}ms, "
                f"max={max(rtt_list):.1f}ms"
            )
        
        if self.jitter_history:
            jitter_list = list(self.jitter_history)
            logger.info(
                f"[{self.connection_name}] Jitter Stats: "
                f"avg={statistics.mean(jitter_list):.1f}ms, "
                f"p95={sorted(jitter_list)[int(len(jitter_list)*0.95)]:.1f}ms"
            )
        
        if self.packet_loss_history:
            loss_list = list(self.packet_loss_history)
            logger.info(
                f"[{self.connection_name}] Packet Loss: "
                f"avg={statistics.mean(loss_list):.2f}%, "
                f"max={max(loss_list):.2f}%"
            )

    async def get_current_stats(self) -> Dict[str, Any]:
        """Get current stats snapshot"""
        try:
            stats = await self.pc.getStats()
            return self._parse_stats(stats)
        except Exception as e:
            logger.error(f"[{self.connection_name}] Failed to get stats: {e}")
            return {}

