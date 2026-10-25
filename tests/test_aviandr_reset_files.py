"""Windows replacement failures must not turn a reset into a partial deletion."""

import errno
import hashlib
from pathlib import Path
import sqlite3
import stat
import sys

from cryptography.fernet import Fernet
import pytest

from app.storage import connect
from scripts import setup_aviandr_scenarios as module
from tests.helpers import login


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replacement_files(tmp_path):
    old, fresh = tmp_path / "compass.db", tmp_path / "fresh.db"
    for path, value in ((old, "OLD-SYNTHETIC"), (fresh, "NEW-SYNTHETIC")):
        with connect(path) as c:
            c.execute("CREATE TABLE sample(value TEXT)")
            c.execute("INSERT INTO sample VALUES(?)", (value,))
    return old, fresh, fingerprint(old)


@pytest.mark.parametrize("code", [5, 32])
def test_transient_windows_denial_retries_atomic_replace(tmp_path, monkeypatch, code):
    old, fresh, previous = replacement_files(tmp_path)
    replace = module.os.replace
    calls, sleeps = [], []

    def temporarily_denied(source, target):
        calls.append((source, target))
        assert fingerprint(old) == previous
        if len(calls) < 3:
            error = PermissionError(errno.EACCES, "Synthetic Windows denial")
            error.winerror = code
            raise error
        replace(source, target)

    monkeypatch.setattr(module.os, "replace", temporarily_denied)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    module.replace_database(fresh, old, previous, tmp_path / "backup.db")
    assert len(calls) == 3 and sleeps == [0.2, 0.4]
    with connect(old) as c:
        assert c.execute("SELECT value FROM sample").fetchone()[0] == "NEW-SYNTHETIC"
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_permanent_lock_cli_preserves_db_key_backup_and_closed_connections(
    client, monkeypatch, capsys
):
    login(client, "doctor")
    path = client.app.state.db_path.resolve()
    original = fingerprint(path)
    key = Path(str(path) + ".key")
    key.write_bytes(Fernet.generate_key())
    original_key = key.read_bytes()
    connections, attempts = [], []
    sqlite_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        connection = sqlite_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def deny_replace(source, target):
        assert connections and all(c.closed for c in connections)
        assert fingerprint(path) == original
        attempts.append(1)
        raise PermissionError(errno.EACCES, "Synthetic locked destination")

    monkeypatch.setattr(module.sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(module.os, "replace", deny_replace)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sys, "argv", ["setup_aviandr_scenarios", "--db", str(path), "--reset"]
    )
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert not output.out
    assert "Traceback" not in output.err
    assert "Disconnect" in output.err and "Прежняя база не заменена" in output.err
    assert len(attempts) == 5
    assert fingerprint(path) == original and key.read_bytes() == original_key
    backups = list((path.parent / "backups").glob("before-aviandr-reset-*.db"))
    assert len(backups) == 1 and str(backups[0]) in output.err
    with connect(backups[0]) as c:
        assert c.execute("SELECT count(*) FROM accounts").fetchone()[0] == 3
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert all(c.closed for c in connections)
    assert not list(path.parent.glob(".aviandr-reset-*"))


def test_windows_readonly_destination_is_made_writable_for_replace(
    tmp_path, monkeypatch
):
    old, fresh, previous = replacement_files(tmp_path)
    old.chmod(stat.S_IREAD)
    replace = module.os.replace

    def verify_writable(source, target):
        assert target.stat().st_mode & stat.S_IWRITE
        replace(source, target)

    monkeypatch.setattr(module, "WINDOWS", True)
    monkeypatch.setattr(module.os, "replace", verify_writable)
    try:
        module.replace_database(fresh, old, previous, tmp_path / "backup.db")
        assert fingerprint(old) != previous
    finally:
        old.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_failed_replace_restores_original_readonly_flag(tmp_path, monkeypatch):
    old, fresh, previous = replacement_files(tmp_path)
    old.chmod(stat.S_IREAD)
    mode = old.stat().st_mode
    monkeypatch.setattr(module, "WINDOWS", True)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def deny(*args):
        raise PermissionError(errno.EACCES, "Synthetic persistent lock")

    monkeypatch.setattr(module.os, "replace", deny)
    try:
        with pytest.raises(ValueError, match="Прежняя база не заменена"):
            module.replace_database(fresh, old, previous, tmp_path / "backup.db")
        assert old.stat().st_mode == mode and fingerprint(old) == previous
        assert fresh.is_file()
    finally:
        old.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("change", ["write", "wal", "new_file"])
def test_changes_during_retry_abort_instead_of_overwriting(
    tmp_path, monkeypatch, change
):
    old, fresh, previous = replacement_files(tmp_path)
    if change == "new_file":
        old.unlink()
        previous = None
    calls = []

    def denied(*args):
        calls.append(1)
        raise PermissionError(errno.EACCES, "Synthetic temporary lock")

    def another_process_saved(_):
        if change == "wal":
            Path(str(old) + "-wal").write_bytes(b"SYNTHETIC ACTIVE WAL")
        elif change == "new_file":
            old.write_bytes(b"OTHER PROCESS CREATED A FILE")
        else:
            with connect(old) as c:
                c.execute("INSERT INTO sample VALUES('OTHER SYNTHETIC WRITE')")

    monkeypatch.setattr(module.os, "replace", denied)
    monkeypatch.setattr(module.time, "sleep", another_process_saved)
    with pytest.raises(ValueError, match="(изменилась|активный WAL)"):
        module.replace_database(fresh, old, previous, tmp_path / "backup.db")
    assert len(calls) == 1 and fresh.is_file()
    if change == "write":
        with connect(old) as c:
            assert c.execute("SELECT count(*) FROM sample").fetchone()[0] == 2


def test_non_lock_io_error_does_not_retry_or_delete_destination(tmp_path, monkeypatch):
    old, fresh, previous = replacement_files(tmp_path)
    attempts = []

    def full_disk(*args):
        attempts.append(1)
        raise OSError(errno.ENOSPC, "Synthetic disk full")

    monkeypatch.setattr(module.os, "replace", full_disk)
    monkeypatch.setattr(module.time, "sleep", lambda _: pytest.fail("Unexpected retry"))
    with pytest.raises(ValueError, match="свободное место"):
        module.replace_database(fresh, old, previous, tmp_path / "backup.db")
    assert attempts == [1] and fingerprint(old) == previous and fresh.is_file()


def test_preparation_os_error_has_a_concise_cli_message(monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionError("Synthetic unwritable directory")

    monkeypatch.setattr(module, "setup", denied)
    monkeypatch.setattr(sys, "argv", ["setup_aviandr_scenarios", "--reset"])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert not output.out and "Traceback" not in output.err
    assert "проверьте доступ к файлам" in output.err
