# live_mesh_viewer_threaded_robot_safe.py
import threading, queue
import typing as t
import numpy as np
import numpy.typing as npt
import open3d as o3d

def _triangles_from_faces(face_count: int, flip_winding: bool = False) -> npt.NDArray[np.int32]:
    base = (np.arange(face_count, dtype=np.int32) * 4)[:, None]
    if not flip_winding:
        t0 = base + np.array([0, 1, 2], dtype=np.int32)
        t1 = base + np.array([2, 1, 3], dtype=np.int32)
    else:
        t0 = base + np.array([0, 2, 1], dtype=np.int32)
        t1 = base + np.array([2, 3, 1], dtype=np.int32)
    return np.vstack([t0, t1])

def _positions_u8_to_world_points(
    positions_u8: npt.NDArray[np.uint8],
    resolution: float,
    origin_xyz: t.Sequence[float],
    axis_order: tuple[int, int, int] = (0, 1, 2),
) -> npt.NDArray[np.float32]:
    pos = np.asarray(positions_u8, dtype=np.uint8).reshape(-1, 3)[:, list(axis_order)]
    pts = pos.astype(np.float32) * float(resolution)
    origin = np.asarray(origin_xyz, dtype=np.float32)[None, :]
    return pts + origin

def _quat_to_rot(qx: float, qy: float, qz: float, qw: float) -> npt.NDArray[np.float64]:
    x, y, z, w = qx, qy, qz, qw
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz = x*y, x*z, y*z
    wx, wy, wz = w*x, w*y, w*z
    return np.array([
        [1 - 2*(yy + zz),     2*(xy - wz),     2*(xz + wy)],
        [    2*(xy + wz), 1 - 2*(xx + zz),     2*(yz - wx)],
        [    2*(xz - wy),     2*(yz + wx), 1 - 2*(xx + yy)],
    ], dtype=np.float64)

