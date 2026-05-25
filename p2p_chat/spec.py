import os
import socket


def get_lan_ip() -> str:
    """Best-effort LAN IP used only for display/help text."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


HOST = os.getenv("P2P_HOST", "127.0.0.1")
PORT = int(os.getenv("P2P_PORT", "55555"))
P2P_BASE_PORT = int(os.getenv("P2P_BASE_PORT", "8000"))

ADDRESS = HOST, PORT
