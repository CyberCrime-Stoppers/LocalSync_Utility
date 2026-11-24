"""localsync_core.py — plain-Python backend for LocalSync_Utility.

NO Qt imports — deliberately, so this module stays testable from the CLI
(python3 -c "import localsync_core ...") and reusable by cron.

Responsibilities
----------------
  - Config store        : JSON at ~/.config/LocalSync_Utility/config.json
                          (sync pairs, remote-access profiles, last runs)
  - Protocol catalog    : every sync-capable transport and remote-access
                          tool the GUI can configure
  - Command builders    : turn a pair/profile dict into a concrete argv
                          (+ env vars / temp auth files when needed)
  - Scheduler           : generate + install managed crontab lines via
                          marker comments (never touches other entries)
  - Detection helpers   : tailscale state, installed tools, systemd unit
                          filtering for the Remote Services page

Security notes
--------------
  - SSH keys are preferred over passwords everywhere (pair key_path).
  - lftp passwords travel via the LFTP_PASSWORD environment variable
    (--env-password), never in argv (visible to `ps` otherwise).
  - smbclient passwords use a throwaway authentication file written to
    a 0600 tempfile and deleted after the run — again, never in argv.
"""

import json
import os
import shutil
import subprocess
import tempfile

APP_NAME = "LocalSync_Utility"
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    APP_NAME,
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
RUNNER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                           "localsync_runner.py"))
CRON_BEGIN = "# localsync-begin (managed block - do not edit inside)"
CRON_END = "# localsync-end"
CRON_LOG = os.path.join(CONFIG_DIR, "localsync-cron.log")


# ============================================================
# protocol / tool catalog
# ============================================================

# --- file-sync transports (usable as a sync-pair protocol) ---
# engine tells build_sync_command() how to actually move bytes:
#   rsync-ssh | rsync-daemon | scp | lftp | rclone | smbclient |
#   rsync-local (nfs mountpoint) | syncthing-api
SYNC_PROTOCOLS = {
    "rsync-ssh": {
        "label": "rsync over SSH", "engine": "rsync-ssh", "port": 5790,
        "bins": ["rsync", "ssh"],
        "secure": True,
        "desc": "Best all-round choice: delta transfers, deletion "
                "mirroring, resumable. Runs over any SSH host.",
    },
    "rsync-daemon": {
        "label": "rsync daemon (rsyncd)", "engine": "rsync-daemon",
        "port": 873, "bins": ["rsync"], "secure": False,
        "desc": "Fast unencrypted rsync server module (LAN/Tailnet only). "
                "remote_path is 'module/subpath'.",
    },
    "scp": {
        "label": "SCP (secure copy)", "engine": "scp", "port": 5790,
        "bins": ["scp"], "secure": True,
        "desc": "Simple recursive copy over SSH. No delta/resume - fine "
                "for small trees.",
    },
    "sftp": {
        "label": "SFTP (via lftp mirror)", "engine": "lftp", "port": 22,
        "bins": ["lftp"], "secure": True,
        "desc": "lftp mirror -R pushes a whole tree over SFTP with "
                "parallel connections. Key or LFTP_PASSWORD auth.",
    },
    "ftp": {
        "label": "FTP", "engine": "lftp", "port": 21,
        "bins": ["lftp"], "secure": False,
        "desc": "Plaintext classic FTP via lftp. LAN-only at best - "
                "credentials cross the wire in cleartext.",
    },
    "ftps": {
        "label": "FTPS (FTP over TLS)", "engine": "lftp", "port": 990,
        "bins": ["lftp"], "secure": True,
        "desc": "FTP wrapped in TLS via lftp (implicit TLS port 990, "
                "explicit usually 21).",
    },
    "http": {
        "label": "HTTP (PUT via rclone)", "engine": "rclone", "port": 80,
        "bins": ["rclone"], "secure": False,
        "desc": "Directory push over plain HTTP. Uses a configured rclone "
                "'http' remote - enter its name as Host.",
    },
    "https": {
        "label": "HTTPS (PUT via rclone)", "engine": "rclone", "port": 443,
        "bins": ["rclone"], "secure": True,
        "desc": "Directory push over TLS. Uses a configured rclone 'http' "
                "(tls) remote - enter its name as Host.",
    },
    "webdav": {
        "label": "WebDAV", "engine": "rclone", "port": 80,
        "bins": ["rclone"], "secure": False,
        "desc": "WebDAV collection sync via rclone webdav remote - enter "
                "the rclone remote name as Host.",
    },
    "webdavs": {
        "label": "WebDAV over HTTPS", "engine": "rclone", "port": 443,
        "bins": ["rclone"], "secure": True,
        "desc": "Secure WebDAV via rclone (Nextcloud, Apache mod_dav, "
                "most NAS boxes expose this).",
    },
    "smb": {
        "label": "SMB / CIFS share", "engine": "smbclient", "port": 445,
        "bins": ["smbclient"], "secure": True,
        "desc": "Push into a Windows/Samba share. remote_path is "
                "'share/subdir'. Auth file is temp-written, never argv.",
    },
    "nfs": {
        "label": "NFS (mounted export)", "engine": "rsync-local",
        "port": 2049, "bins": ["rsync", "mount.nfs"], "secure": False,
        "desc": "Rsync into an already-mounted NFS export: remote_path "
                "is the LOCAL mountpoint (e.g. /mnt/nfs-share).",
    },
    "rclone": {
        "label": "rclone (any backend)", "engine": "rclone", "port": 0,
        "bins": ["rclone"], "secure": True,
        "desc": "Any of rclone's 40+ backends (S3, B2, Google, SFTP, "
                "Koop...). Host = rclone remote name.",
    },
    "syncthing": {
        "label": "Syncthing (trigger scan)", "engine": "syncthing-api",
        "port": 8384, "bins": ["curl"], "secure": True,
        "desc": "Not a transfer itself: pokes a Syncthing node's REST API "
                "to rescan folder ID (remote_path). Extra args = API key.",
    },
}

