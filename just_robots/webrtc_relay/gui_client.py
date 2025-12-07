"""
PyQt5 GUI client for GO2 robot control with integrated video, lidar, and odometry display.
"""
import sys
import asyncio
import argparse
import logging
import threading
import typing as t
import numpy as np
import numpy.typing as npt

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QGridLayout, QGroupBox, QLabel, QLineEdit, QCheckBox,  # pyright: ignore[reportUnusedImport]
    QSplitter, QDoubleSpinBox, QSpinBox, QTextEdit, QTabWidget, QDialog
)
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QKeyEvent, QFocusEvent, QPixmap

from aiortc import MediaStreamTrack  # type: ignore

from go2_robot_sdk.domain.entities.robot_config import RobotConfig
from go2_robot_sdk.domain.entities.robot_data import RobotData
from go2_robot_sdk.webrtc_relay.webrtc_relay_client import WebRTCRelayClient
from go2_robot_sdk.webrtc_relay.firebase_auth import FirebaseAuthManager
from go2_robot_sdk.webrtc_relay.gui_widgets import (
    VideoWidget, LidarWidget, OdometryWidget
)
import json
import os
import requests
from pathlib import Path
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
from go2_robot_sdk.webrtc_relay.keyboard_command_handler import KeyboardCommandHandler, QtInputAdapter

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class LoginDialog(QDialog):
    """Simple login dialog for Firebase authentication."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GO2 Robot - Login")
        self.setModal(True)
        self.setFixedSize(400, 200)
        self.id_token = None
        
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("GO2 Robot Control")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # Email field
        email_layout = QHBoxLayout()
        email_label = QLabel("Email:")
        email_label.setFixedWidth(80)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Enter your email")
        email_layout.addWidget(email_label)
        email_layout.addWidget(self.email_input)
        layout.addLayout(email_layout)
        
        # Password field
        password_layout = QHBoxLayout()
        password_label = QLabel("Password:")
        password_label.setFixedWidth(80)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_input)
        layout.addLayout(password_layout)
        
        # Error message label
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red; font-size: 9pt;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.login_button = QPushButton("Login")
        self.login_button.setDefault(True)
        self.login_button.clicked.connect(self.authenticate)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.login_button)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        self.email_input.setFocus()
        self.password_input.returnPressed.connect(self.authenticate)
    
    def authenticate(self):
        """Authenticate with Firebase."""
        email = self.email_input.text().strip()
        password = self.password_input.text()
        
        if not email or not password:
            self.error_label.setText("Please enter both email and password")
            return
        
        # Disable button during authentication
        self.login_button.setEnabled(False)
        self.login_button.setText("Authenticating...")
        self.error_label.setText("")
        QApplication.processEvents()
        
        # Get Firebase API key
        firebase_api_key = os.getenv("FIREBASE_API_KEY")
        if not firebase_api_key:
            self.error_label.setText("FIREBASE_API_KEY not configured")
            self.login_button.setEnabled(True)
            self.login_button.setText("Login")
            return
        
        # Authenticate with Firebase
        try:
            response = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_api_key}",
                json={
                    "email": email,
                    "password": password,
                    "returnSecureToken": True
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.id_token = data.get("idToken")
                if self.id_token:
                    self.accept()
                else:
                    self.error_label.setText("Authentication failed: No token received")
                    self.login_button.setEnabled(True)
                    self.login_button.setText("Login")
            else:
                error_data = response.json()
                error_message = error_data.get("error", {}).get("message", "Authentication failed")
                if "INVALID_PASSWORD" in error_message or "INVALID_EMAIL" in error_message:
                    self.error_label.setText("Invalid email or password")
                else:
                    self.error_label.setText(f"Authentication failed: {error_message}")
                self.login_button.setEnabled(True)
                self.login_button.setText("Login")
        except requests.exceptions.Timeout:
            self.error_label.setText("Connection timeout. Please check your internet connection.")
            self.login_button.setEnabled(True)
            self.login_button.setText("Login")
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            self.error_label.setText(f"Error: {str(e)}")
            self.login_button.setEnabled(True)
            self.login_button.setText("Login")


class RobotControlSignals(QObject):
    """Qt signals for thread-safe updates from async callbacks."""
    video_frame_ready = Signal(np.ndarray)
    lidar_update = Signal(np.ndarray, int, float, tuple)
    odometry_update = Signal(dict, dict)
    connection_status = Signal(bool)
    status_message = Signal(str)
    console_message = Signal(str)


class ConsoleWidget(QWidget):
    """Console widget for displaying robot data messages."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_queue = deque(maxlen=25)
        self._pending_update = False
        self.init_ui()
    
    def init_ui(self):
        """Initialize the console UI."""
        layout = QVBoxLayout()
        
        # Console text area
        self.console_text = QTextEdit()
        self.console_text.setReadOnly(True)
        self.console_text.setStyleSheet("""
            QTextEdit {
                color: #00ff00;
                background-color: #1e1e1e;
                border: 1px solid #555;
                font-family: 'Courier New', monospace;
                font-size: 9pt;
            }
        """)
        layout.addWidget(self.console_text)
        
        # Copy button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.btn_copy = QPushButton("Copy All")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                padding: 5px 15px;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #777;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
        """)
        self.btn_copy.clicked.connect(self.copy_all)
        button_layout.addWidget(self.btn_copy)
        layout.addLayout(button_layout)
        
        # Timer for rate-limited GUI updates (every 2 seconds)
        self.update_timer = QTimer()
        self.update_timer.setInterval(2000)  # 2 seconds
        self.update_timer.timeout.connect(self._update_display)
        self.update_timer.setSingleShot(False)
        
        self.setLayout(layout)
    
    def add_message(self, message: str):
        """Add a message to the console with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        # Add to queue immediately (automatically limits to 25)
        self.message_queue.append(formatted_message)
        
        # Mark that we have pending updates
        self._pending_update = True
        
        # Start the timer if it's not already running
        if not self.update_timer.isActive():
            self.update_timer.start()
            # Also do an immediate update for the first message
            self._update_display()
    
    def _update_display(self):
        """Update the GUI display with current queue contents."""
        if not self._pending_update:
            return
        
        # Update text widget with all messages
        self.console_text.clear()
        self.console_text.setPlainText("\n".join(self.message_queue))
        
        # Auto-scroll to bottom to show latest message
        scrollbar = self.console_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        self._pending_update = False
    
    def copy_all(self):
        """Copy all console text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(self.message_queue))
        logger.info("Console output copied to clipboard")


class ControlPanel(QWidget):
    """Control panel with movement buttons and commands."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def _load_config_value(self, section: str, key: str, default: t.Any) -> t.Any:
        """Load a value from keyboard_config.json, with fallback to default."""
        try:
            config_path = Path(__file__).parent / "keyboard_config.json"
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    return config.get(section, {}).get(key, default)
        except Exception as e:
            logger.warning(f"Failed to load config value {section}.{key}: {e}")
        return default
    
    def _get_button_style(self) -> str:
        """Get the button style string."""
        return """
            QPushButton {
                background-color: #3a3a3a;
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                padding: 5px;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #777;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
        """
    
    def _create_movement_widget(self) -> QWidget:
        """Create movement controls widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        movement_group = QGroupBox("Movement Controls")
        movement_layout = QGridLayout()
        
        # Create movement buttons
        self.btn_forward = QPushButton("↑ Forward (W)")
        self.btn_backward = QPushButton("↓ Backward (S)")
        self.btn_strafe_left = QPushButton("← Strafe Left (Q)")
        self.btn_strafe_right = QPushButton("→ Strafe Right (E)")
        self.btn_rotate_left = QPushButton("⟲ Rotate Left (A)")
        self.btn_rotate_right = QPushButton("⟳ Rotate Right (D)")
        self.btn_stop = QPushButton("⏹ Stop (Space)")
        
        button_style = self._get_button_style()
        
        for btn in [self.btn_forward, self.btn_backward, self.btn_strafe_left, 
                   self.btn_strafe_right, self.btn_rotate_left, self.btn_rotate_right, self.btn_stop]:
            btn.setStyleSheet(button_style)
            btn.setMinimumHeight(35)
            btn.setMaximumHeight(45)
        
        self.btn_stop.setStyleSheet(button_style + """
            QPushButton { background-color: #8b0000; }
            QPushButton:hover { background-color: #a00000; }
        """)
        
        # Layout movement buttons in a grid
        movement_layout.addWidget(self.btn_forward, 0, 1)
        movement_layout.addWidget(self.btn_strafe_left, 1, 0)
        movement_layout.addWidget(self.btn_stop, 1, 1)
        movement_layout.addWidget(self.btn_strafe_right, 1, 2)
        movement_layout.addWidget(self.btn_backward, 2, 1)
        movement_layout.addWidget(self.btn_rotate_left, 3, 0)
        movement_layout.addWidget(self.btn_rotate_right, 3, 2)
        
        movement_group.setLayout(movement_layout)
        layout.addWidget(movement_group)
        layout.addStretch()
        
        # Apply dark theme to group box
        group_style = """
            QGroupBox {
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """
        movement_group.setStyleSheet(group_style)
        
        widget.setLayout(layout)
        return widget
    
    def _create_posture_fun_widget(self) -> QWidget:
        """Create posture and fun commands widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        button_style = self._get_button_style()
        
        # Posture controls
        posture_group = QGroupBox("Posture Controls")
        posture_layout = QVBoxLayout()
        
        self.btn_stand_up = QPushButton("Stand Up")
        self.btn_recovery_stand = QPushButton("Recovery Stand")
        self.btn_sit = QPushButton("Sit Down")
        self.btn_lie_down = QPushButton("Lie Down")
        
        for btn in [self.btn_stand_up, self.btn_recovery_stand, self.btn_sit, self.btn_lie_down]:
            btn.setStyleSheet(button_style)
            btn.setMinimumHeight(30)
            btn.setMaximumHeight(40)
            posture_layout.addWidget(btn)
        
        posture_group.setLayout(posture_layout)
        layout.addWidget(posture_group)
        
        # Fun commands
        fun_group = QGroupBox("Fun Commands")
        fun_layout = QVBoxLayout()
        
        self.btn_balance_stand = QPushButton("⚖️ Balance Stand")
        self.btn_balance_stand.setStyleSheet(button_style)
        self.btn_balance_stand.setMinimumHeight(30)
        self.btn_balance_stand.setMaximumHeight(40)
        fun_layout.addWidget(self.btn_balance_stand)
        
        fun_group.setLayout(fun_layout)
        layout.addWidget(fun_group)
        
        layout.addStretch()
        
        # Apply dark theme to group boxes
        group_style = """
            QGroupBox {
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """
        posture_group.setStyleSheet(group_style)
        fun_group.setStyleSheet(group_style)
        
        widget.setLayout(layout)
        return widget
    
    def _create_raw_commands_widget(self) -> QWidget:
        """Create raw JSON commands widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        button_style = self._get_button_style()
        
        json_group = QGroupBox("JSON Command")
        json_layout = QVBoxLayout()
        
        self.json_command_text = QTextEdit()
        self.json_command_text.setPlaceholderText('Enter JSON command (e.g., {"type": "msg", "topic": "rt/api/sport/request", "data": {...}})')
        self.json_command_text.setStyleSheet("QTextEdit { color: white; background-color: #3a3a3a; padding: 5px; border: 1px solid #555; }")
        json_layout.addWidget(self.json_command_text)
        
        self.btn_send_json = QPushButton("Send JSON Command")
        self.btn_send_json.setStyleSheet(button_style + """
            QPushButton { background-color: #007bff; }
            QPushButton:hover { background-color: #0056b3; }
        """)
        self.btn_send_json.setMinimumHeight(30)
        self.btn_send_json.setMaximumHeight(40)
        json_layout.addWidget(self.btn_send_json)
        
        json_group.setLayout(json_layout)
        layout.addWidget(json_group)
        layout.addStretch()
        
        # Apply dark theme to group box
        group_style = """
            QGroupBox {
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """
        json_group.setStyleSheet(group_style)
        
        widget.setLayout(layout)
        return widget
    
    def _create_settings_widget(self) -> QWidget:
        """Create settings widget."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        button_style = self._get_button_style()
        
        avoid_group = QGroupBox("Settings")
        avoid_layout = QVBoxLayout()
        
        # Obstacle avoidance buttons
        obstacle_avoid_layout = QHBoxLayout()
        obstacle_avoid_layout.addWidget(QLabel("Obstacle Avoidance:"))
        self.btn_enable_obstacle_avoid = QPushButton("Enable")
        self.btn_enable_obstacle_avoid.setStyleSheet(button_style + """
            QPushButton { background-color: #28a745; }
            QPushButton:hover { background-color: #34ce57; }
        """)
        self.btn_enable_obstacle_avoid.setMinimumHeight(30)
        self.btn_disable_obstacle_avoid = QPushButton("Disable")
        self.btn_disable_obstacle_avoid.setStyleSheet(button_style + """
            QPushButton { background-color: #dc3545; }
            QPushButton:hover { background-color: #e4606d; }
        """)
        self.btn_disable_obstacle_avoid.setMinimumHeight(30)
        obstacle_avoid_layout.addWidget(self.btn_enable_obstacle_avoid)
        obstacle_avoid_layout.addWidget(self.btn_disable_obstacle_avoid)
        avoid_layout.addLayout(obstacle_avoid_layout)
        
        # Linear velocity control (forward/backward/strafe)
        linear_velocity_layout = QHBoxLayout()
        linear_velocity_layout.addWidget(QLabel("Linear Velocity:"))
        self.linear_velocity_spinbox = QDoubleSpinBox()
        self.linear_velocity_spinbox.setRange(0.0, 1.0)
        self.linear_velocity_spinbox.setSingleStep(0.1)
        self.linear_velocity_spinbox.setValue(self._load_config_value("velocity", "linear", 0.25))
        self.linear_velocity_spinbox.setDecimals(2)
        self.linear_velocity_spinbox.setStyleSheet("QDoubleSpinBox { color: white; background-color: #3a3a3a; padding: 5px; }")
        linear_velocity_layout.addWidget(self.linear_velocity_spinbox)
        avoid_layout.addLayout(linear_velocity_layout)
        
        # Rotation velocity control
        rotation_velocity_layout = QHBoxLayout()
        rotation_velocity_layout.addWidget(QLabel("Rotation Velocity:"))
        self.rotation_velocity_spinbox = QDoubleSpinBox()
        self.rotation_velocity_spinbox.setRange(0.0, 1.0)
        self.rotation_velocity_spinbox.setSingleStep(0.1)
        self.rotation_velocity_spinbox.setValue(self._load_config_value("velocity", "rotation", 0.50))
        self.rotation_velocity_spinbox.setDecimals(2)
        self.rotation_velocity_spinbox.setStyleSheet("QDoubleSpinBox { color: white; background-color: #3a3a3a; padding: 5px; }")
        rotation_velocity_layout.addWidget(self.rotation_velocity_spinbox)
        avoid_layout.addLayout(rotation_velocity_layout)
        
        # Velocity ramp control
        ramp_layout = QHBoxLayout()
        ramp_layout.addWidget(QLabel("Ramp Time (ms):"))
        self.ramp_spinbox = QSpinBox()
        self.ramp_spinbox.setRange(0, 5000)
        self.ramp_spinbox.setSingleStep(100)
        self.ramp_spinbox.setValue(self._load_config_value("ramp", "ramp_time_ms", 1000))
        self.ramp_spinbox.setSuffix(" ms")
        self.ramp_spinbox.setStyleSheet("QSpinBox { color: white; background-color: #3a3a3a; padding: 5px; }")
        ramp_layout.addWidget(self.ramp_spinbox)
        avoid_layout.addLayout(ramp_layout)
        
        avoid_group.setLayout(avoid_layout)
        layout.addWidget(avoid_group)
        layout.addStretch()
        
        # Apply dark theme to group box
        group_style = """
            QGroupBox {
                color: white;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """
        avoid_group.setStyleSheet(group_style)
        
        widget.setLayout(layout)
        return widget
    
    def init_ui(self):
        """Initialize the control panel with tab widgets."""
        layout = QVBoxLayout()
        
        # Top tab widget for commands
        top_tabs = QTabWidget()
        top_tabs.addTab(self._create_movement_widget(), "Movement")
        top_tabs.addTab(self._create_posture_fun_widget(), "Posture/Fun")
        top_tabs.addTab(self._create_raw_commands_widget(), "Raw Commands")
        
        # Style the tab widget
        top_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background-color: #3a3a3a;
                color: white;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #555;
            }
            QTabBar::tab:hover {
                background-color: #4a4a4a;
            }
        """)
        
        layout.addWidget(top_tabs)
        self.setLayout(layout)


class GO2GuiClient(QMainWindow):
    """Main GUI window for GO2 robot control."""
    
    def __init__(self, relay_url: str):
        super().__init__()
        self.relay_url = relay_url
        # self.robot_config = robot_config
        # self.client: WebRTCRelayClient | None = None
        self.client_thread: threading.Thread | None = None  # Store client thread for cleanup
        self.signals = RobotControlSignals()
        self.video_track: MediaStreamTrack | None = None
        self.video_task: asyncio.Task[None] | None = None
        self.invoker = GuiInvoker.make_invoker_on_gui_thread()
        
        # Keyboard command handler (will be initialized in add_client)
        self.keyboard_handler: KeyboardCommandHandler | None = None
        self.keyboard_adapter: QtInputAdapter | None = None
        
        self.init_ui()
        self.connect_signals()
        
        # Timer for continuous movement updates (will be connected to handler)
        self.move_timer = QTimer()
        self.move_timer.setInterval(self._load_config_value("ramp", "update_interval_ms", 50))
        
        # Timer for velocity ramping (will be connected to handler)
        self.ramp_timer = QTimer()
        self.ramp_timer.setInterval(20)  # 50 Hz for smooth ramping
        
        # Set focus policy to receive keyboard events
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Buffer for latest lidar frame and low-rate update timer (0.2 Hz)
        self._latest_lidar_frame: t.Optional[dict[str, t.Any]] = None
        self._lidar_update_timer = QTimer()
        self._lidar_update_timer.timeout.connect(self._process_latest_lidar_frame)
        self._lidar_update_timer.setInterval(5000)  # 5000 ms = 0.2 Hz
        self._lidar_update_timer.start()
    
    def _load_config_value(self, section: str, key: str, default: t.Any) -> t.Any:
        """Load a value from keyboard_config.json, with fallback to default."""
        try:
            config_path = Path(__file__).parent / "keyboard_config.json"
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    return config.get(section, {}).get(key, default)
        except Exception as e:
            logger.warning(f"Failed to load config value {section}.{key}: {e}")
        return default
    
    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("GO2 Robot Control Center")
        self.setGeometry(100, 100, 1320, 900)
        self.setMinimumSize(800, 600)
        
        # Apply dark theme
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: white;
            }
        """)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout()
        
        # Header with logo at top left
        header_layout = QHBoxLayout()
        logo_path = Path(__file__).parent / "logo.png"
        if logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path))
            # Scale logo to reasonable size (e.g., max height 60px)
            scaled_pixmap = pixmap.scaled(200, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            header_layout.addWidget(logo_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)
        
        # Content area
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left panel: Video and Lidar in tabs
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        
        self.video_widget = VideoWidget()
        # Use VTK for embedded 3D visualization (fallback to Open3D button if VTK not available)
        self.lidar_widget = LidarWidget()
        
        # Create tab widget for video and lidar
        video_lidar_tabs = QTabWidget()
        video_lidar_tabs.addTab(self.video_widget, "Video")
        video_lidar_tabs.addTab(self.lidar_widget, "Lidar")
        video_lidar_tabs.setCurrentIndex(0)  # Set video as default tab
        
        # Style the tab widget
        video_lidar_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background-color: #3a3a3a;
                color: white;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #555;
            }
            QTabBar::tab:hover {
                background-color: #4a4a4a;
            }
        """)
        
        left_layout.addWidget(video_lidar_tabs)
        left_panel.setLayout(left_layout)
        
        # Right panel: Controls and Odometry with tabs
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        
        self.control_panel = ControlPanel()
        
        # Create settings widget (need to access it from control_panel)
        settings_widget = self.control_panel._create_settings_widget()
        self.odometry_widget = OdometryWidget()
        self.console_widget = ConsoleWidget()
        
        # Bottom tab widget for Settings, Odometry, and Console
        bottom_tabs = QTabWidget()
        bottom_tabs.addTab(settings_widget, "Settings")
        bottom_tabs.addTab(self.odometry_widget, "Odometry")
        bottom_tabs.addTab(self.console_widget, "Console")
        
        # Style the bottom tab widget
        bottom_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background-color: #3a3a3a;
                color: white;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #555;
            }
            QTabBar::tab:hover {
                background-color: #4a4a4a;
            }
        """)
        
        right_layout.addWidget(self.control_panel, stretch=2)
        right_layout.addWidget(bottom_tabs, stretch=1)
        right_panel.setLayout(right_layout)
        
        # Add panels to splitter
        content_splitter.addWidget(left_panel)
        content_splitter.addWidget(right_panel)
        content_splitter.setStretchFactor(0, 3)
        content_splitter.setStretchFactor(1, 1)
        
        main_layout.addWidget(content_splitter)
        
        central_widget.setLayout(main_layout)

    def add_client(self, client: WebRTCRelayClient, client_thread: threading.Thread | None = None):
        """Add WebRTCRelayClient to the GUI client."""
        self.client = client
        self.client_thread = client_thread  # Store thread reference for cleanup
        
        # Initialize keyboard command handler
        self.keyboard_handler = KeyboardCommandHandler(client)
        self.keyboard_adapter = self.keyboard_handler.create_qt_adapter()
        
        # Setup Qt timers with handler
        self.keyboard_handler.setup_qt_timers(self.ramp_timer, self.move_timer)
    
    def connect_signals(self):
        """Connect Qt signals to slots."""
        # Control buttons (use appropriate velocity multiplier from spinbox)
        self.control_panel.btn_forward.pressed.connect(
            lambda: self.set_velocity("forward", self.control_panel.linear_velocity_spinbox.value()))
        self.control_panel.btn_forward.released.connect(lambda: self.set_velocity("forward", 0.0))
        
        self.control_panel.btn_backward.pressed.connect(
            lambda: self.set_velocity("forward", -self.control_panel.linear_velocity_spinbox.value()))
        self.control_panel.btn_backward.released.connect(lambda: self.set_velocity("forward", 0.0))
        
        self.control_panel.btn_strafe_left.pressed.connect(
            lambda: self.set_velocity("strafe", self.control_panel.linear_velocity_spinbox.value()))
        self.control_panel.btn_strafe_left.released.connect(lambda: self.set_velocity("strafe", 0.0))
        
        self.control_panel.btn_strafe_right.pressed.connect(
            lambda: self.set_velocity("strafe", -self.control_panel.linear_velocity_spinbox.value()))
        self.control_panel.btn_strafe_right.released.connect(lambda: self.set_velocity("strafe", 0.0))
        
        self.control_panel.btn_rotate_left.pressed.connect(
            lambda: self.set_velocity("rotation", self.control_panel.rotation_velocity_spinbox.value()))
        self.control_panel.btn_rotate_left.released.connect(lambda: self.set_velocity("rotation", 0.0))
        
        self.control_panel.btn_rotate_right.pressed.connect(
            lambda: self.set_velocity("rotation", -self.control_panel.rotation_velocity_spinbox.value()))
        self.control_panel.btn_rotate_right.released.connect(lambda: self.set_velocity("rotation", 0.0))
        
        self.control_panel.btn_stop.clicked.connect(self.stop_movement)
        
        # Posture buttons
        self.control_panel.btn_stand_up.clicked.connect(self.on_stand_up)
        self.control_panel.btn_recovery_stand.clicked.connect(self.on_recovery_stand)
        self.control_panel.btn_sit.clicked.connect(self.on_sit)
        self.control_panel.btn_lie_down.clicked.connect(self.on_lie_down)
        
        # Fun commands
        self.control_panel.btn_balance_stand.clicked.connect(self.on_balance_stand)
        
        # Obstacle avoidance buttons
        self.control_panel.btn_enable_obstacle_avoid.clicked.connect(self.on_enable_obstacle_avoid)
        self.control_panel.btn_disable_obstacle_avoid.clicked.connect(self.on_disable_obstacle_avoid)
        
        # JSON command button
        self.control_panel.btn_send_json.clicked.connect(self.on_send_json_command)
        
        # Data update signals
        self.signals.video_frame_ready.connect(self.video_widget.update_frame)
        self.signals.lidar_update.connect(self.on_lidar_update)
        self.signals.odometry_update.connect(self.on_odometry_update)
        self.signals.console_message.connect(self.console_widget.add_message)
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle keyboard input using keyboard command handler."""
        if event.isAutoRepeat():
            return
        
        if self.keyboard_adapter:
            self.keyboard_adapter.handle_key_press(event.key())
        
        # Handle quit action separately (close window)
        if self.keyboard_handler:
            action = self.keyboard_handler._get_action_for_qt_key(event.key())
            if action == "quit":
                self.close()
    
    def keyReleaseEvent(self, event: QKeyEvent):
        """Handle keyboard release using keyboard command handler."""
        if event.isAutoRepeat():
            return
        
        if self.keyboard_adapter:
            self.keyboard_adapter.handle_key_release(event.key())
    
    def set_velocity(self, direction: str, value: float):
        """Set velocity directly (used by button controls)."""
        if self.keyboard_handler:
            self.keyboard_handler.set_target_velocity(direction, value)
    
    def stop_movement(self):
        """Stop all movement immediately using STOPMOVE command."""
        if self.keyboard_handler:
            self.keyboard_handler.stop_movement()
    
    def focusOutEvent(self, event: QFocusEvent):
        """Handle window focus loss - stop all movement for safety."""
        self.stop_movement()
        super().focusOutEvent(event)

    def on_connect(self):
        pass
    #     """Connect to the robot."""
    #     try:
    #         self.signals.status_message.emit("Connecting...")
    #         self.btn_connect.setEnabled(False)
            
    #         self.client = WebRTCRelayClient(
    #             relay_url=self.relay_url,
    #             robot_config=self.robot_config,
    #             on_robot_data=self.handle_robot_data,
    #             on_video_track=self.handle_video_track,
    #             on_lidar_frame=self.handle_lidar_frame
    #         )
            
    #         await self.client.start(connect_go2=True)
            
    #         self.signals.connection_status.emit(True)
    #         self.signals.status_message.emit("Connected to robot")
    #         self.btn_disconnect.setEnabled(True)
            
    #     except Exception as e:
    #         logger.error(f"Connection failed: {e}")
    #         self.signals.status_message.emit(f"Connection failed: {e}")
    #         self.btn_connect.setEnabled(True)
    #         QMessageBox.critical(self, "Connection Error", f"Failed to connect: {e}")
    
    def on_disconnect(self):
        """Disconnect from the robot."""
        try:
            if self.video_task:
                self.video_task.cancel()
                self.video_task = None
            
            # if self.client:
            #     await self.client.shutdown()
            #     self.client = None
            
            self.signals.connection_status.emit(False)
            self.signals.status_message.emit("Disconnected")
            
        except Exception as e:
            logger.error(f"Disconnect failed: {e}")
    
    def on_stand_up(self):
        """Command robot to stand up."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send stand_up command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.stand_up(),
                    loop
                )
                logger.info("Stand up command sent")
            except Exception as e:
                logger.warning(f"Failed to send stand_up command: {e}")
    
    def on_recovery_stand(self):
        """Command robot to recovery stand."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send recovery_stand command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.recovery_stand(),
                    loop
                )
                logger.info("Recovery stand command sent")
            except Exception as e:
                logger.warning(f"Failed to send recovery_stand command: {e}")
    
    def on_sit(self):
        """Command robot to sit."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send sit command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.sit_on_hind_legs(),
                    loop
                )
                logger.info("Sit command sent")
            except Exception as e:
                logger.warning(f"Failed to send sit command: {e}")
    
    def on_lie_down(self):
        """Command robot to lie down."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send lie_down command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.lie_down_on_belly(),
                    loop
                )
                logger.info("Lie down command sent")
            except Exception as e:
                logger.warning(f"Failed to send lie_down command: {e}")
    
    def on_balance_stand(self):
        """Command robot to perform balance stand."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send balance_stand command")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.balance_stand(),
                    loop
                )
            except Exception as e:
                logger.warning(f"Failed to send balance_stand command: {e}")
    
    def on_enable_obstacle_avoid(self):
        """Enable obstacle avoidance."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot enable obstacle avoidance")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.change_obstacle_avoid_state(True),
                    loop
                )
                logger.info("Enabled obstacle avoidance")
            except Exception as e:
                logger.warning(f"Failed to enable obstacle avoidance: {e}")
    
    def on_disable_obstacle_avoid(self):
        """Disable obstacle avoidance."""
        if self.client:
            try:
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot disable obstacle avoidance")
                    return
                
                asyncio.run_coroutine_threadsafe(
                    self.client.change_obstacle_avoid_state(False),
                    loop
                )
                logger.info("Disabled obstacle avoidance")
            except Exception as e:
                logger.warning(f"Failed to disable obstacle avoidance: {e}")
    
    def on_send_json_command(self):
        """Send JSON command to robot."""
        if self.client:
            try:
                # Get JSON command from text box
                json_text = self.control_panel.json_command_text.toPlainText().strip()
                if not json_text:
                    logger.warning("JSON command is empty")
                    self.signals.status_message.emit("JSON command is empty")
                    return
                
                # Validate JSON format
                try:
                    json.loads(json_text)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON format: {e}")
                    self.signals.status_message.emit(f"Invalid JSON format: {e}")
                    return
                
                loop = getattr(self.client, "_loop", None)
                if loop is None:
                    logger.warning("Client event loop not available; cannot send JSON command")
                    self.signals.status_message.emit("Client not connected")
                    return
                
                # Send command using threadsafe coroutine
                asyncio.run_coroutine_threadsafe(
                    self.client.send_json_command(json_text),
                    loop
                )
                logger.info("JSON command sent successfully")
                self.signals.status_message.emit("JSON command sent successfully")
            except Exception as e:
                logger.warning(f"Failed to send JSON command: {e}")
                self.signals.status_message.emit(f"Failed to send JSON command: {e}")
    
    def handle_robot_data(self, robot_data: RobotData):
        """Handle robot data updates."""
        try:
            if not robot_data:
                return
            
            # Handle odometry data
            if robot_data.odometry_data:
                self.signals.odometry_update.emit(
                    robot_data.odometry_data.position,
                    robot_data.odometry_data.orientation
                )
            
            # Handle other robot data fields (non-odometry)
            # Get all attributes of robot_data that are not private/internal
            robot_data_attrs = [attr for attr in dir(robot_data) 
                              if not attr.startswith('_') and attr != 'odometry_data']
            
            for attr_name in robot_data_attrs:
                try:
                    attr_value = getattr(robot_data, attr_name, None)
                    if attr_value is not None:
                        # Format the data for display
                        try:
                            # Try to convert to JSON if it's a complex object
                            if isinstance(attr_value, (dict, list)):
                                formatted_data = json.dumps(attr_value, indent=2, default=str)
                            else:
                                formatted_data = str(attr_value)
                            
                            # Create message with field name and data
                            message = f"{attr_name}: {formatted_data}"
                            self.signals.console_message.emit(message)
                        except Exception as e:
                            # Fallback to simple string representation
                            message = f"{attr_name}: {str(attr_value)}"
                            self.signals.console_message.emit(message)
                except Exception as e:
                    logger.debug(f"Failed to process attribute {attr_name}: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to handle robot data: {e}")
    
    def handle_video_track(self, frame):
        """Handle new video track."""
        # logger.info(f"Received video track")

        try:
            img = frame.to_ndarray(format="bgr24")  # pyright: ignore[reportAttributeAccessIssue]
            self.signals.video_frame_ready.emit(img)
        except asyncio.CancelledError:
            logger.info("Video stream processing cancelled")
        except Exception as e:
            logger.warning(f"Video stream error: {e}")
    
    def handle_lidar_frame(self, lidar_frame: dict[str, t.Any]):
        """Handle lidar data updates."""
        try:
            dec = lidar_frame["decoded_data"]
            meta = lidar_frame["data"]
            
            positions = dec["positions"]
            face_count = int(dec["face_count"])
            resolution = float(meta["resolution"])
            origin = tuple(meta["origin"])
            
            self.signals.lidar_update.emit(positions, face_count, resolution, origin)
        except Exception as e:
            logger.warning(f"Failed to handle lidar frame: {e}")
    
    def on_lidar_update(self, positions: npt.NDArray[np.uint8], face_count: int, resolution: float, origin: tuple[float, float, float]):
        """Update lidar widget."""
        self.lidar_widget.update_lidar_data(positions, face_count, resolution, origin)
    
    def update_latest_lidar_frame(self, lidar_frame: dict[str, t.Any]) -> None:
        """Store the most recent lidar frame (called on GUI thread)."""
        self._latest_lidar_frame = lidar_frame

    def _process_latest_lidar_frame(self) -> None:
        """Called by QTimer at 0.2 Hz to process the latest lidar frame if any."""
        if self._latest_lidar_frame is None:
            return
        try:
            # reuse existing handler to decode & emit signals
            self.handle_lidar_frame(self._latest_lidar_frame)
        except Exception as e:
            logger.warning(f"Failed to process latest lidar frame: {e}")
        finally:
            # clear buffer so we only process new incoming frames next tick
            self._latest_lidar_frame = None

    def on_odometry_update(self, position: dict[str, float], orientation: dict[str, float]):
        """Update odometry widget and lidar robot pose."""
        self.odometry_widget.update_odometry(position, orientation)
        self.lidar_widget.update_robot_pose(position, orientation)
    
    def closeEvent(self, event):
        """Handle window close event - disconnect from relay server before closing."""
        logger.info("GUI window closing, initiating cleanup...")
        
        # Stop movement timer
        if self.move_timer.isActive():
            self.move_timer.stop()
        
        # Cancel video task
        if self.video_task and not self.video_task.done():
            self.video_task.cancel()
        
        # Close VoxelMapViewer if active
        if hasattr(self.lidar_widget, '_viewer_started') and self.lidar_widget._viewer_started:  # pyright: ignore[reportPrivateUsage]
            try:
                self.lidar_widget.stop_voxel_viewer()
            except Exception as e:
                logger.warning(f"Error closing VoxelMapViewer: {e}")
        
        # Disconnect client if connected
        if self.client:
            try:
                # Get the event loop from the client (stored by client_main)
                loop = getattr(self.client, "_loop", None)
                if loop is not None:
                    logger.info("Disconnecting from relay server...")
                    # Schedule shutdown on the client's event loop
                    future = asyncio.run_coroutine_threadsafe(
                        self.client.shutdown(),
                        loop
                    )
                    # Wait up to 3 seconds for shutdown to complete
                    try:
                        future.result(timeout=3.0)
                        logger.info("Successfully disconnected from relay server")
                    except Exception as e:
                        logger.warning(f"Error during client shutdown: {e}")
                else:
                    logger.warning("Client event loop not available, cannot disconnect cleanly")
            except Exception as e:
                logger.warning(f"Error disconnecting client: {e}")
            finally:
                self.client = None
        
        # Wait for client thread to finish (with timeout)
        if self.client_thread and self.client_thread.is_alive():
            logger.info("Waiting for client thread to finish...")
            self.client_thread.join(timeout=5.0)  # Wait up to 5 seconds
            if self.client_thread.is_alive():
                logger.warning("Client thread did not finish within timeout, continuing anyway")
            else:
                logger.info("Client thread finished successfully")
        
        event.accept()

class GuiInvoker(QtCore.QObject):
    """Helper to run functions that originate from threads back onto main GUI thread"""

    call = QtCore.Signal(object)  # emits a Python callable

    @QtCore.Slot(object)  # type: ignore[reportCallIssue]
    def _run(self, fn: t.Callable[[], None]) -> None:
        fn()

    @classmethod
    def make_invoker_on_gui_thread(cls) -> 'GuiInvoker':
        """factory function for making this class"""
        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("No QApplication running")
        inv = GuiInvoker()
        inv.moveToThread(app.thread())  # ensure GUI-thread affinity
        inv.call.connect(  # type: ignore[reportAttributeAccessIssue]
            inv._run, QtCore.Qt.ConnectionType.QueuedConnection
        )
        return inv
    
async def client_main(api, config, client, on_robot_data, on_video_track, on_lidar_frame):
    """Main client logic."""
    try:
        client._loop = asyncio.get_running_loop()
        await client.start(connect_go2=True)
        # Keep running until shutdown is requested
        try:
            while not client._shutdown_requested:
                await asyncio.sleep(0.5)  # Check more frequently for shutdown
            logger.info("Shutdown requested, exiting client main loop...")
        except asyncio.CancelledError:
            logger.info("Client main loop cancelled, shutting down...")
        except Exception as e:
            logger.error(f"Error in client main loop: {e}")
    except Exception as e:
        logger.error(f"Error in client main: {e}")
    finally:
        # Always attempt to shutdown cleanly (shutdown() is safe to call multiple times)
        try:
            if not client._shutdown_requested:
                # Set flag and shutdown if not already done
                client._shutdown_requested = True
                await client.shutdown()
        except Exception as e:
            logger.warning(f"Error during client shutdown: {e}")
        logger.info("Client main thread exiting")

def main():
    """Main entry point."""
    import os
    parser = argparse.ArgumentParser(description="PyQt5 GUI client for GO2 robot")
    parser.add_argument("--api", default="http://localhost:8000", help="WebRTC relay server URL")
    parser.add_argument("--robot-ip", default="192.168.12.1", help="GO2 robot IP address")
    parser.add_argument("--token", default="", help="Robot authentication token")
    parser.add_argument("--firebase-id-token", default=None, help="Firebase ID token for authentication (or set FIREBASE_ID_TOKEN env var)")
    parser.add_argument("--firebase-config", default=None, help="Path to Firebase service account JSON file")
    parser.add_argument("--firebase-api-key", default=None, help="Firebase API key for user authentication")
    parser.add_argument("--firebase-email", default=None, help="Firebase email for user authentication")
    parser.add_argument("--firebase-password", default=None, help="Firebase password for user authentication")
    args = parser.parse_args()
    
    # Create Qt application
    app = QApplication(sys.argv)
    
    # Show login dialog first
    login_dialog = LoginDialog()
    if login_dialog.exec() != QDialog.DialogCode.Accepted:
        logger.info("Login cancelled or failed - exiting")
        sys.exit(0)
    
    # Get ID token from login
    id_token = login_dialog.id_token
    if not id_token:
        logger.error("No ID token received from login")
        sys.exit(1)
    
    # Create Firebase auth manager with the token
    firebase_auth_manager = FirebaseAuthManager(firebase_id_token=id_token)
    logger.info("Firebase authentication successful")
    window = GO2GuiClient(relay_url=args.api)
    invoker = GuiInvoker.make_invoker_on_gui_thread()

    async def on_robot_data(robot_data: RobotData):
        def _helper():
            window.handle_robot_data(robot_data)
        invoker.call.emit(_helper)

    # Create robot config
    config = RobotConfig(
        robot_ip_list=[args.robot_ip],
        token=args.token,
        conn_type="webrtc",
        enable_video=True,
        decode_lidar=True,
        publish_raw_voxel=True,
        obstacle_avoidance=True,
        conn_mode='single'
    )

    video_task = None
    async def on_video_track(track: MediaStreamTrack):
        nonlocal video_task
        def _helper(frame):
            window.handle_video_track(frame)

        async def _video_task_runner():
            while True:
                frame = await track.recv()
                invoker.call.emit(lambda: _helper(frame))
    
        video_task = asyncio.create_task(_video_task_runner())

    # lidar_task = None
    async def on_lidar_frame(lidar_frame: dict[str, t.Any]):
        # nonlocal lidar_task
        # def _helper():
        #     window.update_latest_lidar_frame(lidar_frame)

        # async def _lidar_task_runner():
        #     while True:
        #         invoker.call.emit(_helper)

        # lidar_task = asyncio.create_task(_lidar_task_runner())
        def _helper():
            window.update_latest_lidar_frame(lidar_frame)
        invoker.call.emit(_helper)
        # pass

    client = WebRTCRelayClient(
        relay_url=args.api,
        robot_config=config,
        on_robot_data=on_robot_data,
        on_video_track=on_video_track,
        on_lidar_frame=on_lidar_frame,
        firebase_auth_manager=firebase_auth_manager,
    )

    def client_thread():
        try:
            asyncio.run(
                client_main(
                    api=args.api, 
                    config=config, 
                    client=client,
                    on_robot_data=on_robot_data, 
                    on_video_track=on_video_track, 
                    on_lidar_frame=on_lidar_frame
                )
            )
        except Exception as e:
            logger.error(f"Connection failed: {e}")

    client_thread_handle = threading.Thread(target=client_thread, daemon=False)
    client_thread_handle.start()
    
    # Pass client and thread to window for proper cleanup
    window.add_client(client, client_thread_handle)
        

    # Enable Ctrl+C handling
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    # loop = QEventLoop(app)
    # asyncio.set_event_loop(loop)
    
    # Create and show main window
    window.show()
    
    # Run event loop
    try:
        sys.exit(app.exec_())
        # with loop:
            # loop.run_forever()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, shutting down...")
    finally:
        if video_task is not None:
            video_task.cancel()
        # Ensure cleanup
        # if window.client:
        #     try:
        #         loop.run_until_complete(window.client.shutdown())
        #     except:
        #         pass
        # loop.close()


if __name__ == "__main__":
    main()
