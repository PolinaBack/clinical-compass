"""Local account passwords: PBKDF2 hashes, never default credentials."""

import hashlib
import secrets


def hash_password(password):
    if len(password) < 10 or len(password) > 256:
        raise ValueError("Пароль должен содержать от 10 до 256 символов")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 310000
    ).hex()
    return f"pbkdf2_sha256$310000${salt}${digest}"


def verify_password(password, stored):
    try:
        algorithm, rounds, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256" or len(password) > 256:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
        ).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False