# --- remote-access tools (configured, not sync transports) ---
ACCESS_TOOLS = {
    "ssh": {
        "label": "SSH shell", "bins": ["ssh"], "port": 5790, "secure": True,
        "cmd": "ssh -p {port} {user_at}{host}",
        "desc": "The workhorse remote terminal.",
    },
    "mosh": {
        "label": "Mosh", "bins": ["mosh"], "port": 60000, "secure": True,
        "cmd": "mosh --ssh='ssh -p 22' {user_at}{host}",
        "desc": "Roaming/low-latency SSH replacement for laptops.",
    },
    "rdp": {
        "label": "RDP (xrdp / FreeRDP)", "bins": ["xfreerdp", "rdesktop"],
        "port": 3389, "secure": True,
        "cmd": "xfreerdp /v:{host}:{port} /u:{user} /dynamic-resolution",
        "desc": "Full desktop over RDP. Server side = xrdp on the host.",
    },
    "vnc": {
        "label": "VNC (TigerVNC/x11vnc)", "bins": ["vncviewer", "xtigervncviewer"],
        "port": 5900, "secure": False,
        "cmd": "vncviewer {host}:{display}",
        "desc": "Desktop sharing; tunnel over SSH or tailnet only.",
    },
    "x2go": {
        "label": "X2Go", "bins": ["x2goclient"], "port": 22,
        "secure": True,
        "cmd": "x2goclient --host={host} --user={user} --port={port}",
        "desc": "Remote desktop tuned for slow links, rides on SSH.",
    },
    "nx": {
        "label": "NoMachine / NX", "bins": ["nxplayer"], "port": 4000,
        "secure": True,
        "cmd": "nxplayer --session=nx://{user}@{host}:{port}",
        "desc": "Proprietary-but-free accelerated remote desktop.",
    },
    "telnet": {
        "label": "Telnet", "bins": ["telnet"], "port": 23,
        "secure": False,
        "cmd": "telnet {host} {port}",
        "desc": "Legacy plaintext console - troubleshooting only.",
    },
    "tailscale-ssh": {
        "label": "Tailscale SSH (MagicDNS)", "bins": ["tailscale", "ssh"],
        "port": 22, "secure": True,
        "cmd": "ssh {user_at}{host}",
        "desc": "Plain ssh to a MagicDNS name (host.ts.net) - the tailnet "
                "handles the encryption and ACLs.",
    },
}

