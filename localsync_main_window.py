"""localsync_main_window.py — Serene-themed main window for LocalSync_Utility.

Same skeleton as serene_main_window.py: header / sidebar / QStackedWidget /
status bar, theme persisted via QSettings.
"""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from localsync_modules import (
    AccessPage, OverviewPage, ServicesPage, SyncPairsPage, ToolsPage,
)
from localsync_theme import PALETTES, build_stylesheet

ORG = "SereneForge"
APP = "LocalSync_Utility"
VERSION = "0.1.0"
NAV = [
    ("Overview", "🏠"),
    ("Sync Pairs", "🔁"),
    ("Remote Access", "🔌"),
    ("Remote Services", "🛰"),
    ("Tools", "🧰"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"LocalSync Utility — v{VERSION}")
        self.resize(1050, 660)
        self.setMinimumSize(800, 520)

        self.settings = QSettings(ORG, APP)
        current = self.settings.value("theme", "dark")
        if current not in PALETTES:
            current = "dark"

        self._build_ui()
        self.apply_theme(current)
        self.sidebar.setCurrentRow(0)

    # ---------------- UI construction ----------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), stretch=1)
        self.statusBar().showMessage("Ready.")

    def _build_header(self) -> QWidget:
        header = QWidget(objectName="header")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 10, 16, 10)

        title_box = QVBoxLayout()
        self.header_title = QLabel("LocalSync Utility", objectName="header-title")
        self.header_sub = QLabel(
            "Automatic file sync across your LAN and tailnet", objectName="header-sub")
        title_box.addWidget(self.header_title)
        title_box.addWidget(self.header_sub)
        h.addLayout(title_box, stretch=1)

        self.btn_theme = QPushButton("🌙 Dark", objectName="toggle")
        self.btn_theme.setToolTip("Switch between the Serene dark and light palettes.")
        self.btn_theme.clicked.connect(self.toggle_theme)
        h.addWidget(self.btn_theme)
        return header

    def _build_body(self) -> QWidget:
        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.sidebar = QListWidget(objectName="sidebar")
        self.sidebar.setFixedWidth(190)
        for name, icon in NAV:
            QListWidgetItem(f"{icon}  {name}", self.sidebar)
        self.sidebar.currentRowChanged.connect(self._navigate)
        h.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.pages = {}
        try:
            for name, cls in (
                ("Overview", OverviewPage),
                ("Sync Pairs", SyncPairsPage),
                ("Remote Access", AccessPage),
                ("Remote Services", ServicesPage),
                ("Tools", ToolsPage),
            ):
                page = cls(self)
                self.pages[name] = page
                self.stack.addWidget(page.widget)
        except Exception as exc:  # never crash: show the problem in-window
            from PySide6.QtWidgets import QLabel as _L
            self.stack.addWidget(_L(f"Module init failed: {exc}"))

        h.addWidget(self.stack, stretch=1)
        return body

    # ---------------- navigation ----------------

    def _navigate(self, row: int):
        if not (0 <= row < len(NAV)):
            return
        self.stack.setCurrentIndex(row)
        page = self.pages.get(NAV[row][0])
        if page is not None:
            page.refresh()

    def status_msg(self, text: str):
        self.statusBar().showMessage(text)

    # ---------------- theme ----------------

    def apply_theme(self, name: str):
        self.theme_name = name
        self.setStyleSheet(build_stylesheet(PALETTES[name]))
        self.btn_theme.setText("☀ Light" if name == "dark" else "🌙 Dark")
        self.settings.setValue("theme", name)

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")
