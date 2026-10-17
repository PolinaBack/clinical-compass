import sqlite3
import pytest
from scripts.backup import backup


def test_backup_preserves_data_and_refuses_overwrite(tmp_path):
    source, target = tmp_path/'source.db', tmp_path/'backup.db'
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE synthetic(value TEXT)')
        c.execute('INSERT INTO synthetic VALUES(?)', ('demo',))
    backup(source,target)
    with sqlite3.connect(target) as c:
        assert c.execute('SELECT value FROM synthetic').fetchone()[0]=='demo'
    with pytest.raises(ValueError):
        backup(source,target)
    with pytest.raises(ValueError):
        backup(tmp_path/'missing.db',tmp_path/'other.db')