# Remote/transfer-related systemd units the Remote Services page shows.
# Matched by substring against the unit name, lowercased.
REMOTE_SERVICE_TOKENS = (
    "ssh", "rsync", "ftp", "smb", "nmb", "nfs", "rpcbind", "xrdp",
    "vnc", "spice", "tailscale", "syncthing", "cron", "crond", "atd",
    "apache", "httpd", "nginx", "lighttpd", "caddy", "dav", "webdav",
    "rclone", "x2go", "nxserver", "mosh", "minio", "nextcloud",
    "rustdesk", "cockpit", "webmin",
)


def default_port(protocol: str) -> int:
    return SYNC_PROTOCOLS.get(protocol, {}).get("port", 0)


def tool_installed(bins: list[str]) -> str | None:
    """First binary that exists on PATH, or None."""
    for b in bins:
        if shutil.which(b):
            return b
    return None


# ============================================================
# config store
# ============================================================

DEFAULT_CONFIG = {"pairs": [], "access_profiles": []}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        # Corrupt file: fall back to defaults, never crash the GUI.
        pass
    return cfg


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".cfg-", suffix=".json")[1]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.chmod(tmp, 0o600)  # pairs may carry credentials
    os.replace(tmp, CONFIG_PATH)


def blank_pair(name: str = "New pair") -> dict:
    return {
        "name": name, "enabled": False,
        "source_dir": os.path.expanduser("~/"),
        "protocol": "rsync-ssh", "host": "", "port": 5790, "user": "",
        "password": "", "key_path": "", "remote_path": "",
        "mirror": False, "extra_args": "",
        "schedule": {"mode": "manual", "interval": 30, "cron": ""},
        "last_run": "", "last_status": "never run",
    }


def blank_profile(name: str = "New profile") -> dict:
    return {
        "name": name, "tool": "ssh", "host": "", "port": 22,
        "user": os.environ.get("USER", ""), "extra_args": "",
    }


def record_run(cfg: dict, pair_name: str, ok: bool, detail: str):
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for pair in cfg["pairs"]:
        if pair["name"] == pair_name:
            pair["last_run"] = stamp
            pair["last_status"] = ("OK: " if ok else "FAIL: ") + detail[:200]
            break
    save_config(cfg)


# ============================================================
# command builders
# ============================================================

class BuildError(ValueError):
    """Pair/profile missing required fields - surfaced in the GUI."""


def _require(pair: dict, *keys: str):
    for k in keys:
        if not str(pair.get(k, "")).strip():
            raise BuildError(f"'{k}' is required for protocol "
                            f"'{pair['protocol']}'")


