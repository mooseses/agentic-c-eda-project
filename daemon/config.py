LOG_FILES = ["/var/log/syslog", "/var/log/auth.log"]
NETWORK_TAG = "[Agent]"

IGNORED_PORTS_DEFAULT = {
    "80", "443", "22", "53", "3389", "5432", "6379"
}


IGNORED_PORTS = IGNORED_PORTS_DEFAULT.copy()
IGNORED_PORTS.update({
    "5353", "5355", "1900", "137", "138", "67", "68",
    "32410", "32412", "32414", "17500"
})


IGNORED_IPS = {"127.0.0.1", "0.0.0.0"}

LLM_API_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "qwen/qwen3-4b-2507"
LLM_TIMEOUT = 10
AGENT_SENSITIVITY = 5

DATABASE_PATH = "/var/lib/agentic-c-eda/agentic-c-eda.db"
DATABASE_RETENTION_DAYS = 7

TRUSTED_INTERNAL_PORTS = set()
TRUSTED_SERVICES = {}
INTERNAL_SUBNET = "10.0.0."

MANUAL_TRUSTED_PORTS = {
    22, 80, 443, 1234, 3389, 8080, 9000,
    24800, 1716, 27036, 27060
}