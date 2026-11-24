"""localsync_modules.py — GUI pages for LocalSync_Utility.

Same module contract as the SereneOS Control Panel: each page is a plain
class exposing `.widget` and `.refresh()` so the main window can host any
mix of modules. Flat layout — lives next to localsync_core.py.

Pages
-----
  OverviewPage       — sync health dashboard: tailscale state, pairs, tools
  SyncPairsPage      — add/edit/test/run sync pairs (source→remote dirs)
  AccessPage         — remote-access profiles (ssh, rdp, vnc, mosh…)
  ServicesPage       — systemd REMOTE services only (sshd, rsyncd, xrdp…)
  ToolsPage          — protocol/tool catalog with installed detection
  SettingsPage       — mirror-delete warning toggles, cron log peek
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

import localsync_core as core


# ============================================================
# helpers
# ============================================================

class Page:
    """Minimal contract every module implements (icond convention)."""

    def __init__(self, window):
        self.window = window
        self.widget = QWidget()
        self.root = QVBoxLayout(self.widget)
        self.root.setContentsMargins(16, 14, 16, 14)

    def refresh(self):
        pass


def run(argv: list[str], timeout: int = 20):
    try:
        import subprocess
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, check=False)
        return r.stdout, r.stderr, r.returncode
    except FileNotFoundError:
        return "", f"Not installed: {argv[0]}", -1
    except subprocess.TimeoutExpired:
        return "", f"Timed out after {timeout}s", -1


def _table(cols: int, headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, cols)
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(cols - 1, QHeaderView.ResizeMode.Stretch)
    return t


# ============================================================
# Overview
# ============================================================

class OverviewPage(Page):
    """Dashboard — tailscale, pair counts, missing tools."""

    def __init__(self, window):
        super().__init__(window)
        self.root.addWidget(QLabel("<b>Overview</b>"))
        self.lbl_ts = QLabel("…")
        self.lbl_pairs = QLabel("…")
        self.lbl_missing = QLabel("…")
        self.lbl_cfg = QLabel("…")
        for w in (self.lbl_ts, self.lbl_pairs, self.lbl_missing, self.lbl_cfg):
            w.setWordWrap(True)
            self.root.addWidget(w)
        self.root.addStretch(1)
        self.refresh()

    def refresh(self):
        cfg = core.load_config()
        ts = core.tailscale_state()
        if ts == "up":
            ts_txt = "✔ connected"
            ip = core.tailscale_ip()
            if ip:
                ts_txt += f"  ({ip})"
        else:
            ts_txt = f"✘ {ts} — sync across machines needs the tailnet"
        self.lbl_ts.setText(f"<b>Tailscale:</b> {ts_txt}")

        enabled = sum(1 for p in cfg["pairs"] if p.get("enabled"))
        self.lbl_pairs.setText(
            f"<b>Sync pairs:</b> {len(cfg['pairs'])} configured, "
            f"{enabled} enabled"
        )

        need: set[str] = set()
        for p in cfg["pairs"]:
            for b in core.SYNC_PROTOCOLS[p["protocol"]]["bins"]:
                if not core.tool_installed([b]):
                    need.add(b)
        self.lbl_missing.setText(
            "<b>Missing binaries:</b> " + (", ".join(sorted(need)) if need
                                           else "none — all good ✔")
        )
        self.lbl_cfg.setText(f"<b>Config file:</b> {core.CONFIG_PATH}")


# ============================================================
# Sync pairs
# ============================================================

class PairDialog(QDialog):
    """Create or edit one sync pair."""

    def __init__(self, parent=None, pair: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Sync pair")
        self.setMinimumWidth(520)
        form = QFormLayout(self)

        pair = pair or core.blank_pair()
        self.pair = pair

        self.ed_name = QLineEdit(pair["name"])
        form.addRow("Name", self.ed_name)

        self.ed_src = QLineEdit(pair["source_dir"])
        b_pick = QPushButton("…")
        b_pick.setFixedWidth(36)
        b_pick.clicked.connect(self._pick_src)
        row = QHBoxLayout()
        row.addWidget(self.ed_src, stretch=1)
        row.addWidget(b_pick)
        w = QWidget(); w.setLayout(row)
        form.addRow("Source directory", w)

        self.cb_proto = QComboBox()
        for pid, spec in core.SYNC_PROTOCOLS.items():
            self.cb_proto.addItem(spec["label"], pid)
        self.cb_proto.setCurrentIndex(
            max(0, list(core.SYNC_PROTOCOLS).index(pair["protocol"]))
        )
        self.cb_proto.currentIndexChanged.connect(self._proto_changed)
        form.addRow("Protocol", self.cb_proto)

        self.lbl_proto_desc = QLabel(
            core.SYNC_PROTOCOLS[pair["protocol"]]["desc"])
        self.lbl_proto_desc.setWordWrap(True)
        form.addRow("", self.lbl_proto_desc)

        self.ed_host = QLineEdit(pair["host"])
        form.addRow("Host (IP / DNS / rclone remote)", self.ed_host)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(0, 65535)
        self.sp_port.setValue(int(pair["port"]))
        form.addRow("Port", self.sp_port)

        self.ed_user = QLineEdit(pair["user"])
        form.addRow("User", self.ed_user)

        self.ed_pass = QLineEdit(pair["password"])
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password (env-only, never argv)", self.ed_pass)

        self.ed_key = QLineEdit(pair["key_path"])
        form.addRow("SSH key path", self.ed_key)

        self.ed_remote = QLineEdit(pair["remote_path"])
        form.addRow("Remote path / module / share", self.ed_remote)

        self.cb_mirror = QCheckBox(
            "Mirror (--delete: remove remote files missing locally)")
        self.cb_mirror.setChecked(bool(pair.get("mirror")))
        form.addRow("", self.cb_mirror)

        self.ed_extra = QLineEdit(pair.get("extra_args", ""))
        form.addRow("Extra CLI arguments", self.ed_extra)

        row2 = QHBoxLayout()
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["manual", "interval", "cron"])
        self.cb_mode.setCurrentText(pair["schedule"]["mode"])
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(1, 10080)
        self.sp_interval.setValue(int(pair["schedule"]["interval"]))
        self.sp_interval.setSuffix(" min")
        self.ed_cron = QLineEdit(pair["schedule"]["cron"])
        self.ed_cron.setPlaceholderText("*/5 * * * *")
        row2.addWidget(self.cb_mode)
        row2.addWidget(self.sp_interval)
        row2.addWidget(self.ed_cron, stretch=1)
        w2 = QWidget(); w2.setLayout(row2)
        form.addRow("Schedule", w2)

        self._proto_changed()
        buttons = QHBoxLayout()
        b_ok = QPushButton("Save"); b_cancel = QPushButton("Cancel")
        b_ok.clicked.connect(self.accept); b_cancel.clicked.connect(self.reject)
        buttons.addStretch(1); buttons.addWidget(b_cancel); buttons.addWidget(b_ok)
        form.addRow(buttons)

    def _pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "Source directory",
                                             self.ed_src.text())
        if d:
            self.ed_src.setText(d)

    def _proto_changed(self):
        pid = self.cb_proto.currentData()
        spec = core.SYNC_PROTOCOLS[pid]
        self.lbl_proto_desc.setText(spec["desc"])
        self.sp_port.setValue(spec["port"])

    def result_pair(self) -> dict:
        self.pair.update({
            "name": self.ed_name.text().strip() or "unnamed",
            "source_dir": self.ed_src.text().strip(),
            "protocol": self.cb_proto.currentData(),
            "host": self.ed_host.text().strip(),
            "port": self.sp_port.value(),
            "user": self.ed_user.text().strip(),
            "password": self.ed_pass.text(),
            "key_path": self.ed_key.text().strip(),
            "remote_path": self.ed_remote.text().strip(),
            "mirror": self.cb_mirror.isChecked(),
            "extra_args": self.ed_extra.text().strip(),
            "schedule": {
                "mode": self.cb_mode.currentText(),
                "interval": self.sp_interval.value(),
                "cron": self.ed_cron.text().strip(),
            },
        })
        return self.pair


class SyncPairsPage(Page):
    """Source dir → remote dir pairs, per-pair protocol + schedule."""

    def __init__(self, window):
        super().__init__(window)
        self.root.addWidget(QLabel("<b>Sync Pairs</b>"))
        self.root.addWidget(QLabel(
            "Each row is one directory pair: local source → remote target, "
            "with its own protocol, credentials and schedule."))

        btns = QHBoxLayout()
        b_add = QPushButton("＋ Add pair")
        b_edit = QPushButton("✎ Edit")
        b_del = QPushButton("🗑 Delete")
        b_test = QPushButton("🧪 Dry-run test")
        b_run = QPushButton("▶ Run now")
        b_apply = QPushButton("🗓 Apply cron schedule")
        b_add.clicked.connect(self.add_pair)
        b_edit.clicked.connect(self.edit_pair)
        b_del.clicked.connect(self.delete_pair)
        b_test.clicked.connect(lambda: self.run_pair(dry=True))
        b_run.clicked.connect(lambda: self.run_pair(dry=False))
        b_apply.clicked.connect(self.apply_cron)
        for b in (b_add, b_edit, b_del, b_test, b_run, b_apply):
            btns.addWidget(b)
        btns.addStretch(1)
        self.root.addLayout(btns)

        self.table = _table(6, ["Name", "Enabled", "Protocol", "Source",
                                "Destination", "Schedule / last run"])
        self.table.doubleClicked.connect(self.edit_pair)
        self.root.addWidget(self.table, stretch=1)
        self.refresh()

    def refresh(self):
        cfg = core.load_config()
        self.table.setRowCount(0)
        for p in cfg["pairs"]:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(p["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(
                "yes" if p.get("enabled") else "no"))
            self.table.setItem(r, 2, QTableWidgetItem(
                core.SYNC_PROTOCOLS[p["protocol"]]["label"]))
            self.table.setItem(r, 3, QTableWidgetItem(p["source_dir"]))
            dst = (f"{p['user']}@{p['host']}:{p['remote_path']}"
                   if p.get("host") else p["remote_path"])
            self.table.setItem(r, 4, QTableWidgetItem(dst))
            sched = core.schedule_expr(p)
            line = ("cron: " + sched if sched else
                    f"{p['schedule']['mode']} · last: {p.get('last_run') or 'never'}"
                    f" · {p.get('last_status', '')}")
            self.table.setItem(r, 5, QTableWidgetItem(line))

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            self.window.status_msg("Select a pair first.")
            return None
        return core.load_config()["pairs"][row]

    def _write_back(self, updated: dict):
        cfg = core.load_config()
        for i, p in enumerate(cfg["pairs"]):
            if p["name"] == updated["name"]:
                cfg["pairs"][i] = updated
                break
        core.save_config(cfg)

    def add_pair(self):
        dlg = PairDialog(self.widget)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pair = dlg.result_pair()
            pair["enabled"] = True
            problems = core.validate_pair(pair)
            if problems:
                QMessageBox.warning(self.widget, "Incomplete pair",
                                    "Pair saved, but note:\n" + problems)
            cfg = core.load_config()
            cfg["pairs"].append(pair)
            core.save_config(cfg)
            self.window.status_msg(f"Pair '{pair['name']}' added.")
            self.refresh()

    def edit_pair(self):
        row = self.table.currentRow()
        if row < 0:
            self.window.status_msg("Select a pair first.")
            return
        cfg = core.load_config()
        dlg = PairDialog(self.widget, cfg["pairs"][row])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._write_back(dlg.result_pair())
            self.window.status_msg("Pair updated.")
            self.refresh()

    def delete_pair(self):
        row = self.table.currentRow()
        if row < 0:
            self.window.status_msg("Select a pair first.")
            return
        cfg = core.load_config()
        name = cfg["pairs"][row]["name"]
        if QMessageBox.question(
                self.widget, "Delete", f"Delete pair '{name}'?") \
                == QMessageBox.StandardButton.Yes:
            del cfg["pairs"][row]
            core.save_config(cfg)
            self.refresh()

    def run_pair(self, dry: bool):
        pair = self._selected()
        if pair is None:
            return
        cfg = core.load_config()
        try:
            built = core.build_sync_command(pair, dry_run=dry)
        except core.BuildError as exc:
            QMessageBox.warning(self.widget, "Incomplete pair", str(exc))
            return
        self.window.status_msg(("Testing" if dry else "Syncing")
                               + f" '{pair['name']}'…")
        import subprocess
        import tempfile
        env = dict(os.environ)
        env.update(built["env"])
        argv = list(built["argv"])
        tmpfile = None
        if built["authfile"]:
            fd, tmpfile = tempfile.mkstemp(prefix="lsu-auth-")
            os.write(fd, built["authfile"].encode())
            os.close(fd)
            os.chmod(tmpfile, 0o600)
            argv = [tmpfile if a == "__AUTHFILE__" else a for a in argv]
        try:
            r = subprocess.run(argv, env=env, capture_output=True, text=True,
                               timeout=3600, check=False)
            ok = r.returncode == 0
            detail = (r.stderr or r.stdout or "done").strip()[-300:]
            core.record_run(cfg, pair["name"], ok, detail)
            self.window.status_msg(
                f"Pair '{pair['name']}' "
                + ("test OK ✔" if ok and dry else
                   "synced OK ✔" if ok else f"FAILED (exit {r.returncode})"))
            self.refresh()
        finally:
            if tmpfile:
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass

    def apply_cron(self):
        cfg = core.load_config()
        try:
            n = core.install_crontab(cfg)
            self.window.status_msg(
                f"Crontab applied: {n} schedule line(s) installed.")
        except (RuntimeError, FileNotFoundError) as exc:
            QMessageBox.warning(self.widget, "crontab", str(exc))


# ============================================================
# Remote access
# ============================================================

class AccessDialog(QDialog):
    def __init__(self, parent=None, profile: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Remote access profile")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        profile = profile or core.blank_profile()
        self.profile = profile

        self.ed_name = QLineEdit(profile["name"])
        form.addRow("Name", self.ed_name)

        self.cb_tool = QComboBox()
        for tid, spec in core.ACCESS_TOOLS.items():
            self.cb_tool.addItem(spec["label"], tid)
        self.cb_tool.setCurrentIndex(
            max(0, list(core.ACCESS_TOOLS).index(profile["tool"])))
        self.cb_tool.currentIndexChanged.connect(self._tool_changed)
        form.addRow("Tool", self.cb_tool)

        self.lbl_desc = QLabel(core.ACCESS_TOOLS[profile["tool"]]["desc"])
        self.lbl_desc.setWordWrap(True)
        form.addRow("", self.lbl_desc)

        self.ed_host = QLineEdit(profile["host"])
        form.addRow("Host / MagicDNS name", self.ed_host)

        self.sp_port = QSpinBox()
        self.sp_port.setRange(0, 65535)
        self.sp_port.setValue(int(profile["port"]))
        form.addRow("Port", self.sp_port)

        self.ed_user = QLineEdit(profile["user"])
        form.addRow("User", self.ed_user)

        self.ed_extra = QLineEdit(profile.get("extra_args", ""))
        form.addRow("Extra CLI arguments", self.ed_extra)

        self._tool_changed()
        buttons = QHBoxLayout()
        b_ok = QPushButton("Save"); b_cancel = QPushButton("Cancel")
        b_ok.clicked.connect(self.accept); b_cancel.clicked.connect(self.reject)
        buttons.addStretch(1); buttons.addWidget(b_cancel); buttons.addWidget(b_ok)
        form.addRow(buttons)

    def _tool_changed(self):
        spec = core.ACCESS_TOOLS[self.cb_tool.currentData()]
        self.lbl_desc.setText(spec["desc"])
        self.sp_port.setValue(spec["port"])

    def result_profile(self) -> dict:
        self.profile.update({
            "name": self.ed_name.text().strip() or "unnamed",
            "tool": self.cb_tool.currentData(),
            "host": self.ed_host.text().strip(),
            "port": self.sp_port.value(),
            "user": self.ed_user.text().strip(),
            "extra_args": self.ed_extra.text().strip(),
        })
        return self.profile


class AccessPage(Page):
    """Profiles for the remote tools in the catalog (launch commands)."""

    def __init__(self, window):
        super().__init__(window)
        self.root.addWidget(QLabel("<b>Remote Access Profiles</b>"))
        self.root.addWidget(QLabel(
            "Configure how you reach other machines: ssh, rdp, vnc, mosh, "
            "x2go, nx, telnet, Tailscale-SSH… Launch builds the command "
            "for the selected profile."))

        btns = QHBoxLayout()
        b_add = QPushButton("＋ Add profile")
        b_edit = QPushButton("✎ Edit")
        b_del = QPushButton("🗑 Delete")
        b_launch = QPushButton("🚀 Launch")
        b_copy = QPushButton("📋 Copy command")
        b_add.clicked.connect(self.add_profile)
        b_edit.clicked.connect(self.edit_profile)
        b_del.clicked.connect(self.delete_profile)
        b_launch.clicked.connect(self.launch_profile)
        b_copy.clicked.connect(self.copy_profile)
        for b in (b_add, b_edit, b_del, b_launch, b_copy):
            btns.addWidget(b)
        btns.addStretch(1)
        self.root.addLayout(btns)

        self.table = _table(4, ["Name", "Tool", "Destination", "Command"])
        self.table.doubleClicked.connect(self.edit_profile)
        self.root.addWidget(self.table, stretch=1)
        self.refresh()

    def refresh(self):
        cfg = core.load_config()
        self.table.setRowCount(0)
        for prof in cfg["access_profiles"]:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(prof["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(
                core.ACCESS_TOOLS[prof["tool"]]["label"]))
            dest = f"{prof['user']}@{prof['host']}:{prof['port']}" \
                if prof["user"] else f"{prof['host']}:{prof['port']}"
            self.table.setItem(r, 2, QTableWidgetItem(dest))
            try:
                argv = core.build_access_command(prof)
                self.table.setItem(r, 3, QTableWidgetItem(" ".join(argv)))
            except core.BuildError as exc:
                self.table.setItem(r, 3, QTableWidgetItem(f"(incomplete: {exc})"))

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            self.window.status_msg("Select a profile first.")
            return None
        return core.load_config()["access_profiles"][row]

    def add_profile(self):
        dlg = AccessDialog(self.widget)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = core.load_config()
            cfg["access_profiles"].append(dlg.result_profile())
            core.save_config(cfg)
            self.refresh()

    def edit_profile(self):
        row = self.table.currentRow()
        if row < 0:
            self.window.status_msg("Select a profile first.")
            return
        cfg = core.load_config()
        dlg = AccessDialog(self.widget, cfg["access_profiles"][row])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            prof = dlg.result_profile()
            for i, p in enumerate(cfg["access_profiles"]):
                if p["name"] == prof["name"]:
                    cfg["access_profiles"][i] = prof
                    break
            core.save_config(cfg)
            self.refresh()

    def delete_profile(self):
        row = self.table.currentRow()
        if row < 0:
            return
        cfg = core.load_config()
        name = cfg["access_profiles"][row]["name"]
        if QMessageBox.question(self.widget, "Delete",
                                f"Delete profile '{name}'?") \
                == QMessageBox.StandardButton.Yes:
            del cfg["access_profiles"][row]
            core.save_config(cfg)
            self.refresh()

    def launch_profile(self):
        prof = self._selected()
        if prof is None:
            return
        try:
            argv = core.build_access_command(prof)
        except core.BuildError as exc:
            QMessageBox.warning(self.widget, "Incomplete profile", str(exc))
            return
        import subprocess
        self.window.status_msg(f"Launching: {' '.join(argv)}")
        subprocess.Popen(argv)

    def copy_profile(self):
        prof = self._selected()
        if prof is None:
            return
        try:
            argv = core.build_access_command(prof)
        except core.BuildError as exc:
            QMessageBox.warning(self.widget, "Incomplete profile", str(exc))
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(" ".join(argv))
        self.window.status_msg("Command copied to clipboard.")


# ============================================================
# Remote services (systemd, filtered)
# ============================================================

class ServicesPage(Page):
    """systemd units — ONLY remote/transfer-related ones, Serene-panel style."""

    def __init__(self, window):
        super().__init__(window)
        self.root.addWidget(QLabel("<b>Remote Services</b>"))
        self.root.addWidget(QLabel(
            "Filtered systemd view: only services that serve remote access "
            "or file transfer (sshd, rsyncd, vsftpd, smbd, nfs, xrdp, "
            "tailscaled, syncthing, cron, http servers…)."))
        self.lbl_filter = QLabel("…")
        self.lbl_filter.setWordWrap(True)
        self.root.addWidget(self.lbl_filter)

        btns = QHBoxLayout()
        b_start = QPushButton("▶ Start")
        b_stop = QPushButton("⏹ Stop")
        b_restart = QPushButton("↻ Restart")
        b_enable = QPushButton("📌 Enable")
        b_refresh = QPushButton("🔄 Refresh")
        for b, fn in ((b_start, "start"), (b_stop, "stop"),
                      (b_restart, "restart"), (b_enable, "enable"),
                      (b_refresh, "refresh")):
            b.clicked.connect(lambda _=False, f=fn: self.act(f))
            btns.addWidget(b)
        btns.addStretch(1)
        self.root.addLayout(btns)

        self.table = _table(3, ["Unit", "Active", "Description"])
        self.root.addWidget(self.table, stretch=1)
        self.all_units = []
        self.refresh()

    def refresh(self):
        out, err, code = run(
            ["systemctl", "list-units", "--type=service", "--all",
             "--no-pager", "--no-legend"])
        if code != 0:
            self.window.status_msg(f"systemctl failed: {err.strip()[:80]}")
            return
        self.all_units = []
        for line in out.splitlines():
            parts = line.split()
            if parts and parts[0].endswith(".service"):
                desc = " ".join(parts[4:]) if len(parts) > 4 else ""
                self.all_units.append((parts[0], parts[2], desc))

        shown = [(u, s, d) for u, s, d in self.all_units
                 if core.is_remote_service(u)]
        self.table.setRowCount(0)
        for unit, state, desc in shown:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(unit))
            self.table.setItem(r, 1, QTableWidgetItem(state))
            self.table.setItem(r, 2, QTableWidgetItem(desc[:100]))
        self.lbl_filter.setText(
            f"Showing {len(shown)} of {len(self.all_units)} units "
            f"(matched by: ssh, rsync, ftp, smb, nfs, rdp, vnc, tailscale, "
            f"syncthing, cron, web servers…)")

    def _selected_unit(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def act(self, action):
        if action == "refresh":
            self.refresh()
            return
        unit = self._selected_unit()
        if not unit:
            self.window.status_msg("Select a service first.")
            return
        argv = ["pkexec", "systemctl", action, unit]
        self.window.status_msg(f"Running systemctl {action} {unit}…")
        out, err, code = run(argv, timeout=60)
        if code == 0:
            self.window.status_msg(f"Service '{unit}' {action} done ✔")
            self.refresh()
        else:
            self.window.status_msg(f"Failed: {(err or out).strip()[:80]}")
            QMessageBox.warning(self.widget, "Action failed",
                                f"systemctl {action} {unit} failed:\n"
                                f"{(err or out)[:300]}")


# ============================================================
# Tools catalog
# ============================================================

class ToolsPage(Page):
    """Every protocol/tool the app knows, with installed-detection."""

    def __init__(self, window):
        super().__init__(window)
        self.root.addWidget(QLabel("<b>Protocols &amp; Tools</b>"))

        self.table = _table(4, ["Name", "Kind", "Binaries", "Installed / notes"])
        self.root.addWidget(self.table, stretch=1)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(0)
        rows = []
        for pid, spec in core.SYNC_PROTOCOLS.items():
            found = [b for b in spec["bins"] if core.tool_installed([b])]
            missing = [b for b in spec["bins"] if not core.tool_installed([b])]
            note = ("✔ " + ", ".join(found)) if found \
                else "missing: " + ", ".join(missing)
            rows.append((spec["label"], "sync transport",
                         ", ".join(spec["bins"]), note))
        for tid, spec in core.ACCESS_TOOLS.items():
            found = core.tool_installed(spec["bins"])
            note = f"✔ {found}" if found else "not installed"
            rows.append((spec["label"], "remote access",
                         ", ".join(spec["bins"]), note))
        for label, kind, bins, note in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(label))
            self.table.setItem(r, 1, QTableWidgetItem(kind))
            self.table.setItem(r, 2, QTableWidgetItem(bins))
            self.table.setItem(r, 3, QTableWidgetItem(note))
