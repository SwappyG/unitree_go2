"""
PyQt5 widgets for displaying robot data in the GUI.
"""
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGridLayout, QGroupBox, QPushButton
from PySide6.QtCore import Qt, Signal, QTimer  # pyright: ignore[reportUnusedImport]
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QFont  # pyright: ignore[reportUnusedImport]
import numpy as np
import numpy.typing as npt
import cv2
import typing as t  # pyright: ignore[reportUnusedImport]
import logging

try:
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
    import vtkmodules.vtkRenderingOpenGL2  # pyright: ignore[reportUnusedImport]
    from vtkmodules.vtkCommonCore import vtkPoints, vtkUnsignedCharArray  # pyright: ignore[reportUnusedImport]
    from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
    from vtkmodules.vtkRenderingCore import (
        vtkActor, vtkPolyDataMapper, vtkRenderer, vtkRenderWindow,  # pyright: ignore[reportUnusedImport]
        vtkProperty, vtkCamera  # pyright: ignore[reportUnusedImport]
    )
    from vtkmodules.vtkFiltersCore import vtkTriangleFilter  # pyright: ignore[reportUnusedImport]
    from vtkmodules.vtkCommonTransforms import vtkTransform
    from vtkmodules.vtkFiltersSources import vtkCubeSource
    from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
    VTK_AVAILABLE = True
except ImportError:
    VTK_AVAILABLE = False  # pyright: ignore[reportConstantRedefinition]
    print("Warning: VTK not available. Install with: pip install vtk")

from go2_robot_sdk.webrtc_relay.voxel_map_viewer import VoxelMapViewer

logger = logging.getLogger(__name__)


if VTK_AVAILABLE:
    # TODO: this should be moved to a different file so type hinting is cleaner, but not urgent
    class CustomInteractorStyle(vtkInteractorStyleTrackballCamera):
        """Custom VTK interactor style for pan and rotate controls.
        
        - Left click + drag: Pan the view
        - Ctrl + left click + drag: Rotate around Z-axis (vertical axis)
        - Mouse wheel: Zoom in/out
        - Right click + drag: Rotate (alternative)
        """
        
        def __init__(self, parent=None):
            super().__init__()
            self.AddObserver("LeftButtonPressEvent", self.left_button_press)
            self.AddObserver("LeftButtonReleaseEvent", self.left_button_release)
            self.AddObserver("MouseMoveEvent", self.mouse_move)
            self.is_ctrl_pressed = False
            self.last_mouse_x = 0
            self.last_mouse_y = 0
        
        def left_button_press(self, obj, event):
            """Handle left button press."""
            # Check if Ctrl key is pressed
            interactor = self.GetInteractor()
            self.is_ctrl_pressed = interactor.GetControlKey()
            
            # Store mouse position
            self.last_mouse_x, self.last_mouse_y = interactor.GetEventPosition()
            
            if self.is_ctrl_pressed:
                # Ctrl + click = rotate around Z-axis
                self.StartRotate()
            else:
                # Click alone = pan
                self.StartPan()
        
        def left_button_release(self, obj, event):
            """Handle left button release."""
            if self.is_ctrl_pressed:
                self.EndRotate()
            else:
                self.EndPan()
            self.is_ctrl_pressed = False
        
        def mouse_move(self, obj, event):
            """Handle mouse move."""
            interactor = self.GetInteractor()
            
            if self.is_ctrl_pressed and self.GetState() == 2:  # VTKIS_ROTATE
                # Get current mouse position
                x, y = interactor.GetEventPosition()
                
                # Calculate horizontal movement (for Z-axis rotation)
                dx = x - self.last_mouse_x
                
                # Get camera and focal point
                camera = self.GetCurrentRenderer().GetActiveCamera()
                focal_point = camera.GetFocalPoint()
                
                # Rotate around Z-axis at focal point
                # Positive dx = rotate counter-clockwise, negative = clockwise
                angle = dx * 0.5  # Sensitivity factor
                
                # Get camera position relative to focal point
                cam_pos = camera.GetPosition()
                rel_x = cam_pos[0] - focal_point[0]
                rel_y = cam_pos[1] - focal_point[1]
                rel_z = cam_pos[2] - focal_point[2]
                
                # Rotate around Z-axis
                import math
                angle_rad = math.radians(angle)
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)
                
                new_x = rel_x * cos_a - rel_y * sin_a
                new_y = rel_x * sin_a + rel_y * cos_a
                
                # Set new camera position
                camera.SetPosition(
                    focal_point[0] + new_x,
                    focal_point[1] + new_y,
                    focal_point[2] + rel_z
                )
                
                # Update view up vector to maintain Z as up
                camera.SetViewUp(0, 0, 1)
                
                # Store current position for next move
                self.last_mouse_x = x
                self.last_mouse_y = y
                
                # Trigger render
                interactor.Render()
            else:
                # Normal mouse move behavior
                self.OnMouseMove()
                # Update last position
                self.last_mouse_x, self.last_mouse_y = interactor.GetEventPosition()



