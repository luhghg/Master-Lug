import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings

_fernet = Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_token(token: str) -> str:
    """Encrypt a plain bot token for storage."""
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored bot token."""
    return _fernet.decrypt(encrypted.encode()).decode()


def hash_token(token: str) -> str:
    """SHA-256 hash used as an indexed lookup key (never stored plain)."""
    return hashlib.sha256(token.encode()).hexdigest()
