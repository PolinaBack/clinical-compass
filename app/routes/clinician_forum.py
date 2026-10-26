"""Authenticated physician forum; never exposes or imports patient records."""

import secrets
from urllib.parse import urlencode

from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.services import clinician_forum as forum
from app.services import forum_attachments as attachments
from app.storage import audit, now


def register_clinician_forum(app, db, account, csrf_check, render):
    def doctor(request):
        actor = account(request)
        if actor["role"] != "doctor":
            raise HTTPException(403, "Форум доступен только врачам.")
        return actor

    def page(request, template, **context):
        if context.get("form"):
            form = dict(context["form"])
            actor = doctor(request)
            kind = "comment" if "comment" in form["operation"] else "topic"
            with db() as c:
                form["attachment_files"] = attachments.selected(
                    c,
                    form.get("attachment_ids", []),
                    actor,
                    kind,
                    form["target_id"] if form["operation"].startswith("edit") else None,
                )
            context["form"] = form
        response = render(request, template, kinds=forum.KINDS, **context)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Robots-Tag": "noindex, nofollow",
            }
        )
        return response

    def redirect(tid=None):
        response = RedirectResponse(
            "/doctor/forum" + (f"/topics/{tid}" if tid else ""), status_code=303
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def check_fields(request, allowed):
        form = await request.form()
        if any(k not in allowed for k in form) or any(
            len(form.getlist(k)) != 1 for k in form if k != "file"
        ):
            raise HTTPException(
                422,
                "Форум принимает только поля формы и вложения; привязки к пациенту недоступны.",
            )
        return form

    @app.get("/doctor/forum")
    def index(
        request: Request,
        kind: str = "all",
        mine: bool = False,
        q: str = Query("", max_length=80),
        p: int = Query(1, ge=1, le=100000),
    ):
        actor = doctor(request)
        if kind not in {"all", *forum.KINDS}:
            raise HTTPException(422, "Выберите случаи, вопросы или все обсуждения.")
        where, args = ["t.status='published'"], []
        if mine:
            where = ["t.author_id=? AND t.status!='deleted'"]
            args.append(actor["id"])
        if kind != "all":
            where.append("t.kind=?")
            args.append(kind)
        if q.strip():
            where.append("instr(forum_fold(t.title || char(10) || t.body),?)>0")
            args.append(q.strip().casefold())
        with db() as c:
            c.create_function("forum_fold", 1, str.casefold, deterministic=True)
            records = [
                dict(r)
                for r in c.execute(
                    "SELECT t.*,(SELECT count(*) FROM clinician_comments r WHERE r.topic_id=t.id AND r.status='published') AS comments_count FROM clinician_topics t WHERE "
                    + " AND ".join(where)
                    + " ORDER BY t.id DESC LIMIT 21 OFFSET ?",
                    (*args, (p - 1) * 20),
                )
            ]
        parameters = dict(kind=kind, mine="true" if mine else "false", q=q)
        return page(
            request,
            "clinician_forum.html",
            actor=actor,
            topics=records[:20],
            kind=kind,
            mine=mine,
            q=q,
            previous=(
                "/doctor/forum?" + urlencode(parameters | {"p": p - 1})
                if p > 1
                else None
            ),
            following=(
                "/doctor/forum?" + urlencode(parameters | {"p": p + 1})
                if len(records) > 20
                else None
            ),
        )

    @app.get("/doctor/forum/new")
    def new(request: Request):
        doctor(request)
        return page(
            request,
            "clinician_forum_form.html",
            form=dict(
                operation="topic",
                target_id=0,
                revision=0,
                kind="question",
                title="",
                body="",
            ),
            error=None,
        )

    @app.get("/doctor/forum/topics/{tid}")
    def thread(request: Request, tid: int):
        actor = doctor(request)
        with db() as c:
            item = forum.topic(c, tid, actor)
            item["files"] = attachments.listing(c, "topic", tid)
            comments = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM clinician_comments WHERE topic_id=? AND (status='published' OR (status='hidden' AND author_id=?)) ORDER BY id LIMIT 200",
                    (tid, actor["id"]),
                )
            ]
            for comment in comments:
                comment["files"] = attachments.listing(c, "comment", comment["id"])
        return page(
            request,
            "clinician_forum_thread.html",
            actor=actor,
            topic=item,
            comments=comments,
            form=dict(
                operation="comment",
                target_id=tid,
                revision=item["revision"],
                kind=item["kind"],
                title="",
                body="",
            ),
        )

    @app.get("/doctor/forum/topics/{tid}/edit")
    @app.get("/doctor/forum/comments/{tid}/edit")
    def edit(request: Request, tid: int):
        actor = doctor(request)
        operation = "edit_comment" if "/comments/" in request.url.path else "edit_topic"
        with db() as c:
            item = forum.target(c, operation, tid, actor)
            attached = attachments.listing(
                c, "comment" if operation == "edit_comment" else "topic", tid
            )
        return page(
            request,
            "clinician_forum_form.html",
            form=dict(
                operation=operation,
                target_id=tid,
                revision=item["revision"],
                kind=item.get("kind", "question"),
                title=item.get("title", ""),
                body=item["body"],
                attachment_ids=[f["id"] for f in attached],
            ),
            error=None,
        )

    async def prepare(
        request,
        operation,
        target_id,
        revision,
        kind,
        title,
        body,
        deidentified,
        *,
        publishing=False,
    ):
        actor = doctor(request)
        allowed = {
            "csrf",
            "operation",
            "target_id",
            "revision",
            "kind",
            "title",
            "body",
            "deidentified",
            "attachments",
        }
        raw_form = await check_fields(
            request, allowed | ({"preview_token"} if publishing else {"file"})
        )
        values = attachments.ids(raw_form.get("attachments", "[]"))
        file_kind = "comment" if "comment" in operation else "topic"
        file_target = target_id if operation.startswith("edit") else None
        with db() as c:
            forum.target(c, operation, target_id, actor)
            if publishing and not operation.startswith("edit"):
                table = (
                    "clinician_comments"
                    if file_kind == "comment"
                    else "clinician_topics"
                )
                published = c.execute(
                    f"SELECT id FROM {table} WHERE submission_id=? AND author_id=?",
                    (raw_form.get("preview_token", ""), actor["id"]),
                ).fetchone()
                if published:
                    file_target = published["id"]
            attachments.selected(c, values, actor, file_kind, file_target)
        if not publishing:
            if not deidentified:
                raise HTTPException(422, "Подтвердите обезличивание текста и вложений")
            prepared = await attachments.read_uploads(raw_form)
            if len(values) + len(prepared) > attachments.MAX_FILES:
                raise HTTPException(422, "Можно прикрепить до пяти файлов")
            with db() as c:
                values += attachments.save(
                    c, app.state.db_path, file_kind, prepared, actor["id"]
                )
            request.state.forum_attachment_ids = values
        title, body = forum.validate_content(
            kind,
            title,
            body,
            deidentified,
            comment=operation in {"comment", "edit_comment"},
        )
        payload = dict(
            operation=operation,
            target_id=target_id,
            revision=revision,
            kind=kind,
            title=title,
            body=body,
            deidentified=True,
        )
        if values:
            payload["attachment_ids"] = values
        return actor, payload

    @app.post("/doctor/forum/preview")
    async def preview(
        request: Request,
        csrf: str = Form(...),
        operation: str = Form(...),
        target_id: int = Form(0),
        revision: int = Form(0),
        kind: str = Form("question"),
        title: str = Form("", max_length=140),
        body: str = Form(..., max_length=6000),
        deidentified: bool = Form(False),
    ):
        doctor(request)
        csrf_check(request, csrf)
        form = dict(
            operation=operation,
            target_id=target_id,
            revision=revision,
            kind=kind,
            title=title,
            body=body,
        )
        try:
            actor, payload = await prepare(
                request, operation, target_id, revision, kind, title, body, deidentified
            )
            with db() as c:
                item = forum.target(c, operation, target_id, actor)
                if item and item["revision"] != revision:
                    raise HTTPException(
                        409, "Публикация изменилась. Откройте актуальную версию."
                    )
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            form["attachment_ids"] = getattr(request.state, "forum_attachment_ids", [])
            response = page(
                request, "clinician_forum_form.html", form=form, error=exc.detail
            )
            response.status_code = 422
            return response
        nonce = secrets.token_urlsafe(24)
        previews = request.session.get("clinician_previews", [])[-4:]
        previews.append([nonce, forum.fingerprint(payload, actor)])
        request.session["clinician_previews"] = previews
        return page(
            request, "clinician_forum_preview.html", form=payload, preview_token=nonce
        )

    @app.post("/doctor/forum/publish")
    async def publish(
        request: Request,
        csrf: str = Form(...),
        preview_token: str = Form(..., max_length=80),
        operation: str = Form(...),
        target_id: int = Form(0),
        revision: int = Form(0),
        kind: str = Form("question"),
        title: str = Form("", max_length=140),
        body: str = Form(..., max_length=6000),
        deidentified: bool = Form(False),
    ):
        doctor(request)
        csrf_check(request, csrf)
        actor, payload = await prepare(
            request,
            operation,
            target_id,
            revision,
            kind,
            title,
            body,
            deidentified,
            publishing=True,
        )
        expected = forum.fingerprint(payload, actor)
        if not any(
            secrets.compare_digest(nonce, preview_token)
            and secrets.compare_digest(value, expected)
            for nonce, value in request.session.get("clinician_previews", [])
        ):
            raise HTTPException(
                409, "Сначала проверьте предварительный просмотр именно этого текста."
            )
        comment = operation in {"comment", "edit_comment"}
        table = "clinician_comments" if comment else "clinician_topics"
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                f"SELECT * FROM {table} WHERE submission_id=?", (preview_token,)
            ).fetchone()
            if previous:
                if previous["author_id"] != actor["id"]:
                    raise HTTPException(409, "Обновите форму.")
                return redirect(previous["topic_id"] if comment else previous["id"])
            item = forum.target(c, operation, target_id, actor)
            if item and item["revision"] != revision:
                raise HTTPException(
                    409, "Публикация изменилась. Откройте актуальную версию."
                )
            stamp = now()
            if operation.startswith("edit"):
                if comment:
                    c.execute(
                        "UPDATE clinician_comments SET body=?,status='published',revision=revision+1,submission_id=?,updated_at=? WHERE id=?",
                        (payload["body"], preview_token, stamp, target_id),
                    )
                    tid = item["topic_id"]
                else:
                    c.execute(
                        "UPDATE clinician_topics SET kind=?,title=?,body=?,status='published',revision=revision+1,submission_id=?,updated_at=? WHERE id=?",
                        (
                            kind,
                            payload["title"],
                            payload["body"],
                            preview_token,
                            stamp,
                            target_id,
                        ),
                    )
                    tid = target_id
                oid = target_id
            elif comment:
                # Keep one complete thread readable; further topics remain available.
                if (
                    c.execute(
                        "SELECT count(*) FROM clinician_comments WHERE topic_id=? AND status!='deleted'",
                        (target_id,),
                    ).fetchone()[0]
                    >= 200
                ):
                    raise HTTPException(
                        409,
                        "В обсуждении уже 200 ответов. Создайте новую тему для продолжения.",
                    )
                oid = c.execute(
                    "INSERT INTO clinician_comments(topic_id,author_id,body,submission_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        target_id,
                        actor["id"],
                        payload["body"],
                        preview_token,
                        stamp,
                        stamp,
                    ),
                ).lastrowid
                tid = target_id
            else:
                oid = c.execute(
                    "INSERT INTO clinician_topics(author_id,kind,title,body,submission_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        actor["id"],
                        kind,
                        payload["title"],
                        payload["body"],
                        preview_token,
                        stamp,
                        stamp,
                    ),
                ).lastrowid
                tid = oid
            attachments.bind(
                c,
                payload.get("attachment_ids", []),
                actor,
                "comment" if comment else "topic",
                oid,
            )
            audit(c, actor["id"], f"clinician_forum_{operation}", object_id=oid)
        return redirect(tid)

    @app.post("/doctor/forum/{collection}/{oid}/{intent}")
    async def act(
        request: Request,
        collection: str,
        oid: int,
        intent: str,
        csrf: str = Form(...),
        confirmed: bool = Form(False),
    ):
        actor = doctor(request)
        csrf_check(request, csrf)
        await check_fields(request, {"csrf", "confirmed"})
        if collection not in {"topics", "comments"} or intent not in {
            "delete",
            "report",
        }:
            raise HTTPException(404, "Действие не найдено.")
        if not confirmed:
            raise HTTPException(422, "Подтвердите действие.")
        table = "clinician_topics" if collection == "topics" else "clinician_comments"
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            item = c.execute(
                f"SELECT * FROM {table} WHERE id=? AND status!='deleted'", (oid,)
            ).fetchone()
            if (
                not item
                or item["status"] == "hidden"
                and item["author_id"] != actor["id"]
            ):
                raise HTTPException(404, "Публикация недоступна.")
            if collection == "comments":
                forum.topic(c, item["topic_id"], actor)
            if intent == "delete":
                if item["author_id"] != actor["id"]:
                    raise HTTPException(403, "Удалять можно только свои публикации.")
                c.execute(
                    f"UPDATE {table} SET body='',status='deleted',revision=revision+1,updated_at=?"
                    + (",title=''" if collection == "topics" else "")
                    + " WHERE id=?",
                    (now(), oid),
                )
                if collection == "topics":
                    c.execute(
                        "UPDATE clinician_comments SET body='',status='deleted',revision=revision+1,updated_at=? WHERE topic_id=?",
                        (now(), oid),
                    )
            else:
                c.execute(
                    f"UPDATE {table} SET status='hidden',revision=revision+1,updated_at=? WHERE id=?",
                    (now(), oid),
                )
                field = "topic_id" if collection == "topics" else "comment_id"
                c.execute(
                    f"INSERT INTO clinician_privacy_reports({field},reporter_id,created_at) VALUES(?,?,?)",
                    (oid, actor["id"], now()),
                )
            audit(
                c, actor["id"], f"clinician_forum_{collection}_{intent}", object_id=oid
            )
            tid = item["topic_id"] if collection == "comments" else None
        return redirect(tid)
