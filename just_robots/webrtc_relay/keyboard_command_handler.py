"""
Keyboard command handler for GO2 robot control.
Supports both Qt GUI and terminal interfaces with JSON-based configuration.
"""
import json
import os
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Set, Any, TYPE_CHECKING
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QKeyEvent

if TYPE_CHECKING:
    from go2_robot_sdk.webrtc_relay.webrtc_relay_client import WebRTCRelayClient


logger = logging.getLogger(__name__)


class InputAdapter(ABC):
    """Abstract base class for input adapters."""
    
    def __init__(self, handler: 'KeyboardCommandHandler'):
        self.handler = handler
    
    @abstractmethod
    def handle_key_press(self, key: Any) -> None:
        """Handle a key press event."""
        pass
    
    @abstractmethod
    def handle_key_release(self, key: Any) -> None:
        """Handle a key release event."""
        pass


class QtInputAdapter(InputAdapter):
    """Adapter for Qt key events."""
    
    def __init__(self, handler: 'KeyboardCommandHandler'):
        super().__init__(handler)
        self._qt_key_map = self._build_qt_key_map()
    
    def _build_qt_key_map(self) -> Dict[str, Qt.Key]:
        """Build mapping from JSON key strings to Qt.Key enum values."""
        qt_key_map = {}
        for attr_name in dir(Qt.Key):
            if attr_name.startswith('Key_'):
                qt_key_map[attr_name] = getattr(Qt.Key, attr_name)
        return qt_key_map
    
    def _qt_key_from_string(self, key_str: str) -> Optional[Qt.Key]:
        """Convert JSON key string to Qt.Key enum."""
        return self._qt_key_map.get(key_str)
    
    def handle_key_press(self, key: Qt.Key) -> None:
        """Handle Qt key press event."""
        # Convert Qt.Key to action using handler's config
        action = self.handler._get_action_for_qt_key(key)
        if action:
            self.handler.handle_key_press(action)
    
    def handle_key_release(self, key: Qt.Key) -> None:
        """Handle Qt key release event."""
        action = self.handler._get_action_for_qt_key(key)
        if action:
            self.handler.handle_key_release(action)


class TerminalInputAdapter(InputAdapter):
    """Adapter for terminal stdin input."""
    
    def __init__(self, handler: 'KeyboardCommandHandler'):
        super().__init__(handler)
        self._last_key_time = {}  # Track when keys were pressed
    
    def handle_key_press(self, key: str) -> None:
        """Handle terminal key press (character input)."""
        # Normalize key (lowercase, handle special cases)
        normalized_key = key.lower().strip()
        if normalized_key == " ":
            normalized_key = "space"
        
        # Convert terminal key to action using handler's config
        action = self.handler._get_action_for_terminal_key(normalized_key)
        if action:
            # For terminal, we treat each key press as a momentary command
            # Set velocity, then clear after a short delay
            self.handler.handle_key_press(action)
            # Schedule velocity clear after movement duration
            # This simulates key release behavior
            import time
            self._last_key_time[action] = time.time()
    
    def handle_key_release(self, key: str) -> None:
        """Handle terminal key release (for terminal, we treat release as immediate)."""
        # Terminal doesn't have key release events, so we don't do anything
        # The handler will manage state based on key presses
        pass


