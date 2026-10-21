"""Diary entry, physician-only metric assignment and old-record compatibility."""
from datetime import date
import sqlite3

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token


def add_metric(client, pid=1, **changes):
    data = dict(csrf=token(client), title='Энергия', kind='scale', unit='')
    data.update(changes)
    return client.post(f'/patients/{pid}/diary/metrics', data=data)


def save_entry(client, **changes):
    data = dict(csrf=token(client), day=date.today().isoformat(), wellbeing='0',
                sleep='7.5', sleep_quality='10', adherence='missed', note='Тестовая запись')
    data.update(changes)
    return client.post('/patients/1/diary', data=data)


def test_patient_opens_diary_without_home_or_card_controls(client):
    login(client, 'patient1')
    for entry in ('/', '/me', '/me/overview', '/patients/1'):
        assert client.get(entry).url.path == '/patients/1/diary'
    html = client.get('/patients/1/diary').text
    assert 'Главная' not in html and html.count('class="diary-card ') == 3
    assert all(name in html for name in ('wellbeing-dialog', 'sleep-dialog', 'treatment-dialog'))
    settings = client.get('/settings').text
    assert '_pinned' not in settings and '_position' not in settings and '_visible' not in settings
    assert add_metric(client).status_code == 403
    profile = client.get('/patients/1/profile').text
    assert all(f'/static/icons.svg#{name}' in profile for name in ('anthropometry', 'scales', 'people', 'consultation'))


def test_only_assigned_doctor_can_add_metrics_and_custom_answers_persist(client):
    login(client, 'doctor')
    assert add_metric(client).status_code == 200
    with connect(client.app.state.db_path) as c:
        mid = c.execute('SELECT id FROM diary_metrics').fetchone()[0]
    login(client, 'patient1')
    assert f'metric-dialog-{mid}' in client.get('/patients/1/diary').text
    response = save_entry(client, **{f'metric_{mid}':'8'})
    assert response.status_code == 200 and 'качество 10/10' in response.text
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, 'doctor')
        html = restarted.get('/patients/1/diary').text
        assert 'Энергия:</strong> 8' in html and 'Сообщён пропуск' in html
        assert '1 записей с пропуском' in restarted.get('/doctor').text
    login(client, 'patient2')
    assert f'metric-dialog-{mid}' not in client.get('/patients/2/diary').text
    assert client.get('/patients/1/diary').status_code == 403
    login(client, 'doctor')
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой врач')")
        c.execute('UPDATE patients SET doctor_id=2 WHERE id=2')
    assert add_metric(client, 2).status_code == 403


@pytest.mark.parametrize('changes', [dict(title=' '), dict(kind='bad'),
    dict(kind='number',minimum='NaN'), dict(kind='number',minimum='10',maximum='1'),
    dict(kind='boolean',unit='кг'), dict(csrf='wrong')])
def test_invalid_metric_assignment_is_rejected_without_changes(client, changes):
    login(client, 'doctor')
    assert add_metric(client, **changes).status_code in (403, 422)
    with connect(client.app.state.db_path) as c:
        assert c.execute('SELECT count(*) FROM diary_metrics').fetchone()[0] == 0


def test_metric_values_reject_foreign_ids_and_invalid_values_atomically(client):
    login(client, 'doctor')
    assert add_metric(client).status_code == 200
    assert add_metric(client, 2).status_code == 200
    with connect(client.app.state.db_path) as c:
        own, foreign = [r[0] for r in c.execute('SELECT id FROM diary_metrics ORDER BY id')]
    login(client, 'patient1')
    for values in ({f'metric_{foreign}':'7'}, {f'metric_{own}':'11'},
                   {f'metric_{own}':'1.5'}, {f'metric_{own}':'NaN'}, {'sleep_quality':'11'},
                   {'sleep_quality':'NaN'}, {'sleep_quality':'1.5'}, {'sleep_quality':'-1'}):
        assert save_entry(client, **values).status_code == 422
    with connect(client.app.state.db_path) as c:
        assert c.execute('SELECT count(*) FROM diary').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM diary_metric_values').fetchone()[0] == 0


def test_sleep_quality_is_optional_and_never_reinterprets_legacy_hours(client):
    login(client, 'patient1')
    data = dict(csrf=token(client), day=date.today().isoformat(), wellbeing='10', sleep='4.5', adherence='none')
    assert client.post('/patients/1/diary',data=data).status_code == 200
    with connect(client.app.state.db_path) as c:
        row = c.execute('SELECT wellbeing,sleep,sleep_quality FROM diary').fetchone()
        assert tuple(row) == (10,4.5,None)
    assert 'Сон: 4.5 ч' in client.get('/patients/1/diary').text
    assert client.post('/patients/1/diary',data={**data,'sleep_quality':''}).status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute('SELECT count(*) FROM diary WHERE sleep_quality IS NULL').fetchone()[0] == 2


def test_number_boolean_and_text_answers_keep_units_and_escape_markup(client):
    login(client, 'doctor')
    for changes in (dict(title='Вес',kind='number',unit='кг',minimum='1',maximum='500'),
                    dict(title='Прогулка',kind='boolean'), dict(title='Контекст',kind='text')):
        assert add_metric(client, **changes).status_code == 200
    with connect(client.app.state.db_path) as c:
        mids = [r[0] for r in c.execute('SELECT id FROM diary_metrics ORDER BY id')]
    login(client, 'patient1')
    data = {f'metric_{mid}':value for mid,value in zip(mids,('65.2','false','<script>sample</script>'))}
    response = save_entry(client, **data)
    assert response.status_code == 200
    assert '65.2 кг' in response.text and 'Прогулка:</strong> Нет' in response.text
    assert '&lt;script&gt;sample&lt;/script&gt;' in response.text