def build_sync_command(pair: dict, dry_run: bool = False) -> dict:
    """Return {'argv': [...], 'env': {...}, 'authfile': str|None}.

    env entries must be merged into the child process environment;
    authfile (if set) must be written to a 0600 tempfile, its path
    replaces the literal '__AUTHFILE__' in argv, and deleted after run.
    """
    proto = pair["protocol"]
    spec = SYNC_PROTOCOLS[proto]
    engine = spec["engine"]
    src = os.path.join(os.path.expanduser(pair["source_dir"].rstrip("/")), "")
    host, port = pair["host"].strip(), pair.get("port", 0)
    user = pair.get("user", "").strip()
    pw = pair.get("password", "")
    key = os.path.expanduser(pair.get("key_path", "").strip()) \
        if pair.get("key_path", "").strip() else ""
    dst = pair.get("remote_path", "").strip()
    mirror = bool(pair.get("mirror"))

    argv: list[str] = []
    env: dict[str, str] = {}
    authfile: str | None = None

    if engine == "rsync-ssh":
        _require(pair, "host", "remote_path")
        ssh = f"ssh -p {port}"
        if key:
            ssh += f" -i {key}"
        argv = ["rsync", "-a", "--info=stats1,progress2"]
        if mirror:
            argv.append("--delete")
        if dry_run:
            argv.append("--dry-run")
        argv += ["-e", ssh, src, f"{user + '@' if user else ''}{host}:{dst}"]

    elif engine == "rsync-daemon":
        _require(pair, "host", "remote_path")
        argv = ["rsync", "-a", f"--port={port}"]
        if mirror:
            argv.append("--delete")
        if dry_run:
            argv.append("--dry-run")
        argv += [src, f"{host}::{dst}"]

    elif engine == "scp":
        _require(pair, "host", "remote_path")
        argv = ["scp", "-r", "-P", str(port)]
        if key:
            argv += ["-i", key]
        argv += [src, f"{user + '@' if user else ''}{host}:{dst}"]

    elif engine == "lftp":
        _require(pair, "host", "remote_path")
        scheme = {"sftp": "sftp", "ftp": "ftp",
                  "ftps": "ftps"}.get(proto, "ftp")
        setcmds = ""
        if scheme == "ftps":
            setcmds = "set ftp:ssl-force true; set ftp:ssl-protect-data true; "
        mirror_cmd = f"mirror -R{' --delete' if mirror else ''}" \
                     f"{' --dry-run' if dry_run else ''} '{src}' '{dst}'"
        url = f"{scheme}://{host}:{port}"
        argv = ["lftp", "--env-password", "-u", user or "anonymous", url,
                "-e", f"{setcmds}{mirror_cmd}; bye"]
        if pw:
            env["LFTP_PASSWORD"] = pw
        else:
            # no password given: rely on key agent (~/.netrc / agent)
            argv[argv.index("--env-password")] = "--norandom"

    elif engine == "rclone":
        _require(pair, "host", "remote_path")
        verb = "sync" if mirror else "copy"
        argv = ["rclone", verb, src.rstrip("/"), f"{host}:{dst}", "-P"]
        if pair.get("extra_args"):
            argv += pair["extra_args"].split()
        if dry_run:
            argv.append("--dry-run")

    elif engine == "smbclient":
        _require(pair, "host", "remote_path")
        if "/" not in dst:
            raise BuildError("remote_path must be 'share/subdir' for SMB")
        share, sub = dst.split("/", 1)
        cmds = ("recurse; prompt; "
                f"lcd '{src.rstrip('/')}'; cd '{sub or '.'}'; mput *")
        argv = ["smbclient", f"//{host}/{share}",
                "-A", "__AUTHFILE__", "-c", cmds]
        if pw:
            authfile = f"username = {user}\npassword = {pw}\ndomain = WORKGROUP"

    elif engine == "rsync-local":  # NFS mountpoint
        _require(pair, "remote_path")
        argv = ["rsync", "-a", "--info=stats1"]
        if mirror:
            argv.append("--delete")
        if dry_run:
            argv.append("--dry-run")
        argv += [src, dst.rstrip("/") + "/"]

    elif engine == "syncthing-api":
        _require(pair, "host", "remote_path")
        if not pair.get("extra_args", "").strip():
            raise BuildError("extra_args must hold the Syncthing API key")
        url = (f"http://{host}:{port}/rest/db/scan"
               f"?folder={dst}")
        argv = ["curl", "-sS", "-H",
                f"Authorization: Bearer {pair['extra_args'].strip()}",
                "-X", "POST", url]

    else:
        raise BuildError(f"Unknown engine '{engine}'")

    if pair.get("extra_args") and engine not in ("rclone", "syncthing-api"):
        argv += pair["extra_args"].split()

    return {"argv": argv, "env": env, "authfile": authfile}


