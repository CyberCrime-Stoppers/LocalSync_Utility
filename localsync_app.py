#!/usr/bin/env python3
"""localsync_app.py — GUI entry point for LocalSync_Utility.

Run:
    python3 localsync_app.py

FLAT LAYOUT: all files sit together in one folder (localsync_core.py,
localsync_theme.py, localsync_main_window.py, localsync_modules.py) —
copy the folder anywhere and it runs. Imports are sys.path-fixed
relative to THIS file.

Dependencies: PySide6 (python3-pyside6 on KDE Neon / Ubuntu, pyside6 on
Arch family), plus whichever sync tools you configure (rsync, lftp,
rclone, smbclient…).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("LocalSync Utility")
    app.setOrganizationName("SereneForge")

    from localsync_main_window import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
