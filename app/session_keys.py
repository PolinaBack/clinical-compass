"""One private signing key per database, shared by workers and process restarts."""

import os
from pathlib import Path
import re
import secrets
import tempfile


def session_key_path(db_path):
    configured = os.getenv("COMPASS_SESSION_KEY_FILE")
    if configured is not None:
        if not configured.strip():
            raise RuntimeError("COMPASS_SESSION_KEY_FILE не должен быть пустым")
        return Path(configured).expanduser().resolve()
    return Path(str(Path(db_path).resolve()) + ".session.key")


def _read_key(path):
    # Never log contents or silently replace a malformed, already published key.
    with path.open("rb") as f:
        value = f.read(66)
    if not re.fullmatch(rb"[0-9a-f]{64}\n?", value):
        raise RuntimeError(
            f"Некорректный ключ сеансов в {path}. Проверьте файл; он не перезаписан."
        )
    return value.decode("ascii").strip()


def session_secret(db_path):
    configured = os.getenv("COMPASS_SESSION_SECRET")
    if configured is not None:
        if not 32 <= len(configured) <= 4096 or configured != configured.strip():
            raise RuntimeError(
                "COMPASS_SESSION_SECRET должен содержать 32–4096 символов без пробелов по краям"
            )
        return configured
    path = session_key_path(db_path)
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass
    except OSError:
        raise RuntimeError(f"Не удалось прочитать ключ сеансов {path}") from None
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".session-key-", dir=path.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_hex(32).encode("ascii") + b"\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            # Publish an already complete file without replacing another worker's
            # winner. NTFS and local Linux filesystems support this operation.
            os.link(temporary, path)
        except FileExistsError:
            pass
        return _read_key(path)
    except OSError:
        raise RuntimeError(
            f"Не удалось подготовить общий ключ сеансов {path}. "
            "Проверьте права и поддержку жёстких ссылок либо задайте одинаковый "
            "COMPASS_SESSION_SECRET для всех процессов. Случайный ключ процесса не используется."
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
