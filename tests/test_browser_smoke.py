"""Teste opcional de navegador. Instale Playwright e Chromium antes de executar.

    python tests/test_browser_smoke.py
    python tests/test_browser_smoke.py --channel msedge
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as core


FIXTURE_COUNT = 53
PAGE_SIZE = 50
CHECKLIST = b"disponivel sem conexao - teste de busca pelo conteudo\n"
PLAN = "Ponto principal: praça do bairro.\nPonto alternativo: casa da família."


def seed_library(data):
    core.ensure_db(data)
    core.add_user(data, "admin", "SenhaDeTeste123!", "admin")
    core.add_user(data, "familia", "FamiliaTeste123!", "leitor")
    core.add_stream(data, "Checklist.txt", io.BytesIO(CHECKLIST), len(CHECKLIST), "Guias")
    for index in range(FIXTURE_COUNT - 1):
        content = f"Exemplo {index:02d}\n".encode()
        core.add_stream(data, f"Exemplo {index:02d}.txt", io.BytesIO(content), len(content))


def login(page, url, username, password, expect, count):
    page.goto(url, wait_until="domcontentloaded")
    expect(page.locator("#loginScreen")).to_be_visible()
    page.locator("#loginUsername").fill(username)
    page.locator("#loginPassword").fill(password)
    page.locator("#loginForm button[type=submit]").click()
    expect(page.locator("#statFiles")).to_have_text(str(count))


def contacts_card(page):
    return page.locator(".plan-card").filter(
        has=page.get_by_role("heading", name="Contatos essenciais")
    )


def check_desktop(browser, url, expect):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        login(page, url, "admin", "SenhaDeTeste123!", expect, FIXTURE_COUNT)
        page.locator("[data-page=library]").click()
        cards = page.locator(".file-card")
        previous = page.locator("#filesPrevious")
        next_page = page.locator("#filesNext")

        expect(cards).to_have_count(PAGE_SIZE)
        expect(page.locator("#filesPagination")).to_have_css("display", "flex")
        expect(previous).to_be_disabled()
        first_page_names = set(cards.locator("h3").all_text_contents())
        next_page.click()
        expect(cards).to_have_count(FIXTURE_COUNT - PAGE_SIZE)
        expect(next_page).to_be_disabled()
        assert first_page_names.isdisjoint(cards.locator("h3").all_text_contents())
        previous.click()
        expect(cards).to_have_count(PAGE_SIZE)

        search = page.locator("#searchInput")
        search.fill("teste de busca pelo conteudo")
        expect(cards).to_have_count(1)
        expect(cards.locator("h3")).to_have_text("Checklist.txt")
        with page.expect_download() as event:
            cards.get_by_role("link", name=re.compile("Baixar")).click()
        assert event.value.path().read_bytes() == CHECKLIST

        page.locator("#uploadButton").click()
        page.locator("#uploadFiles").set_input_files([
            {"name": "Guia adicionado.txt", "mimeType": "text/plain", "buffer": b"Guia teste"},
            {"name": "Vazio.txt", "mimeType": "text/plain", "buffer": b""},
        ])
        page.locator("#startUpload").click()
        error = page.locator("#uploadError")
        expect(error).to_be_visible()
        expect(page.locator("#uploadModal")).to_be_visible()
        retry = page.locator("#startUpload")
        expect(retry).to_be_enabled()
        with page.expect_response(
            lambda response: response.status == 400 and "filename=Vazio.txt" in response.url
        ):
            retry.click()
        expect(error).to_be_visible()
        page.locator("#cancelUpload").click()
        expect(page.locator("#uploadModal")).to_be_hidden()
        search.fill("Guia adicionado")
        expect(cards).to_have_count(1)
        expect(cards.locator("h3")).to_have_text("Guia adicionado.txt")

        page.locator("[data-page=emergency]").click()
        contacts = contacts_card(page)
        contacts.get_by_role("button", name="Editar seção").click()
        editor = contacts.locator("#plan-body-contacts")
        expect(editor).to_be_focused()
        expect(editor).to_have_css("resize", "vertical")
        editor.fill(PLAN)
        contacts.get_by_role("button", name="Salvar").click()
        expect(contacts.locator(".plan-body")).to_have_text(re.compile(re.escape(PLAN)))

        assert not errors, f"Erros JavaScript no desktop: {errors}"
        print("Desktop OK — login, paginação, busca por conteúdo, download, falha parcial de upload, plano multilinha")
    finally:
        page.close()


def check_mobile(browser, url, expect):
    page = browser.new_page(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        login(page, url, "familia", "FamiliaTeste123!", expect, FIXTURE_COUNT + 1)
        page.locator("#menuBtn").click()
        page.locator("[data-page=library]").click()
        expect(page.locator(".file-card")).to_have_count(PAGE_SIZE)
        expect(page.locator("#uploadButton")).to_be_hidden()
        page.locator("#menuBtn").click()
        page.locator("[data-page=emergency]").click()
        contacts = contacts_card(page)
        expect(contacts.locator(".plan-body")).to_have_text(re.compile(re.escape(PLAN)))
        expect(contacts.get_by_role("button", name="Editar seção")).to_have_count(0)
        assert not errors, f"Erros JavaScript no celular: {errors}"
        print("Mobile OK — login leitor, menu, biblioteca e consulta do plano multilinha")
    finally:
        page.close()


def run(channel=None):
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "biblioteca"
        seed_library(data)
        with core.SurvivalHTTPServer(("127.0.0.1", 0), data) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel=channel)
                    try:
                        check_desktop(browser, url, expect)
                        check_mobile(browser, url, expect)
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Testar o portal em Chromium ou Edge.")
    parser.add_argument("--channel", help="Canal do navegador instalado, como msedge ou chrome.")
    run(parser.parse_args().channel)