def build_access_command(profile: dict) -> list[str]:
    """argv for a remote-access profile (launch / copy to clipboard)."""
    spec = ACCESS_TOOLS[profile["tool"]]
    host = profile["host"].strip()
    if not host:
        raise BuildError("'host' is required")
    user_at = f"{profile.get('user', '').strip()}@" if profile.get("user", "").strip() else ""
    display = max(0, int(profile.get("port", 5900)) - 5900)  # vnc :N form
    cmd = spec["cmd"].format(host=host,
                             port=profile.get("port", spec["port"]),
                             user=profile.get("user", ""),
                             user_at=user_at, display=display)
    argv = cmd.split()
    if profile.get("extra_args", "").strip():
        argv += profile["extra_args"].split()
    return argv


# ============================================================
# scheduler (crontab managed block)
# ============================================================

def schedule_expr(pair: dict) -> str | None:
    """Cron expression for a pair, or None when it has no schedule."""
    sch = pair.get("schedule", {})
    mode = sch.get("mode", "manual")
    if mode == "manual":
        return None
    if not pair.get("enabled"):
        return None
    if mode == "interval":
        n = max(1, int(sch.get("interval", 30)))
        if n < 60:
            return f"*/{n} * * * *"
        if n < 1440:
            return f"0 */{n // 60} * * *"
        return "0 0 */%d * * *" % max(1, n // 1440)
    if mode == "cron":
        expr = sch.get("cron", "").strip()
        return expr or None
    return None


def crontab_lines(cfg: dict) -> list[str]:
    lines = []
    for pair in cfg.get("pairs", []):
        expr = schedule_expr(pair)
        if expr:
            python = shutil.which("python3") or "/usr/bin/python3"
            logpart = f" >> {CRON_LOG} 2>&1"
            lines.append(
                f"{expr} {python} {RUNNER_PATH} "
                f"--pair \"{pair['name']}\"{logpart}"
            )
    return lines


def read_crontab() -> list[str]:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                       check=False)
    return [] if r.returncode != 0 else r.stdout.splitlines()


def install_crontab(cfg: dict) -> int:
    """Replace ONLY our managed block inside the user crontab.

    Returns the number of schedule lines installed. Raises RuntimeError
    with stderr when the crontab binary is missing or rejects the file.
    """
    ours = crontab_lines(cfg)
    kept = []
    inside = False
    for line in read_crontab():
        if line.strip() == CRON_BEGIN:
            inside = True
            continue
        if line.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            kept.append(line)
    block = []
    if ours:
        block = [CRON_BEGIN, *ours, CRON_END]
    new_content = "\n".join(kept + block).strip("\n") + "\n"
    r = subprocess.run(["crontab", "-"], input=new_content,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "crontab rejected the file").strip())
    return len(ours)


# ============================================================
# environment helpers
# ============================================================

def tailscale_state() -> str:
    """'absent' | 'up' (ip in out) | 'down' | 'broken'."""
    if shutil.which("tailscale") is None and \
            not os.path.isdir("/proc/sys/net/ipv4/conf/tailscale0"):
        return "absent"
    try:
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True,
                           timeout=10, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return "up"
        return "down"
    except (subprocess.TimeoutExpired, OSError):
        return "broken"


def tailscale_ip() -> str:
    try:
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True,
                           timeout=10, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def is_remote_service(unit: str) -> str | None:
    """Category token if this unit belongs on the Remote Services page."""
    low = unit.lower()
    for tok in REMOTE_SERVICE_TOKENS:
        if tok in low:
            return tok
    return None


def validate_pair(pair: dict) -> str:
    """Human-readable problem list ('' when the pair is complete)."""
    problems = []
    src = os.path.expanduser(pair.get("source_dir", ""))
    if not src:
        problems.append("source directory is empty")
    elif not os.path.isdir(src):
        problems.append(f"source directory does not exist: {src}")
    if not pair.get("host") and pair.get("protocol") not in ("nfs",):
        problems.append("host is empty")
    if not pair.get("remote_path"):
        problems.append("remote path is empty")
    return "; ".join(problems)
