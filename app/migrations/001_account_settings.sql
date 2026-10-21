CREATE TABLE account_profiles(
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 80),
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    address_style TEXT NOT NULL DEFAULT 'polite' CHECK(address_style IN ('polite','friendly')),
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE notification_preferences(
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    diary INTEGER NOT NULL DEFAULT 0 CHECK(diary IN (0,1)),
    medication INTEGER NOT NULL DEFAULT 0 CHECK(medication IN (0,1)),
    tasks INTEGER NOT NULL DEFAULT 0 CHECK(tasks IN (0,1)),
    preferred_time TEXT NOT NULL DEFAULT '20:00',
    quiet_enabled INTEGER NOT NULL DEFAULT 1 CHECK(quiet_enabled IN (0,1)),
    quiet_start TEXT NOT NULL DEFAULT '22:00',
    quiet_end TEXT NOT NULL DEFAULT '08:00',
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE consultation_preferences(
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    audio INTEGER NOT NULL DEFAULT 0 CHECK(audio IN (0,1)),
    video INTEGER NOT NULL DEFAULT 0 CHECK(video IN (0,1)),
    transcription INTEGER NOT NULL DEFAULT 0 CHECK(transcription IN (0,1)),
    summary INTEGER NOT NULL DEFAULT 0 CHECK(summary IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE consent_documents(
    id INTEGER PRIMARY KEY,
    purpose TEXT NOT NULL CHECK(purpose IN ('notifications','audio_recording','video_recording','ai_transcription','ai_summary')),
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    retention TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(purpose,version),
    UNIQUE(id,purpose)
);
CREATE TABLE consultation_consent_contexts(
    id TEXT PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patients(id),
    created_by INTEGER NOT NULL REFERENCES accounts(id),
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE consent_participants(
    context_id TEXT NOT NULL REFERENCES consultation_consent_contexts(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    PRIMARY KEY(context_id,account_id)
);
CREATE TABLE consent_events(
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    context_id TEXT,
    purpose TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('granted','declined','withdrawn')),
    confirmed_via TEXT NOT NULL DEFAULT 'explicit_form',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id,purpose) REFERENCES consent_documents(id,purpose),
    FOREIGN KEY(context_id,account_id) REFERENCES consent_participants(context_id,account_id),
    CHECK((context_id IS NULL AND purpose='notifications') OR
          (context_id IS NOT NULL AND purpose<>'notifications'))
);
CREATE INDEX consent_events_latest ON consent_events(account_id,purpose,context_id,id DESC);
CREATE TRIGGER consent_events_no_update BEFORE UPDATE ON consent_events
BEGIN SELECT RAISE(ABORT,'Consent history is append-only'); END;
CREATE TRIGGER consent_events_no_delete BEFORE DELETE ON consent_events
BEGIN SELECT RAISE(ABORT,'Consent history is append-only'); END;
CREATE TRIGGER consent_documents_no_update BEFORE UPDATE ON consent_documents
BEGIN SELECT RAISE(ABORT,'Consent documents are immutable'); END;
CREATE TRIGGER consent_documents_no_delete BEFORE DELETE ON consent_documents
BEGIN SELECT RAISE(ABORT,'Consent documents are immutable'); END;
INSERT INTO consent_documents(purpose,version,title,body,retention,created_at) VALUES
('notifications','optional-v1-2026-10-21','Уведомления',
 'Разрешаю напоминания о дневнике, приёме лекарств и заданиях в выбранных мной категориях и времени. Можно отключить отдельные категории или отозвать это согласие. Текст внешнего уведомления не должен раскрывать диагноз и название препарата. Реальные каналы доставки пока не подключены.',
 'Отзыв запрещает новые необязательные напоминания. Настройки и история согласий сохраняются в локальной базе; отзыв не удаляет записи дневника и назначения.', '2026-10-21T00:00:00Z'),
('audio_recording','optional-v1-2026-10-21','Аудиозапись консультации',
 'Разрешаю аудиозапись только указанной консультации для последующего прослушивания её участниками по их правам доступа. Запуск требует отдельных подтверждений всех необходимых участников. Это не согласие на видеозапись, ИИ-расшифровку или сводку.',
 'Отзыв запрещает продолжение записи по этому разрешению. Уже созданное аудио не удаляется автоматически: срок и основания хранения, порядок удаления и резервных копий должны быть определены до подключения записи. Запись консультаций пока не подключена.', '2026-10-21T00:00:00Z'),
('video_recording','optional-v1-2026-10-21','Видеозапись консультации',
 'Разрешаю сохранение изображения и звука только указанной видеоконсультации для последующего просмотра её участниками по их правам доступа. Требуются отдельные подтверждения всех необходимых участников. Это не разрешение ИИ-расшифровки или сводки.',
 'Отзыв запрещает продолжение видеозаписи по этому разрешению. Ранее созданные материалы требуют отдельного порядка хранения и удаления, включая копии. До подключения записи нужно определить сроки, место хранения и ответственного. Видеозапись пока не подключена.', '2026-10-21T00:00:00Z'),
('ai_transcription','optional-v1-2026-10-21','ИИ-расшифровка консультации',
 'Разрешаю создание машинного текста из разрешённой записи только указанной консультации. Исходная запись и текст должны храниться отдельно; ошибки проверяет человек. Согласие на запись или предпочтение аккаунта не разрешают расшифровку. Внешний ИИ-сервис пока не подключён.',
 'Отзыв запрещает новые запуски и дальнейшую обработку по этому разрешению. Перед подключением сервиса необходимо указать обработчика, место и сроки хранения, порядок остановки задач и удаления исходных и производных данных.', '2026-10-21T00:00:00Z'),
('ai_summary','optional-v1-2026-10-21','Черновик сводки консультации',
 'Разрешаю подготовку вспомогательного черновика сводки только указанной консультации. Врач проверяет и подтверждает результат; ИИ не назначает лечение и не меняет ответы пациента. Это отдельное согласие от записи и расшифровки. Создание сводок пока не подключено.',
 'Отзыв запрещает новые запуски и дальнейшую обработку сводки по этому разрешению. Для уже созданных версий и медицинских записей нужен отдельный порядок хранения и удаления с учётом применимых оснований; мгновенное удаление всех копий не обещается.', '2026-10-21T00:00:00Z');
