"""Two real HTTP workers, independent browsers and synthetic accounts only."""

from playwright.sync_api import expect


def check_concurrent_sessions(browser, urls, credentials, restart):
    errors = []
    for role, username in (("specialist", "specialist-2"), ("patient", "patient-e")):
        contexts = [browser.new_context(reduced_motion="reduce") for _ in range(2)]
        first, second = [context.new_page() for context in contexts]
        for page in (first, second):
            page.on("pageerror", lambda error: errors.append(str(error)))

        def login(page, backend):
            assert page.goto(urls[backend] + "/login/" + role).status == 200
            page.get_by_label("Логин", exact=True).fill(username)
            page.get_by_label("Пароль", exact=True).fill(credentials[username])
            page.get_by_role(
                "button",
                name=(
                    "Войти как специалист"
                    if role == "specialist"
                    else "Войти как пациент"
                ),
                exact=True,
            ).click()
            expect(page).to_have_url(
                urls[backend]
                + ("/doctor" if role == "specialist" else "/patients/3/diary")
            )

        def form(page):
            if role == "specialist":
                selected = page.locator(
                    'form[action^="/patients/3/diary/"][action$="/comments"]'
                ).first
                selected.locator("..").locator(":scope > summary").click()
                return selected
            return page.locator('form[action="/patients/3/notes"]')

        def save(page, text):
            selected = form(page)
            selected.locator('[name="body"]').fill(text)
            with page.expect_navigation() as navigation:
                selected.get_by_role("button").click()
            assert navigation.value.status == 200
            expect(page.locator("main")).to_contain_text(text)

        login(first, 0)
        assert first.goto(urls[0] + "/patients/3/diary").status == 200
        # Open a form before the other person logs in.
        first_token = first.locator('meta[name="csrf-token"]').get_attribute("content")
        login(second, 1)
        assert second.goto(urls[1] + "/patients/3/diary").status == 200
        second_token = second.locator('meta[name="csrf-token"]').get_attribute(
            "content"
        )
        assert first_token != second_token
        save(first, "Синтетический ввод первого браузера: " + role)
        save(second, "Синтетический ввод второго браузера: " + role)

        routes = (
            (
                "/doctor",
                "/doctor/calendar",
                "/doctor/library",
                "/doctor/forums",
                "/settings",
            )
            if role == "specialist"
            else (
                "/patients/3/diary",
                "/patients/3/profile",
                "/patients/3/profile/assignments",
                "/patients/3/results",
                "/settings",
            )
        )
        for index, path in enumerate(routes):
            for person, page in enumerate((first, second)):
                assert page.goto(urls[(index + person) % 2] + path).status == 200
        cached = contexts[1].new_page()
        assert cached.goto(urls[0] + "/settings").status == 200
        second.get_by_role("button", name="Выйти из кабинета", exact=True).click()
        expect(second).to_have_url(urls[1] + "/")
        # The stale page shares only the second person's now closed cookie.
        cached.get_by_role("button", name="Выйти из кабинета", exact=True).click()
        expect(cached).to_have_url(urls[0] + "/")

        assert first.goto(urls[0] + "/patients/3/diary").status == 200
        save(first, "Синтетический ввод после выхода второго: " + role)
        assert first.goto(urls[0] + "/patients/3/diary").status == 200
        assert (
            first.locator('meta[name="csrf-token"]').get_attribute("content")
            == first_token
        )
        restart(0)
        # Submit the pre-restart HTML form without reloading or reauthenticating.
        save(first, "Синтетический ввод после перезапуска процесса: " + role)
        assert first.goto(urls[1] + "/settings").status == 200
        first.get_by_role("button", name="Выйти из кабинета", exact=True).click()
        expect(first).to_have_url(urls[1] + "/")
        for context in contexts:
            context.close()
    assert not errors, errors
    print(
        "PASS two actual HTTP workers: two independent browsers on each of the same doctor/patient accounts; separate CSRF tokens, existing forms after second login, cross-worker navigation/actions, logout in one browser preserves the other, repeated cached logout, pre-restart form save after real process restart, no JavaScript errors. Synthetic temporary DB only.",
        flush=True,
    )
