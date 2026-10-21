CREATE TABLE meeting_sessions(
 id INTEGER PRIMARY KEY, appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointments(id),
 patient_id INTEGER NOT NULL REFERENCES patients(id), context_id TEXT NOT NULL UNIQUE REFERENCES consultation_consent_contexts(id),
 created_by INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL
);
CREATE TABLE meeting_recordings(
 id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meeting_sessions(id),
 filename TEXT NOT NULL, mime TEXT NOT NULL, duration REAL NOT NULL, sha256 TEXT NOT NULL,
 content BLOB, created_by INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX meeting_recording_identity ON meeting_recordings(meeting_id,sha256) WHERE deleted_at IS NULL;
CREATE TABLE meeting_jobs(
 id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL REFERENCES meeting_recordings(id),
 kind TEXT NOT NULL CHECK(kind IN ('transcript','summary')), source_id INTEGER,
 job_key TEXT NOT NULL UNIQUE, consent_stamp TEXT NOT NULL, config_stamp TEXT NOT NULL,
 requested_by INTEGER NOT NULL REFERENCES accounts(id), status TEXT NOT NULL DEFAULT 'queued'
 CHECK(status IN ('queued','running','done','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT, worker_token TEXT,
 error_code TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX meeting_jobs_queue ON meeting_jobs(status,id);
CREATE TABLE meeting_artifacts(
 id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL REFERENCES meeting_recordings(id),
 kind TEXT NOT NULL CHECK(kind IN ('transcript','summary')), source_id INTEGER REFERENCES meeting_artifacts(id),
 job_id INTEGER UNIQUE REFERENCES meeting_jobs(id), author_id INTEGER REFERENCES accounts(id),
 provider TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE meeting_events(
 id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL REFERENCES meeting_recordings(id),
 artifact_id INTEGER REFERENCES meeting_artifacts(id), account_id INTEGER REFERENCES accounts(id),
 action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TRIGGER meeting_events_no_update BEFORE UPDATE ON meeting_events BEGIN SELECT RAISE(ABORT,'append-only meeting history'); END;
CREATE TRIGGER meeting_events_no_delete BEFORE DELETE ON meeting_events BEGIN SELECT RAISE(ABORT,'append-only meeting history'); END;
CREATE TRIGGER meeting_artifacts_no_delete BEFORE DELETE ON meeting_artifacts BEGIN SELECT RAISE(ABORT,'preserve artifact provenance'); END;
CREATE TRIGGER meeting_artifacts_immutable BEFORE UPDATE ON meeting_artifacts
WHEN OLD.deleted_at IS NOT NULL OR NEW.payload IS NOT NULL OR NEW.deleted_at IS NULL
 OR NEW.id!=OLD.id OR NEW.recording_id!=OLD.recording_id OR NEW.kind!=OLD.kind
 OR NEW.source_id IS NOT OLD.source_id OR NEW.job_id IS NOT OLD.job_id OR NEW.author_id IS NOT OLD.author_id
 OR NEW.provider!=OLD.provider OR NEW.created_at!=OLD.created_at
BEGIN SELECT RAISE(ABORT,'only explicit redaction is allowed'); END;

INSERT INTO consent_documents(purpose,version,title,body,retention,created_at) VALUES
('audio_recording','meeting-local-v2-2026-10-21','Аудиозапись консультации',
'Разрешаю загрузить и хранить аудиозапись этой консультации на компьютере/сервере, где запущен Клинический компас. Доступ имеют участники по их правам. Все участники должны дать отдельное согласие. Это не разрешение на видеозапись, расшифровку или сводку. Сам звонок приложение автоматически не записывает.',
'Новая загруженная запись и её производные доступны до 30 дней после загрузки или до явного удаления. Отзыв запрещает дальнейшую обработку, но не удаляет уже созданное автоматически. Резервные копии удаляются отдельно по регламенту владельца сервера. Используйте синтетические данные до определения оператора и клинического допуска.','2026-10-21T00:00:00Z'),
('video_recording','meeting-local-v2-2026-10-21','Видеозапись консультации',
'Разрешаю загрузить и хранить видеозапись этой консультации на компьютере/сервере Клинического компаса. Все участники подтверждают разрешение отдельно. Извлечение звука для ИИ требует отдельного согласия на расшифровку. Приложение автоматически не включает камеру и не записывает звонок.',
'Новая запись и производные доступны до 30 дней после загрузки или до явного удаления. Отзыв запрещает дальнейшую обработку; удаление и резервные копии выполняются отдельно. Реальные медицинские данные требуют определённого оператора и регламента.','2026-10-21T00:00:00Z'),
('ai_transcription','meeting-local-v2-2026-10-21','ИИ-расшифровка консультации',
'Разрешаю локальное распознавание речи этой записи моделью Faster Whisper на компьютере/сервере Клинического компаса. Запись не передаётся внешнему ИИ-провайдеру. Машинный текст с таймкодами может ошибаться; говорящие не угадываются, врач уточняет их вручную. Проверка названий лекарств по оригиналу обязательна. Для внешнего провайдера потребуется другое согласование.',
'Расшифровка хранится отдельно от оригинала, до 30 дней после загрузки записи или до явного удаления. Отзыв останавливает новые задачи; при изменении согласий запущенный результат не сохраняется. Распознавание проверяет отзыв между сегментами. Резервные копии контролируются владельцем сервера отдельно.','2026-10-21T00:00:00Z'),
('ai_summary','meeting-local-v2-2026-10-21','Черновик итогов и следующих шагов',
'Разрешаю локальной модели через Ollama на том же компьютере/сервере подготовить черновик итогов, следующих шагов по разговору и уточняющих вопросов. Модель не назначает лекарства, не ставит диагнозы и не меняет дневник. Все существенные пункты должны ссылаться на расшифровку; врач исправляет и подтверждает их перед показом пациенту. Это отдельное разрешение от записи и расшифровки.',
'Черновики и проверенные версии доступны до 30 дней после загрузки исходной записи или до явного удаления. Отзыв запрещает дальнейшие запуски и сохранение выполняющегося результата; уже созданное и резервные копии удаляются отдельно. Пауза между проверками возможна во время одного локального запроса модели.','2026-10-21T00:00:00Z');
