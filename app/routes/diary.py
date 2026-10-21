from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.services.diary import metric_definition
from app.storage import audit, now


def register_diary(app, db, account, access, csrf_check):
    @app.post('/patients/{pid}/diary/metrics')
    async def add_metric(request: Request, pid: int):
        if access(request, pid) != 'doctor':
            raise HTTPException(403, 'Добавлять показатели может только ведущий специалист')
        form = await request.form()
        csrf_check(request, str(form.get('csrf', '')))
        definition = metric_definition(form)
        user = account(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT id FROM patients WHERE id=? AND doctor_id=?', (pid, user['doctor_id'])).fetchone():
                raise HTTPException(403, 'Пациент больше не закреплён за этим специалистом')
            mid = c.execute(
                '''INSERT INTO diary_metrics(patient_id,title,kind,unit,minimum,maximum,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)''', (pid, *definition, user['id'], now()),
            ).lastrowid
            audit(c, user['id'], 'diary_metric_added', pid, mid)
        return RedirectResponse(f'/patients/{pid}/diary#assigned-metrics', status_code=303)