class VoxelMapViewer:
    """Open3D Visualizer on its own thread; accepts LiDAR frames and robot poses."""

    def __init__(
        self,
        window_name: str = "LiDAR Mesh Live",
        flip_winding: bool = False,
        compute_normals_every: int = 15,
        axis_order: tuple[int, int, int] = (0, 1, 2),
        robot_box_size: tuple[float, float, float] = (0.4, 0.25, 0.15),
        robot_color: tuple[float, float, float] = (0.9, 0.2, 0.2),
    ):
        self.window_name = window_name
        self.flip_winding = flip_winding
        self.compute_normals_every = int(compute_normals_every)
        self.axis_order = axis_order

        # frame queue: (positions_u8, face_count, resolution, origin)
        self._q: queue.Queue[tuple[npt.NDArray[np.uint8],int,float,tuple[float,float,float]]] = queue.Queue(maxsize=1)

        # pose (shared state)
        self._pose_lock = threading.Lock()
        self._pose_latest: tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]] | None = None  # (t: [3], q: [4])

        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._run, name="Open3DViewer", daemon=True)
        self._started = threading.Event()

        # robot base (canonical) mesh (centered at origin)
        self._robot_box_size = np.asarray(robot_box_size, dtype=np.float64)
        self._robot_color = robot_color

    def start(self):
        self._thr.start()
        self._started.wait()

    def close(self):
        self._stop.set()
        self._thr.join(timeout=2.0)

    # ---- public API ----

    def submit_u8(
        self,
        positions_u8: npt.NDArray[np.uint8],
        face_count: int,
        resolution: float,
        origin_xyz: tuple[float, float, float],
    ):
        # drop stale frame if queue is full
        try:
            while not self._q.empty():
                self._q.get_nowait()
        except queue.Empty:
            pass
        arr = np.array(positions_u8, dtype=np.uint8, copy=True)
        self._q.put((arr, int(face_count), float(resolution), origin_xyz))

    def submit_robot_pose(
        self,
        position: t.Mapping[str, float],
        orientation: t.Mapping[str, float],
    ):
        tvec = np.array([position["x"], position["y"], position["z"]], dtype=np.float64)
        qvec = np.array([orientation["x"], orientation["y"], orientation["z"], orientation["w"]], dtype=np.float64)
        with self._pose_lock:
            self._pose_latest = (tvec, qvec)

    # ---- thread (Open3D) ----

    def _run(self):
        vis = o3d.visualization.Visualizer()  # pyright: ignore[reportAttributeAccessIssue]
        vis.create_window(window_name=self.window_name)
        opt = vis.get_render_option()
        opt.light_on = True
        opt.mesh_show_back_face = True
        opt.mesh_show_wireframe = False
        opt.background_color = np.array([0.05, 0.05, 0.08])

        # Lidar surface mesh
        lidar_mesh = o3d.geometry.TriangleMesh()
        added_lidar = False
        frame = 0
        last_face_count = None

        # Robot mesh: build a canonical box and clone it for rendering
        robot_canon = o3d.geometry.TriangleMesh.create_box(
            width=float(self._robot_box_size[0]),
            height=float(self._robot_box_size[1]),
            depth=float(self._robot_box_size[2]),
        )
        robot_canon.translate(-0.5 * self._robot_box_size)  # center at origin
        robot_canon.compute_vertex_normals()
        robot_canon.paint_uniform_color(self._robot_color)

        V0 = np.asarray(robot_canon.vertices)
        T0 = np.asarray(robot_canon.triangles)
        C0 = np.asarray(robot_canon.vertex_colors)

        robot_mesh = o3d.geometry.TriangleMesh()
        robot_mesh.vertices      = o3d.utility.Vector3dVector(V0.copy())
        robot_mesh.triangles     = o3d.utility.Vector3iVector(T0.copy())
        robot_mesh.vertex_colors = o3d.utility.Vector3dVector(C0.copy())
        robot_mesh.compute_vertex_normals()
        added_robot = False

        self._started.set()

        try:
            while not self._stop.is_set():
                # 1) LiDAR frame (non-blocking)
                got_frame = False
                try:
                    positions_u8, face_count, resolution, origin = self._q.get(timeout=0.01)
                    got_frame = True
                except queue.Empty:
                    pass

                if got_frame:
                    # NOTE: cleanup the control flow here, the implicit invariants are messing with the linter
                    pts = _positions_u8_to_world_points(positions_u8, resolution, origin, self.axis_order) # type: ignore

                    # Update triangles only when face_count changes
                    if face_count > 0 and last_face_count != face_count: # type: ignore
                        tris_np = _triangles_from_faces(face_count, self.flip_winding) # type: ignore
                        lidar_mesh.triangles = o3d.utility.Vector3iVector(tris_np)  # independent buffer
                        last_face_count = face_count # type: ignore

                    lidar_mesh.vertices = o3d.utility.Vector3dVector(pts.astype(np.float64))
                    if self.compute_normals_every > 0 and (frame % self.compute_normals_every) == 0:
                        lidar_mesh.compute_vertex_normals()

                    if not added_lidar:
                        vis.add_geometry(lidar_mesh)
                        added_lidar = True
                    else:
                        vis.update_geometry(lidar_mesh)

                    frame += 1

                # 2) Robot pose (apply every frame if available)
                with self._pose_lock:
                    pose = self._pose_latest

                if pose is not None:
                    tvec, qvec = pose
                    R = _quat_to_rot(qvec[0], qvec[1], qvec[2], qvec[3])  # 3x3
                    V = np.asarray(robot_canon.vertices)                  # [Nv,3] canonical
                    Vt = (V @ R.T) + tvec                                 # rotate + translate

                    robot_mesh.vertices = o3d.utility.Vector3dVector(Vt.astype(np.float64))
                    # triangles/colors on robot_mesh never change now

                    if not added_robot:
                        vis.add_geometry(robot_mesh)
                        added_robot = True
                    else:
                        vis.update_geometry(robot_mesh)

                vis.poll_events()
                vis.update_renderer()

        finally:
            vis.destroy_window()