class KeyboardCommandHandler:
    """Unified keyboard command handler for Qt GUI and terminal interfaces."""
    
    def __init__(self, client: 'WebRTCRelayClient', config_path: Optional[str] = None):
        """
        Initialize the keyboard command handler.
        
        Args:
            client: WebRTCRelayClient instance for sending commands
            config_path: Path to JSON configuration file. If None, uses default path.
        """
        self.client = client
        self.config_path = config_path or self._get_default_config_path()
        
        # Load configuration
        self.config = self.load_config(self.config_path)
        
        # Movement state
        self.current_velocities = {"forward": 0.0, "strafe": 0.0, "rotation": 0.0}
        self.target_velocities = {"forward": 0.0, "strafe": 0.0, "rotation": 0.0}
        
        # Velocity ramping state
        self.ramp_start_times = {"forward": None, "strafe": None, "rotation": None}
        self.ramp_start_velocities = {"forward": 0.0, "strafe": 0.0, "rotation": 0.0}
        
        # Track pressed keys/actions
        self.pressed_actions: Set[str] = set()
        
        # Key mappings (populated from config)
        self._qt_key_to_action: Dict[Qt.Key, str] = {}
        self._terminal_key_to_action: Dict[str, str] = {}
        self._action_to_qt_key: Dict[str, Qt.Key] = {}
        self._action_to_terminal_key: Dict[str, str] = {}
        
        # Build key mappings
        self._build_key_mappings()
        
        # Timers (will be set by adapters if needed)
        self.ramp_timer: Optional[QTimer] = None
        self.move_timer: Optional[QTimer] = None
        self._ramp_task: Optional[asyncio.Task] = None
        self._move_task: Optional[asyncio.Task] = None
        self._ramp_future: Optional[Any] = None  # For run_coroutine_threadsafe
        self._move_future: Optional[Any] = None  # For run_coroutine_threadsafe
        
        # Client event loop reference
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _get_default_config_path(self) -> str:
        """Get default configuration file path."""
        # Get the directory where this file is located
        current_dir = Path(__file__).parent
        return str(current_dir / "keyboard_config.json")
    
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load and validate JSON configuration.
        
        Args:
            config_path: Path to JSON configuration file
            
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        if not os.path.exists(config_path):
            logger.warning(f"Config file not found at {config_path}, using hardcoded defaults")
            return self._get_default_config()
        
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
        
        # Validate structure
        if "key_bindings" not in config:
            raise ValueError("Config missing 'key_bindings' section")
        if "velocity" not in config:
            raise ValueError("Config missing 'velocity' section")
        if "ramp" not in config:
            raise ValueError("Config missing 'ramp' section")
        
        logger.info(f"Loaded keyboard configuration from {config_path}")
        return config
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration (hardcoded defaults)."""
        return {
            "key_bindings": {
                "forward": {"qt": "Key_W", "terminal": "w"},
                "backward": {"qt": "Key_S", "terminal": "s"},
                "strafe_left": {"qt": "Key_A", "terminal": "a"},
                "strafe_right": {"qt": "Key_D", "terminal": "d"},
                "rotate_left": {"qt": "Key_Left", "terminal": "z"},
                "rotate_right": {"qt": "Key_Right", "terminal": "c"},
                "stop": {"qt": "Key_Space", "terminal": " "},
                "quit": {"qt": "Key_P", "terminal": "q"}
            },
            "velocity": {
                "linear": 0.25,
                "rotation": 0.50
            },
            "ramp": {
                "ramp_time_ms": 1000,
                "update_interval_ms": 50,
                "ramp_update_interval_ms": 20
            }
        }
    
    def _build_key_mappings(self) -> None:
        """Build key-to-action mappings from configuration."""
        key_bindings = self.config["key_bindings"]
        
        # Build Qt key mappings
        qt_key_map = {}
        for attr_name in dir(Qt.Key):
            if attr_name.startswith('Key_'):
                qt_key_map[attr_name] = getattr(Qt.Key, attr_name)
        
        for action, keys in key_bindings.items():
            # Qt key mapping
            if "qt" in keys:
                qt_key_str = keys["qt"]
                qt_key = qt_key_map.get(qt_key_str)
                if qt_key:
                    self._qt_key_to_action[qt_key] = action
                    self._action_to_qt_key[action] = qt_key
            
            # Terminal key mapping
            if "terminal" in keys:
                terminal_key = keys["terminal"]
                self._terminal_key_to_action[terminal_key] = action
                self._action_to_terminal_key[action] = terminal_key
    
    def _get_action_for_qt_key(self, key: Qt.Key) -> Optional[str]:
        """Get action for Qt key code."""
        return self._qt_key_to_action.get(key)
    
    def _get_action_for_terminal_key(self, key: str) -> Optional[str]:
        """Get action for terminal key (character)."""
        # Handle special cases
        if key == " ":
            key = "space"
        return self._terminal_key_to_action.get(key)
    
    def handle_key_press(self, action: str) -> None:
        """Handle key press for an action."""
        self.pressed_actions.add(action)
        
        # Get velocity multipliers from config
        linear_velocity = self.config["velocity"]["linear"]
        rotation_velocity = self.config["velocity"]["rotation"]
        
        if action == "forward":
            self.set_target_velocity("forward", linear_velocity)
        elif action == "backward":
            self.set_target_velocity("forward", -linear_velocity)
        elif action == "strafe_left":
            self.set_target_velocity("strafe", linear_velocity)
        elif action == "strafe_right":
            self.set_target_velocity("strafe", -linear_velocity)
        elif action == "rotate_left":
            self.set_target_velocity("rotation", rotation_velocity)
        elif action == "rotate_right":
            self.set_target_velocity("rotation", -rotation_velocity)
        elif action == "stop":
            self.stop_movement()
        elif action == "quit":
            # Quit is handled by the caller (GUI or terminal loop)
            pass
    
    def handle_key_release(self, action: str) -> None:
        """Handle key release for an action."""
        self.pressed_actions.discard(action)
        
        # Check if opposite key is still pressed
        if action == "forward" or action == "backward":
            if "forward" not in self.pressed_actions and "backward" not in self.pressed_actions:
                self.set_target_velocity("forward", 0.0)
        elif action == "strafe_left" or action == "strafe_right":
            if "strafe_left" not in self.pressed_actions and "strafe_right" not in self.pressed_actions:
                self.set_target_velocity("strafe", 0.0)
        elif action == "rotate_left" or action == "rotate_right":
            if "rotate_left" not in self.pressed_actions and "rotate_right" not in self.pressed_actions:
                self.set_target_velocity("rotation", 0.0)
    
    def set_target_velocity(self, direction: str, value: float) -> None:
        """Set target velocity for a specific direction (with ramping)."""
        self.target_velocities[direction] = value
        
        # Start ramping if velocity changed
        if value != self.current_velocities[direction]:
            self.ramp_start_velocities[direction] = self.current_velocities[direction]
            self.ramp_start_times[direction] = self._get_current_time_ms()
            
            # Start ramp timer if not already running
            self._start_ramp_timer()
        
        # Start/update movement timer if any target velocity is non-zero
        if any(v != 0.0 for v in self.target_velocities.values()):
            self._start_move_timer()
        else:
            self._stop_move_timer()
    
    def _get_current_time_ms(self) -> int:
        """Get current time in milliseconds."""
        return int(time.time() * 1000)
    
    def _start_ramp_timer(self) -> None:
        """Start velocity ramping timer (Qt or asyncio based)."""
        if self.ramp_timer is not None:
            if not self.ramp_timer.isActive():
                self.ramp_timer.start()
        elif self._ramp_task is None and self._ramp_future is None:
            # Use asyncio for terminal mode
            try:
                loop = asyncio.get_running_loop()
                self._ramp_task = asyncio.create_task(self._ramp_loop())
            except RuntimeError:
                # No running loop - schedule on client's loop if available
                loop = getattr(self.client, "_loop", None)
                if loop is not None:
                    self._ramp_future = asyncio.run_coroutine_threadsafe(self._ramp_loop(), loop)
                else:
                    logger.warning("No event loop available for ramp timer")
    
    def _stop_ramp_timer(self) -> None:
        """Stop velocity ramping timer."""
        if self.ramp_timer is not None:
            self.ramp_timer.stop()
        if self._ramp_task is not None:
            self._ramp_task.cancel()
            self._ramp_task = None
        if self._ramp_future is not None:
            self._ramp_future.cancel()
            self._ramp_future = None
    
    def _start_move_timer(self) -> None:
        """Start movement command timer."""
        if self.move_timer is not None:
            if not self.move_timer.isActive():
                self.move_timer.start()
        elif self._move_task is None and self._move_future is None:
            # Use asyncio for terminal mode
            try:
                loop = asyncio.get_running_loop()
                self._move_task = asyncio.create_task(self._move_loop())
            except RuntimeError:
                # No running loop - schedule on client's loop if available
                loop = getattr(self.client, "_loop", None)
                if loop is not None:
                    self._move_future = asyncio.run_coroutine_threadsafe(self._move_loop(), loop)
                else:
                    logger.warning("No event loop available for move timer")
    
    def _stop_move_timer(self) -> None:
        """Stop movement command timer."""
        if self.move_timer is not None:
            self.move_timer.stop()
        if self._move_task is not None:
            self._move_task.cancel()
            self._move_task = None
        if self._move_future is not None:
            self._move_future.cancel()
            self._move_future = None
    
    async def _ramp_loop(self) -> None:
        """Asyncio-based velocity ramping loop for terminal mode."""
        ramp_interval_ms = self.config["ramp"]["ramp_update_interval_ms"]
        try:
            while True:
                self._update_velocity_ramp()
                await asyncio.sleep(ramp_interval_ms / 1000.0)
        except asyncio.CancelledError:
            pass
    
    async def _move_loop(self) -> None:
        """Asyncio-based movement command loop for terminal mode."""
        update_interval_ms = self.config["ramp"]["update_interval_ms"]
        try:
            while True:
                self._send_move_command()
                await asyncio.sleep(update_interval_ms / 1000.0)
        except asyncio.CancelledError:
            pass
    
    def _update_velocity_ramp(self) -> None:
        """Update current velocities based on ramping logic."""
        current_time = self._get_current_time_ms()
        ramp_time_ms = self.config["ramp"]["ramp_time_ms"]
        
        all_ramped = True
        for direction in ["forward", "strafe", "rotation"]:
            target = self.target_velocities[direction]
            current = self.current_velocities[direction]
            
            # If target is 0 and current is very small, snap to 0 immediately
            if target == 0.0 and abs(current) < 0.001:
                self.current_velocities[direction] = 0.0
                continue
            
            if abs(target - current) < 0.001:
                # Already at target
                self.current_velocities[direction] = target
                continue
            
            if ramp_time_ms <= 0:
                # No ramping, set immediately
                self.current_velocities[direction] = target
                continue
            
            # Calculate ramp progress
            start_time = self.ramp_start_times.get(direction)
            if start_time is None:
                start_time = current_time
                self.ramp_start_times[direction] = start_time
                self.ramp_start_velocities[direction] = current
            
            elapsed = current_time - start_time
            progress = min(elapsed / ramp_time_ms, 1.0)
            
            # Interpolate between start and target
            start_vel = self.ramp_start_velocities[direction]
            new_velocity = start_vel + (target - start_vel) * progress
            
            # If target is 0 and we're very close, snap to 0
            if target == 0.0 and abs(new_velocity) < 0.001:
                self.current_velocities[direction] = 0.0
            else:
                self.current_velocities[direction] = new_velocity
            
            if abs(target - self.current_velocities[direction]) >= 0.001:
                all_ramped = False
        
        # Stop ramp timer if all velocities are ramped
        if all_ramped:
            self._stop_ramp_timer()
        
        # Send command with current (ramped) velocities
        self._send_move_command()
    
    def _send_move_command(self) -> None:
        """Send movement command to client (thread-safe)."""
        if self.client is None:
            return
        
        try:
            # Get client event loop
            loop = getattr(self.client, "_loop", None)
            if loop is None:
                logger.warning("Client event loop not available; cannot send move command")
                return
            
            # Calculate rotation velocity (invert if moving backward)
            if self.current_velocities["forward"] >= 0:
                rotation_velocity = self.current_velocities["rotation"]
            else:
                rotation_velocity = -self.current_velocities["rotation"]
            
            # Check if we're in the same event loop (terminal mode) or different thread (Qt mode)
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop is loop:
                    # Same loop - schedule as task
                    asyncio.create_task(
                        self.client.move(
                            self.current_velocities["forward"],
                            self.current_velocities["strafe"],
                            rotation_velocity
                        )
                    )
                else:
                    # Different loop - use run_coroutine_threadsafe
                    asyncio.run_coroutine_threadsafe(
                        self.client.move(
                            self.current_velocities["forward"],
                            self.current_velocities["strafe"],
                            rotation_velocity
                        ),
                        loop
                    )
            except RuntimeError:
                # No running loop - use run_coroutine_threadsafe
                asyncio.run_coroutine_threadsafe(
                    self.client.move(
                        self.current_velocities["forward"],
                        self.current_velocities["strafe"],
                        rotation_velocity
                    ),
                    loop
                )
        except Exception as e:
            logger.warning(f"Failed to send move command: {e}")
    
    def stop_movement(self) -> None:
        """Stop all movement immediately using STOPMOVE command."""
        self.target_velocities = {"forward": 0.0, "strafe": 0.0, "rotation": 0.0}
        self.current_velocities = {"forward": 0.0, "strafe": 0.0, "rotation": 0.0}
        self.pressed_actions.clear()
        self._stop_move_timer()
        self._stop_ramp_timer()
        
        # Send STOPMOVE command
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send stop_move command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.stop_move(),
                    loop
                )
            except Exception as e:
                logger.warning(f"Failed to send stop_move command: {e}")
    
    def setup_qt_timers(self, ramp_timer: QTimer, move_timer: QTimer) -> None:
        """Setup Qt timers for GUI mode."""
        self.ramp_timer = ramp_timer
        self.move_timer = move_timer
        
        # Configure timers
        ramp_interval_ms = self.config["ramp"]["ramp_update_interval_ms"]
        update_interval_ms = self.config["ramp"]["update_interval_ms"]
        
        self.ramp_timer.setInterval(ramp_interval_ms)
        self.move_timer.setInterval(update_interval_ms)
        
        # Connect timer signals
        self.ramp_timer.timeout.connect(self._update_velocity_ramp)
        self.move_timer.timeout.connect(self._send_move_command)
    
    def create_qt_adapter(self) -> QtInputAdapter:
        """Create and return a Qt input adapter."""
        return QtInputAdapter(self)
    
    def create_terminal_adapter(self) -> TerminalInputAdapter:
        """Create and return a terminal input adapter."""
        return TerminalInputAdapter(self)
    
    def get_linear_velocity(self) -> float:
        """Get the configured linear velocity."""
        return self.config["velocity"]["linear"]
    
    def get_rotation_velocity(self) -> float:
        """Get the configured rotation velocity."""
        return self.config["velocity"]["rotation"]
    
    def get_ramp_time_ms(self) -> int:
        """Get the configured velocity ramp time in milliseconds."""
        return self.config["ramp"]["ramp_time_ms"]
    
    def get_update_interval_ms(self) -> int:
        """Get the configured movement update interval in milliseconds."""
        return self.config["ramp"]["update_interval_ms"]

