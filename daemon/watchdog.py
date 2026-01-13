import re
import os
import time
from datetime import datetime
import config
from config import LOG_FILES, NETWORK_TAG, IGNORED_PORTS

RE_SRC = re.compile(r'SRC=([\d.]+)')
RE_DPT = re.compile(r'DPT=(\d+)')
RE_PROTO = re.compile(r'PROTO=(\w+)')
RE_IP_FROM = re.compile(r'from ([\d.]+)')
RE_USER = re.compile(r'for (\w+)')
RE_SYSLOG_TIME = re.compile(r'^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})')

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
}

NOISE_PATTERNS = (
    "apparmor=",
    "audit:",
    "IN=lo",
    "DST=224.0.0.251",
    "DST=255.255.255.255",
    "systemd-logind",
    "CRON",
)

class LogWatchdog:

    def __init__(self, log_files: list = None, db=None):
        self.log_files = log_files if log_files is not None else LOG_FILES
        self.db = db
        self.file_handles = {}
        self.file_positions = {}
        self.file_inodes = {}
        self.start_time = None
        self.pending_lines = []

        self.ignored_ports = set(IGNORED_PORTS)
        self.ignored_ips = set()
        self._load_ignored_lists()

    def _load_ignored_lists(self):

        if not self.db:
            return

        try:

            ports_config = self.db.get_config("ignored_ports", "")
            if ports_config:
                db_ports = set(p.strip() for p in ports_config.split('\n') if p.strip())
                self.ignored_ports = set(IGNORED_PORTS) | db_ports

            ips_config = self.db.get_config("ignored_ips", "")
            if ips_config:
                self.ignored_ips = set(ip.strip() for ip in ips_config.split('\n') if ip.strip())
        except Exception:
            pass

    def refresh_ignored_lists(self):

        self._load_ignored_lists()

    def start_stream(self):

        self.start_time = datetime.now()

        for filepath in self.log_files:
            if not os.path.exists(filepath):
                continue
            try:
                fh = open(filepath, 'r')
                fh.seek(0, os.SEEK_END)
                self.file_handles[filepath] = fh
                self.file_positions[filepath] = fh.tell()
                self.file_inodes[filepath] = os.stat(filepath).st_ino
            except (IOError, OSError) as e:
                print(f"[!] Cannot open {filepath}: {e}")

    def stop_stream(self):

        for fh in self.file_handles.values():
            try:
                fh.close()
            except Exception:
                pass
        self.file_handles.clear()
        self.file_positions.clear()
        self.file_inodes.clear()

    def __del__(self):
        self.stop_stream()

    def _check_rotation(self, filepath: str) -> bool:

        try:
            current_inode = os.stat(filepath).st_ino
            if current_inode != self.file_inodes.get(filepath):
                if filepath in self.file_handles:
                    self.file_handles[filepath].close()
                fh = open(filepath, 'r')
                self.file_handles[filepath] = fh
                self.file_positions[filepath] = 0
                self.file_inodes[filepath] = current_inode
                return True
        except (IOError, OSError):
            pass
        return False

    def _parse_log_time(self, line: str) -> datetime | None:

        match = RE_SYSLOG_TIME.match(line)
        if not match:
            return None
        try:
            return datetime(
                datetime.now().year,
                MONTHS.get(match.group(1), 1),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5))
            )
        except (ValueError, KeyError):
            return None

    def _is_noise(self, line: str) -> bool:

        for pattern in NOISE_PATTERNS:
            if pattern in line:
                return True

        dpt_match = RE_DPT.search(line)
        if dpt_match and dpt_match.group(1) in self.ignored_ports:
            return True

        src_match = RE_SRC.search(line)
        if src_match and src_match.group(1) in self.ignored_ips:
            return True

        return False

    def _is_trusted_internal(self, line: str) -> bool:

        src_match = RE_SRC.search(line)
        dpt_match = RE_DPT.search(line)

        if src_match and dpt_match:
            src_ip = src_match.group(1)
            port = int(dpt_match.group(1))
            is_internal = src_ip.startswith(config.INTERNAL_SUBNET)
            is_trusted = port in config.TRUSTED_INTERNAL_PORTS
            if is_internal and is_trusted:
                return True
        return False

    def _parse(self, line: str) -> str | None:

        if NETWORK_TAG in line:
            src = RE_SRC.search(line)
            if not src:
                return None
            if "PROTO=ICMP" in line:
                return f"NET_PING Source={src.group(1)}"
            dpt = RE_DPT.search(line)
            proto = RE_PROTO.search(line)
            if dpt:
                return f"NET_CONN Source={src.group(1)} Port={dpt.group(1)} Proto={proto.group(1) if proto else '?'}"

        if "sshd" in line and "Failed password" in line:
            ip = RE_IP_FROM.search(line)
            user = RE_USER.search(line)
            return f"SSH_AUTH_FAIL User={user.group(1) if user else 'unknown'} Source={ip.group(1) if ip else 'unknown'} Method=password"

        if "sshd" in line and "Accepted" in line:
            ip = RE_IP_FROM.search(line)
            user = RE_USER.search(line)
            method = "key" if "publickey" in line else "password"
            return f"SSH_AUTH_SUCCESS User={user.group(1) if user else 'unknown'} Source={ip.group(1) if ip else 'unknown'} Method={method}"

        if "sshd" in line and "Invalid user" in line:
            ip = RE_IP_FROM.search(line)
            user_match = re.search(r'Invalid user (\w+)', line)
            return f"SSH_INVALID_USER User={user_match.group(1) if user_match else 'unknown'} Source={ip.group(1) if ip else 'unknown'}"

        if "sshd" in line and "Connection closed" in line:
            ip_match = re.search(r'([\d.]+) port', line)
            user_match = re.search(r'user ([\w-]+)', line)
            return f"SSH_CONNECTION_CLOSED User={user_match.group(1) if user_match else 'unknown'} Source={ip_match.group(1) if ip_match else 'unknown'}"

        if "sudo:" in line and "COMMAND=" in line:
            user_match = re.search(r'sudo: (\w+) :', line)
            cmd_match = re.search(r'COMMAND=(.+)$', line)
            tty_match = re.search(r'TTY=([^;]+)', line)
            tty = tty_match.group(1) if tty_match else "unknown"
            session_type = "SSH" if "pts" in tty else "LOCAL" if "tty" in tty else "CRON"
            return f"SUDO_EXEC User={user_match.group(1) if user_match else 'unknown'} Session={session_type} TTY={tty} Command={cmd_match.group(1) if cmd_match else 'unknown'}"

        if "sudo" in line and "authentication failure" in line:
            user_match = re.search(r'logname=(\w+)', line)
            tty_match = re.search(r'tty=([^;]+)', line)
            tty = tty_match.group(1) if tty_match else "unknown"
            session_type = "SSH" if "pts" in tty else "LOCAL" if "tty" in tty else "UNKNOWN"
            return f"SUDO_AUTH_FAIL User={user_match.group(1) if user_match else 'unknown'} Session={session_type} TTY={tty}"

        if "session opened" in line and "pam_unix" in line:
            user_match = re.search(r'for user (\w+)', line)
            service_match = re.search(r'pam_unix\((\w+)', line)
            if service_match and service_match.group(1) not in ('sudo', 'cron'):
                return f"SESSION_OPEN Service={service_match.group(1)} User={user_match.group(1) if user_match else 'unknown'}"

        if "session closed" in line and "pam_unix" in line:
            user_match = re.search(r'for user (\w+)', line)
            service_match = re.search(r'pam_unix\((\w+)', line)
            if service_match and service_match.group(1) not in ('sudo', 'cron'):
                return f"SESSION_CLOSE Service={service_match.group(1)} User={user_match.group(1) if user_match else 'unknown'}"

        return None

    def _read_new_lines(self):

        for filepath, fh in list(self.file_handles.items()):
            self._check_rotation(filepath)
            fh = self.file_handles.get(filepath)
            if not fh:
                continue

            try:
                fh.seek(self.file_positions[filepath])
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue

                    log_time = self._parse_log_time(line)
                    if log_time and self.start_time and log_time < self.start_time:
                        continue

                    if self._is_noise(line) or self._is_trusted_internal(line):
                        continue

                    event = self._parse(line)
                    if event:
                        self.pending_lines.append(event)

                self.file_positions[filepath] = fh.tell()
            except (IOError, OSError):
                pass

    def read_stream(self) -> str | None:

        if self.pending_lines:
            return self.pending_lines.pop(0)

        self._read_new_lines()

        if self.pending_lines:
            return self.pending_lines.pop(0)

        time.sleep(0.1)
        return None