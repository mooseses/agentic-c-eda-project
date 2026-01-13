import subprocess
import logging
import sys
from config import NETWORK_TAG

ENFORCEMENT_ENABLED = False


class FirewallController:

    def __init__(self):
        self.sensor_active = False

    def enable_sensor(self):
        logging.info("[-] Setting up network sensor...")
        
        rule_args = [
            "!", "-i", "lo",
            "-m", "conntrack", "--ctstate", "NEW",
            "-j", "LOG", "--log-prefix", f"{NETWORK_TAG} ", "--log-level", "4"
        ]
        
        check_cmd = ["sudo", "iptables", "-C", "INPUT"] + rule_args
        install_cmd = ["sudo", "iptables", "-I", "INPUT", "1"] + rule_args

        try:
            if subprocess.run(check_cmd, capture_output=True).returncode != 0:
                subprocess.run(install_cmd, check=True)
                self.sensor_active = True
                logging.info("[+] Sensor installed: Capturing NEW connections")
            else:
                logging.info("[*] Sensor already active")
        except subprocess.CalledProcessError as e:
            logging.error(f"[!] Failed to setup sensor: {e}")
            sys.exit(1)

    def disable_sensor(self):
        if not self.sensor_active:
            return
            
        logging.info("[-] Removing network sensor...")
        rule_args = [
            "!", "-i", "lo",
            "-m", "conntrack", "--ctstate", "NEW",
            "-j", "LOG", "--log-prefix", f"{NETWORK_TAG} "
        ]
        subprocess.run(
            ["sudo", "iptables", "-D", "INPUT"] + rule_args,
            stderr=subprocess.DEVNULL
        )
        logging.info("[+] Sensor removed")

    def log_block(self, ip: str, reason: str):
        logging.warning(f"    [WOULD BLOCK] {ip} - {reason}")
        if ENFORCEMENT_ENABLED:
            subprocess.run(["sudo", "iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"])
            logging.warning(f"    [BLOCKED] {ip}")