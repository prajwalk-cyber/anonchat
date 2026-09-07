import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
THUMBNAIL_DIR = UPLOAD_DIR / "thumbs"
THUMBNAIL_DIR.mkdir(exist_ok=True)

DB_PATH = BASE_DIR / "chat.db"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))

# Secure admin token: Load from persistent .admin_token file or generate cryptographically secure 256-bit token
SECRET_FILE = BASE_DIR / ".admin_token"
def _get_admin_token() -> str:
    env_token = os.getenv("ADMIN_TOKEN_SECRET")
    if env_token and env_token != "anonchat-super-secret-admin-key-2026":
        return env_token
    if SECRET_FILE.exists():
        try:
            tok = SECRET_FILE.read_text(encoding="utf-8").strip()
            if len(tok) >= 32:
                return tok
        except Exception:
            pass
    import secrets
    new_tok = secrets.token_urlsafe(32)
    try:
        SECRET_FILE.write_text(new_tok, encoding="utf-8")
        SECRET_FILE.chmod(0o600)
    except Exception:
        pass
    return new_tok

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "prajwal@peace")
ADMIN_TOKEN_SECRET = _get_admin_token()

# Upload configuration (Max 8MB for performance with 500 users)
MAX_UPLOAD_SIZE = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Capacity and Rate Limits for 1000+ concurrent users
MAX_MESSAGE_LENGTH = 1000
MAX_CONNECTIONS_PER_IP = 150  # Increased for campus Wi-Fi / NAT where many users share 1 IP
MAX_TOTAL_CONNECTIONS = 1500  # Supports 1000+ concurrent users with headroom
RATE_LIMIT_POST_SEC = 1.0
RATE_LIMIT_UPLOAD_PER_MIN = 5
RATE_LIMIT_LOGIN_ATTEMPTS = 5
RATE_LIMIT_LOGIN_LOCKOUT = 300

# Board Metadata
BOARD_TITLE = "/anon/ - Pesitm Anonymous Board"
BOARD_SUBTITLE = "Publicly Anonymous • Real-Time Chat"

