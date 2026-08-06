"""Symmetric encryption for secrets stored at rest (Plaid access tokens)."""

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import settings


@lru_cache
def _cipher() -> Fernet:
    if not settings.encryption_key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.encryption_key.encode())


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _cipher().decrypt(ciphertext.encode()).decode()
