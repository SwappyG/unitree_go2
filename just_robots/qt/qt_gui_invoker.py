"""Helper class to run functions from worker threads back onto the main GUI thread."""

from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtWidgets


class QtGuiInvoker(QtCore.QObject):
    """Helper to run functions that originate from threads back onto main GUI thread.

    Usage:
        invoker = QtGuiInvoker.make_invoker_on_gui_thread()

        # From any thread:
        invoker.call.emit(lambda: some_signal.emit(data))
    """

    call = QtCore.Signal(object)  # emits a Python callable

    @QtCore.Slot(object)  # type: ignore[reportCallIssue]
    def _run(self, fn: Callable[[], None]) -> None:
        """Execute the callable on the GUI thread."""
        fn()

    @classmethod
    def make_invoker_on_gui_thread(cls) -> QtGuiInvoker:
        """Factory function for creating a GuiInvoker on the GUI thread.

        Returns:
            A GuiInvoker instance with GUI-thread affinity.

        Raises:
            RuntimeError: If no QApplication is running.
        """
        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("No QApplication running")
        inv = QtGuiInvoker()
        inv.moveToThread(app.thread())  # ensure GUI-thread affinity
        inv.call.connect(  # type: ignore[reportAttributeAccessIssue]
            inv._run, QtCore.Qt.ConnectionType.QueuedConnection
        )
        return inv