class VideoWidget(QWidget):
    """Widget for displaying video stream from the robot."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.label = QLabel("Waiting for video...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumSize(100, 100)
        self.label.setStyleSheet("QLabel { background-color: #1e1e1e; color: white; }")
        
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)

        logger.info(f"Label size: {self.label.size()}")
    
    def update_frame(self, frame: npt.NDArray[np.uint8]):
        """Update the video display with a new frame."""
        try:
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w
            qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            
            # # Scale to fit the label while maintaining aspect ratio
            # scaled_pixmap = QPixmap.fromImage(qt_image).scaled(
            #     self.label.size(), 
            #     Qt.AspectRatioMode.KeepAspectRatio, 
            #     Qt.TransformationMode.SmoothTransformation
            # )

            # Get label size
            label_size = self.label.size()
            label_width = label_size.width()
            label_height = label_size.height()

            # Skip if label has invalid size
            if label_width <= 0 or label_height <= 0:
                return

            # Calculate scale factor based on height to preserve full image height
            height_scale = label_height / h
            scaled_width = int(w * height_scale)
            scaled_height = label_height

            # Scale image to match calculated dimensions (full height, proportional width)
            scaled_pixmap = QPixmap.fromImage(qt_image).scaled(
                scaled_width,
                scaled_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,  # We've already calculated correct aspect ratio
                Qt.TransformationMode.SmoothTransformation
            )

            # Create a black background pixmap matching the label size
            final_pixmap = QPixmap(label_width, label_height)
            final_pixmap.fill(QColor(0, 0, 0))  # Black background

            # Calculate centered position (horizontal centering, vertical is already full height)
            scaled_width_actual = scaled_pixmap.width()
            scaled_height_actual = scaled_pixmap.height()
            x_offset = (label_width - scaled_width_actual) // 2
            y_offset = (label_height - scaled_height_actual) // 2

            # Draw the scaled image centered on the black background
            painter = QPainter(final_pixmap)
            painter.drawPixmap(x_offset, y_offset, scaled_pixmap)
            painter.end()

            self.label.setPixmap(final_pixmap)
        except Exception as e:
            logger.warning(f"Failed to update video frame: {e}")


def open3d_mesh_to_vtk_polydata(mesh: 'o3d.geometry.TriangleMesh') -> 'vtkPolyData':
    """
    Convert Open3D TriangleMesh to VTK PolyData.
    
    Args:
        mesh: Open3D TriangleMesh object
        
    Returns:
        VTK PolyData object
    """
    if not VTK_AVAILABLE:
        raise RuntimeError("VTK not available")
    if not OPEN3D_AVAILABLE:
        raise RuntimeError("Open3D not available")
    
    # Get vertices and triangles from Open3D mesh
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    
    # Create VTK points
    vtk_points = vtkPoints()
    for vertex in vertices:
        vtk_points.InsertNextPoint(float(vertex[0]), float(vertex[1]), float(vertex[2]))
    
    # Create VTK cells (triangles)
    vtk_cells = vtkCellArray()
    for triangle in triangles:
        vtk_cells.InsertNextCell(3)
        vtk_cells.InsertCellPoint(int(triangle[0]))
        vtk_cells.InsertCellPoint(int(triangle[1]))
        vtk_cells.InsertCellPoint(int(triangle[2]))
    
    # Create PolyData
    polydata = vtkPolyData()
    polydata.SetPoints(vtk_points)
    polydata.SetPolys(vtk_cells)
    
    # Add vertex colors if available
    if mesh.has_vertex_colors():
        colors = np.asarray(mesh.vertex_colors)
        vtk_colors = vtkUnsignedCharArray()
        vtk_colors.SetNumberOfComponents(3)
        vtk_colors.SetName("Colors")
        for color in colors:
            vtk_colors.InsertNextTuple3(
                int(color[0] * 255),
                int(color[1] * 255),
                int(color[2] * 255)
            )
        polydata.GetPointData().SetScalars(vtk_colors)
    
    # Add normals if available
    if mesh.has_vertex_normals():
        normals = np.asarray(mesh.vertex_normals)
        from vtkmodules.vtkCommonCore import vtkFloatArray
        vtk_normals = vtkFloatArray()
        vtk_normals.SetNumberOfComponents(3)
        vtk_normals.SetName("Normals")
        for normal in normals:
            vtk_normals.InsertNextTuple3(float(normal[0]), float(normal[1]), float(normal[2]))
        polydata.GetPointData().SetNormals(vtk_normals)
    
    return polydata


class LidarWidget(QWidget):
    """Widget for displaying lidar voxel/mesh data using VTK with Open3D mesh support."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setStyleSheet("QWidget { background-color: #2d2d2d; }")
        
        if not VTK_AVAILABLE:
            logger.error("VTK not available - LidarWidget requires VTK")
            layout = QVBoxLayout()
            error_label = QLabel("VTK not available. Install with: pip install vtk")
            error_label.setStyleSheet("QLabel { color: red; padding: 20px; }")
            layout.addWidget(error_label)
            self.setLayout(layout)
            return
        
        # Data storage
        self.positions: npt.NDArray[np.uint8] | None = None
        self.face_count = 0
        self.resolution = 0.0
        self.origin = (0.0, 0.0, 0.0)
        
        # Robot pose
        self.robot_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.robot_orient = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
        
        # Layout
        layout = QVBoxLayout()
        self.info_label = QLabel("Lidar: Waiting for data... | Controls: Drag=Pan, Ctrl+Drag=Rotate Z-axis, Wheel=Zoom")
        self.info_label.setStyleSheet("QLabel { color: white; padding: 5px; background-color: #1e1e1e; }")
        layout.addWidget(self.info_label)
        
        # Initialize VTK widget
        self._init_vtk_widget()
        layout.addWidget(self.vtk_widget)
        
        self.setLayout(layout)
    
    def _init_vtk_widget(self):
        """Initialize VTK visualization widget for voxel/mesh display."""
        if not VTK_AVAILABLE:
            return
        
        # Create VTK widget
        self.vtk_widget = QVTKRenderWindowInteractor(self)
        self.vtk_widget.setMinimumSize(400, 400)
        
        # Create renderer
        self.vtk_renderer = vtkRenderer()
        
        # Dark gradient background
        self.vtk_renderer.SetBackground(0.1, 0.1, 0.15)
        self.vtk_renderer.SetBackground2(0.05, 0.05, 0.1)
        self.vtk_renderer.SetGradientBackground(True)
        
        # Create render window
        self.vtk_render_window = self.vtk_widget.GetRenderWindow()
        self.vtk_render_window.AddRenderer(self.vtk_renderer)
        self.vtk_render_window.SetMultiSamples(8)  # Anti-aliasing
        
        # Create mesh actor for voxel/lidar data
        self.vtk_mesh_mapper = vtkPolyDataMapper()
        self.vtk_mesh_actor = vtkActor()
        self.vtk_mesh_actor.SetMapper(self.vtk_mesh_mapper)
        
        # Mesh appearance
        mesh_property = self.vtk_mesh_actor.GetProperty()
        mesh_property.SetRepresentationToSurface()  # Solid surface
        mesh_property.SetColor(0.6, 0.7, 0.9)  # Light blue
        mesh_property.SetOpacity(0.9)
        mesh_property.SetAmbient(0.3)
        mesh_property.SetDiffuse(0.6)
        mesh_property.SetSpecular(0.5)
        mesh_property.SetSpecularPower(30)
        
        self.vtk_renderer.AddActor(self.vtk_mesh_actor)
        
        # Create robot cube actor
        self.vtk_robot_source = vtkCubeSource()
        self.vtk_robot_source.SetXLength(0.4)
        self.vtk_robot_source.SetYLength(0.25)
        self.vtk_robot_source.SetZLength(0.15)
        
        self.vtk_robot_mapper = vtkPolyDataMapper()
        self.vtk_robot_mapper.SetInputConnection(self.vtk_robot_source.GetOutputPort())
        
        self.vtk_robot_actor = vtkActor()
        self.vtk_robot_actor.SetMapper(self.vtk_robot_mapper)
        
        # Robot appearance
        robot_property = self.vtk_robot_actor.GetProperty()
        robot_property.SetColor(1.0, 0.2, 0.2)  # Red
        robot_property.SetOpacity(0.8)
        
        self.vtk_renderer.AddActor(self.vtk_robot_actor)
        
        # Add coordinate axes
        try:
            from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
            axes = vtkAxesActor()
            axes.SetTotalLength(1.0, 1.0, 1.0)
            axes.SetShaftTypeToCylinder()
            axes.SetCylinderRadius(0.02)
            axes.AxisLabelsOn()
            self.vtk_renderer.AddActor(axes)
        except ImportError:
            logger.warning("vtkAxesActor not available")
        
        # Setup camera
        camera = self.vtk_renderer.GetActiveCamera()
        camera.SetPosition(8.0, -8.0, 6.0)
        camera.SetFocalPoint(0.0, 0.0, 0.0)
        camera.SetViewUp(0.0, 0.0, 1.0)
        camera.SetViewAngle(45)
        camera.SetClippingRange(0.1, 100.0)
        
        # Set up interactor style
        style = CustomInteractorStyle()
        self.vtk_widget.GetRenderWindow().GetInteractor().SetInteractorStyle(style)
        
        # Initialize interactor
        self.vtk_widget.Initialize()
        self.vtk_widget.Start()
    
    def update_lidar_data(self, positions: npt.NDArray[np.uint8], face_count: int, resolution: float, origin: tuple[float, float, float]):
        """Update lidar data and render visualization (called from GUI thread)."""
        if not VTK_AVAILABLE:
            return
        
        self.positions = positions
        self.face_count = face_count
        self.resolution = resolution
        self.origin = origin
        
        # Update info label
        self.info_label.setText(
            f"Lidar: {face_count} faces, res={resolution:.3f}m, origin=({origin[0]:.2f}, {origin[1]:.2f}, {origin[2]:.2f})"
        )
        
        # Update VTK visualization
        self._update_vtk_mesh()
    
    def update_lidar_mesh(self, mesh: 'o3d.geometry.TriangleMesh'):
        """Update lidar visualization with Open3D mesh (called from GUI thread)."""
        if not VTK_AVAILABLE or not OPEN3D_AVAILABLE:
            logger.warning("VTK or Open3D not available for mesh display")
            return
        
        try:
            # Convert Open3D mesh to VTK PolyData
            polydata = open3d_mesh_to_vtk_polydata(mesh)
            
            # Update mapper
            self.vtk_mesh_mapper.SetInputData(polydata)
            self.vtk_mesh_mapper.Update()
            
            # Render
            self.vtk_render_window.Render()
            
            # Update info label
            vertices = np.asarray(mesh.vertices)
            triangles = np.asarray(mesh.triangles)
            self.info_label.setText(
                f"Lidar Mesh: {len(vertices)} vertices, {len(triangles)} triangles"
            )
        except Exception as e:
            logger.warning(f"Failed to update VTK mesh from Open3D: {e}")
    
    def _update_vtk_mesh(self):
        """Update VTK mesh with current lidar voxel data."""
        if not VTK_AVAILABLE or self.positions is None or self.face_count == 0:
            return
        
        try:
            # Decode positions to world coordinates
            pos = self.positions.reshape(-1, 3).astype(np.float32)
            pts = pos * self.resolution
            origin_arr = np.array(self.origin, dtype=np.float32)
            world_pts = pts + origin_arr
            
            # Create VTK points
            vtk_points = vtkPoints()
            for pt in world_pts:
                vtk_points.InsertNextPoint(float(pt[0]), float(pt[1]), float(pt[2]))
            
            # Create triangles from faces
            triangles = vtkCellArray()
            for i in range(self.face_count):
                base_idx = i * 4
                if base_idx + 3 < len(world_pts):
                    # Create two triangles from quad
                    triangles.InsertNextCell(3)
                    triangles.InsertCellPoint(base_idx + 0)
                    triangles.InsertCellPoint(base_idx + 1)
                    triangles.InsertCellPoint(base_idx + 2)
                    
                    triangles.InsertNextCell(3)
                    triangles.InsertCellPoint(base_idx + 2)
                    triangles.InsertCellPoint(base_idx + 1)
                    triangles.InsertCellPoint(base_idx + 3)
            
            # Create polydata
            polydata = vtkPolyData()
            polydata.SetPoints(vtk_points)
            polydata.SetPolys(triangles)
            
            # Update mapper
            self.vtk_mesh_mapper.SetInputData(polydata)
            self.vtk_mesh_mapper.Update()
            
            # Render
            self.vtk_render_window.Render()
            
        except Exception as e:
            logger.warning(f"Failed to update VTK mesh: {e}")
    
    def _quat_to_vtk_transform(self, position: dict, orientation: dict) -> vtkTransform:
        """Convert quaternion to VTK transform."""
        qx, qy, qz, qw = orientation["x"], orientation["y"], orientation["z"], orientation["w"]
        
        # Convert quaternion to rotation matrix
        xx, yy, zz = qx*qx, qy*qy, qz*qz
        xy, xz, yz = qx*qy, qx*qz, qy*qz
        wx, wy, wz = qw*qx, qw*qy, qw*qz
        
        transform = vtkTransform()
        matrix = [
            1 - 2*(yy + zz),     2*(xy - wz),     2*(xz + wy), position["x"],
                2*(xy + wz), 1 - 2*(xx + zz),     2*(yz - wx), position["y"],
                2*(xz - wy),     2*(yz + wx), 1 - 2*(xx + yy), position["z"],
                           0,                0,                0,            1
        ]
        transform.SetMatrix(matrix)
        return transform
    
    def update_robot_pose(self, position: dict, orientation: dict):
        """Update robot pose in visualization."""
        self.robot_pos = position
        self.robot_orient = orientation
        
        if not VTK_AVAILABLE:
            return
        
        try:
            transform = self._quat_to_vtk_transform(position, orientation)
            self.vtk_robot_actor.SetUserTransform(transform)
            self.vtk_render_window.Render()
        except Exception as e:
            logger.warning(f"Failed to update VTK robot pose: {e}")
    
    def closeEvent(self, event):
        """Cleanup when widget is closed."""
        if VTK_AVAILABLE and hasattr(self, 'vtk_widget'):
            try:
                self.vtk_widget.Finalize()
            except Exception as e:
                logger.warning(f"Error finalizing VTK widget: {e}")
        
        super().closeEvent(event)

class OdometryWidget(QWidget):
    """Widget for displaying odometry information."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 200)
        
        # Create group box
        group = QGroupBox("Odometry Data")
        group.setStyleSheet("""
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
            QLabel {
                color: #ddd;
                padding: 2px;
            }
        """)
        
        layout = QGridLayout()
        
        # Position labels
        self.pos_x_label = QLabel("X: 0.000")
        self.pos_y_label = QLabel("Y: 0.000")
        self.pos_z_label = QLabel("Z: 0.000")
        
        # Orientation labels
        self.orient_x_label = QLabel("Qx: 0.000")
        self.orient_y_label = QLabel("Qy: 0.000")
        self.orient_z_label = QLabel("Qz: 0.000")
        self.orient_w_label = QLabel("Qw: 1.000")
        
        # Add to layout
        layout.addWidget(QLabel("<b>Position:</b>"), 0, 0, 1, 2)
        layout.addWidget(self.pos_x_label, 1, 0)
        layout.addWidget(self.pos_y_label, 1, 1)
        layout.addWidget(self.pos_z_label, 2, 0)
        
        layout.addWidget(QLabel("<b>Orientation:</b>"), 3, 0, 1, 2)
        layout.addWidget(self.orient_x_label, 4, 0)
        layout.addWidget(self.orient_y_label, 4, 1)
        layout.addWidget(self.orient_z_label, 5, 0)
        layout.addWidget(self.orient_w_label, 5, 1)
        
        group.setLayout(layout)
        
        main_layout = QVBoxLayout()
        main_layout.addWidget(group)
        main_layout.addStretch()
        self.setLayout(main_layout)
    
    def update_odometry(self, position: dict, orientation: dict):
        """Update odometry display."""
        try:
            self.pos_x_label.setText(f"X: {position['x']:.3f}")
            self.pos_y_label.setText(f"Y: {position['y']:.3f}")
            self.pos_z_label.setText(f"Z: {position['z']:.3f}")
            
            self.orient_x_label.setText(f"Qx: {orientation['x']:.3f}")
            self.orient_y_label.setText(f"Qy: {orientation['y']:.3f}")
            self.orient_z_label.setText(f"Qz: {orientation['z']:.3f}")
            self.orient_w_label.setText(f"Qw: {orientation['w']:.3f}")
        except Exception as e:
            logger.warning(f"Failed to update odometry display: {e}")


class StatusWidget(QWidget):
    """Widget for displaying connection and system status."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.connection_label = QLabel("Status: Disconnected")
        self.connection_label.setStyleSheet("""
            QLabel {
                color: #ff5555;
                background-color: #2d2d2d;
                padding: 8px;
                border-radius: 4px;
                font-weight: bold;
            }
        """)
        
        self.info_label = QLabel("Ready")
        self.info_label.setStyleSheet("QLabel { color: #aaa; padding: 4px; }")
        
        layout = QVBoxLayout()
        layout.addWidget(self.connection_label)
        layout.addWidget(self.info_label)
        self.setLayout(layout)
    
    def set_connected(self, connected: bool):
        """Update connection status."""
        if connected:
            self.connection_label.setText("Status: Connected")
            self.connection_label.setStyleSheet("""
                QLabel {
                    color: #50fa7b;
                    background-color: #2d2d2d;
                    padding: 8px;
                    border-radius: 4px;
                    font-weight: bold;
                }
            """)
        else:
            self.connection_label.setText("Status: Disconnected")
            self.connection_label.setStyleSheet("""
                QLabel {
                    color: #ff5555;
                    background-color: #2d2d2d;
                    padding: 8px;
                    border-radius: 4px;
                    font-weight: bold;
                }
            """)
    
    def set_info(self, text: str):
        """Update info text."""
        self.info_label.setText(text)
