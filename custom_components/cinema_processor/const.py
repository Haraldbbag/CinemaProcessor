"""Constants for the cinema processor integration."""

DOMAIN = "cinema_processor"

CONF_MODEL = "model"
MODEL_CP750 = "cp750"
MODEL_CP850 = "cp850"
MODEL_CP950 = "cp950"
MODEL_NAMES = {MODEL_CP750: "CP750", MODEL_CP850: "CP850", MODEL_CP950: "CP950 / CP950A"}
ATMOS_MODELS = (MODEL_CP850, MODEL_CP950)

DEFAULT_PORT = 61408  # ASCII control (Telnet-style) on all Dolby processors

CONF_WEB_USERNAME = "web_username"
CONF_WEB_PASSWORD = "web_password"
DEFAULT_WEB_USERNAME = "admin"
DEFAULT_WEB_PASSWORD = "admin"  # Dolby factory default

CONF_SCAN_INTERVAL = "scan_interval"
CONF_READ_ONLY = "read_only"
CONF_MACROS = "macros"
CONF_INPUTS = "inputs"  # CP750 input names

DEFAULT_SCAN_INTERVAL = 5  # seconds
REFRESH_DELAY = 0.7  # seconds after a command before reading back
