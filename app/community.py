"""Unlinked public FAQ questions with explicit, human-reviewed publication."""

import secrets
from fastapi import Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from .storage import now


def public_forum_data(db):
    with db() as c:
        questions = [
            dict(r)
            for r in c.execute(
                "SELECT public_id,body,reply,created_at,replied_at FROM forum_questions WHERE status='published' ORDER BY id DESC LIMIT 100"
            )
        ]
    return dict(public_questions=questions, form_id=secrets.token_urlsafe(24))


def register_community(app, db, identity, account, csrf_check, render):
    @app.get("/faq")
    def faq(request: Request):
        return render(request, "faq.html", **public_forum_data(db))

    @app.post("/faq/questions")
    def ask_public(
        request: Request,
        csrf: str = Form(...),
        body: str = Form(..., min_length=1, max_length=2000),
        public_consent: bool = Form(False),
        form_id: str = Form(..., min_length=20, max_length=80),
    ):
        csrf_check(request, csrf)
        if not public_consent or not body.strip():
            raise HTTPException(
                422, "Напишите общий вопрос и подтвердите публичный формат"
            )
        # No account is queried by this handler. The one-use submission nonce is
        # independent of login; no author, patient ID, IP or user-agent is stored.
        public_id = secrets.token_urlsafe(24)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT public_id,body FROM forum_questions WHERE submission_id=?",
                (form_id,),
            ).fetchone()
            if previous:
                if previous["body"] != body.strip():
                    raise HTTPException(
                        409,
                        "Форма уже отправлена. Обновите страницу для нового вопроса.",
                    )
                public_id = previous["public_id"]
            else:
                c.execute(
                    "INSERT INTO forum_questions(public_id,submission_id,body,created_at) VALUES(?,?,?,?)",
                    (public_id, form_id, body.strip(), now()),
                )
        response = RedirectResponse("/faq/questions/" + public_id, status_code=303)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/faq/questions/{public_id}")
    def thread(request: Request, public_id: str):
        with db() as c:
            question = c.execute(
                "SELECT public_id,body,reply,status,created_at,replied_at FROM forum_questions WHERE public_id=? AND status!='hidden'",
                (public_id,),
            ).fetchone()
        if not question:
            raise HTTPException(404, "Вопрос не найден")
        response = render(request, "forum_thread.html", question=question)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Robots-Tag": "noindex",
            }
        )
        return response

    @app.get("/doctor/faq")
    def moderation(request: Request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только модератору из кабинета специалиста")
        with db() as c:
            questions = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM forum_questions ORDER BY id DESC LIMIT 100"
                )
            ]
        return render(request, "forum_moderation.html", questions=questions)

    @app.post("/doctor/faq/{qid}")
    def moderate(
        request: Request,
        qid: int,
        csrf: str = Form(...),
        intent: str = Form(...),
        reply: str = Form("", max_length=3000),
    ):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только модератору")
        csrf_check(request, csrf)
        if intent not in ["publish", "hide"] or (
            intent == "publish" and not reply.strip()
        ):
            raise HTTPException(422, "Для публикации нужен проверенный ответ")
        with db() as c:
            result = c.execute(
                "UPDATE forum_questions SET reply=?,status=?,replied_at=?,moderator_id=? WHERE id=?",
                (
                    reply.strip(),
                    "published" if intent == "publish" else "hidden",
                    now(),
                    account(request)["id"],
                    qid,
                ),
            )
            if not result.rowcount:
                raise HTTPException(404, "Вопрос не найден")
        return RedirectResponse("/doctor/faq", status_code=303)
