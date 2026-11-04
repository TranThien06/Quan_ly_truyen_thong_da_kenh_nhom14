"""Tương thích bcrypt, SHA-256 và layout AES-GCM của backend Express cũ."""

import base64
import hashlib
import os
import secrets
import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def token():
    return secrets.token_urlsafe(32)


def hash_token(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def hash_password(value):
    return bcrypt.hashpw(value.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(value, stored):
    try:
        return bcrypt.checkpw(value.encode(), stored.encode())
    except (ValueError, TypeError):
        return False


def encryption_key():
    try:
        value = base64.b64decode(os.getenv("TOKEN_ENCRYPTION_KEY", ""), validate=True)
    except ValueError:
        value = b""
    if len(value) != 32:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY phải là khóa 32 byte mã hóa base64")
    return value


def encrypt(value):
    if value is None:
        return None
    iv = os.urandom(12)
    encrypted = AESGCM(encryption_key()).encrypt(iv, value.encode(), None)
    # Node lưu IV(12) + tag(16) + ciphertext; cryptography trả ciphertext + tag.
    return iv + encrypted[-16:] + encrypted[:-16]


def decrypt(value):
    if value is None:
        return None
    value = bytes(value)
    return AESGCM(encryption_key()).decrypt(value[:12], value[28:] + value[12:28], None).decode()
