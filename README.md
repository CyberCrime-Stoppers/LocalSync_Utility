# LocalSync_Utility

**Linux local-network file sync utility** — automatic directory syncing
between your machines over the LAN and/or Tailscale, with a GUI for
configuring every aspect of it.

Part of the SereneForge toolset; shares the Serene green/purple theme
conventions with the SereneOS Control Panel (same flat layout, same
plain-Python-core + PySide6-GUI split, same status-bar UX).

---

## What it does

- **Sync Pairs** — each pair is one local source directory pushed to a
  remote destination directory, with its own protocol, credentials,
  mirror behaviour and schedule (manual / every N minutes / cron).
- **Remote Access profiles** — launch commands for SSH, Mosh, RDP,
  VNC, X2Go, NoMachine/NX, Telnet and Tailscale SSH.
- **Remote Services** — filtered systemd view showing *only*
  remote/transfer services (sshd, rsyncd, vsftpd, smbd, nfs-server,
  xrdp, tailscaled, syncthing, cron, web servers…), with
  start/stop/restart/enable via pkexec.
- **Tools** — catalogue of every supported protocol/tool with
  installed-binary detection.

## Run

```bash
# GUI
python3 main.py          # or: python3 localsync_app.py

# Runner (what cron calls)
python3 localsync_runner.py --list
python3 localsync_runner.py --all
python3 localsync_runner.py --pair "Documents"
```

Dependencies: `python3-pyside6` (KDE Neon/Ubuntu) or `pyside6` (Arch),
plus whatever sync tools your pairs use — `rsync`, `lftp`, `rclone`,
`smbclient`, `curl`.

Config lives at `~/.config/LocalSync_Utility/config.json` (0600, since
pairs may hold credentials). Cron output logs to
`~/.config/LocalSync_Utility/localsync-cron.log`.

## Supported sync transports

| Protocol | Engine | Default port | Secure |
|---|---|---|---|
| rsync over SSH | rsync/ssh | 22 | ✔ |
| rsync daemon (rsyncd) | rsync module | 873 | ✘ (LAN/tailnet only) |
| SCP | scp | 22 | ✔ |
| SFTP (lftp mirror) | lftp | 22 | ✔ |
| FTP | lftp | 21 | ✘ |
| FTPS (FTP over TLS) | lftp | 990 | ✔ |
| HTTP (rclone PUT) | rclone | 80 | ✘ |
| HTTPS (rclone PUT) | rclone | 443 | ✔ |
| WebDAV / WebDAVS | rclone | 80/443 | ✔ (webdavs) |
| SMB / CIFS share | smbclient | 445 | ✔ |
| NFS (mounted export) | rsync → mountpoint | 2049 | ✘ |
| rclone (any backend) | rclone | — | backend-dependent |
| Syncthing (trigger scan) | REST API | 8384 | ✔ |

## Supported remote access tools

SSH · Mosh · RDP (xrdp/FreeRDP) · VNC · X2Go · NoMachine/NX · Telnet ·
Tailscale SSH (MagicDNS)

## How scheduling works

Enabling a pair with an interval (e.g. every 30 min) or a cron
expression and pressing **Apply cron schedule** writes a managed block
into your user crontab:

```
# localsync-begin (managed block - do not edit inside)
*/30 * * * * /usr/bin/python3 /path/to/localsync_runner.py --pair "Documents" >> ~/.config/LocalSync_Utility/localsync-cron.log 2>&1
# localsync-end
```

Only the managed block is ever touched — your other crontab entries are
preserved verbatim. Manual pairs are run from the GUI ("▶ Run now") or
with `localsync_runner.py --pair NAME`.

## Security notes

- SSH-key auth is preferred; passwords are never placed in argv.
- lftp passwords travel via the `LFTP_PASSWORD` env var (`--env-password`).
- smbclient credentials go through a 0600 tempfile (`-A`), deleted after
  each run.
- The config file itself is written 0600.

## License

See `LICENSE`.
