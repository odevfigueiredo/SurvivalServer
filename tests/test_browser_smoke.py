"""Teste opcional de navegador. Use o Chromium do Playwright ou o Edge local.

    python tests/test_browser_smoke.py
    python tests/test_browser_smoke.py --channel msedge --lan --screenshots ./capturas
"""
from __future__ import annotations

import argparse
import contextlib
import io
import ipaddress
import re
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as core


FIXTURE_COUNT = 53
PAGE_SIZE = 50
CHECKLIST = b"disponivel sem conexao - teste de busca pelo conteudo\n"
PLAN = "Ponto principal: praça do bairro.\nPonto alternativo: casa da família."
DEMO_LAN = "http://192.168.1.42:8765"
ADMIN_PASSWORD = secrets.token_urlsafe(18)
READER_PASSWORD = secrets.token_urlsafe(18)


def seed_library(data):
    core.ensure_db(data)
    core.add_user(data, "admin", ADMIN_PASSWORD, "admin")
    core.add_user(data, "familia", READER_PASSWORD, "leitor")
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


@contextlib.contextmanager
def local_page(browser, origin, *, mobile=False):
    viewport = {"width": 390, "height": 844} if mobile else {"width": 1440, "height": 960}
    page = browser.new_page(viewport=viewport, is_mobile=mobile, has_touch=mobile)
    errors = []
    external = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def enforce_local(route):
        target = route.request.url
        if target.startswith((origin + "/", "data:", "blob:" + origin + "/")):
            route.continue_()
        else:
            external.append(target)
            route.abort()

    page.route("**/*", enforce_local)
    try:
        yield page
    finally:
        page.close()
        assert not errors, f"Erros JavaScript: {errors}"
        assert not external, f"Requisições fora do servidor local: {external}"


def go_to_page(page, name, *, mobile=False):
    if mobile:
        page.locator("#menuBtn").click()
    page.locator(f"[data-page={name}]").click()


def check_desktop(browser, url, expect):
    with local_page(browser, url) as page:
        login(page, url, "admin", ADMIN_PASSWORD, expect, FIXTURE_COUNT)
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

        print("Desktop OK — login, paginação, busca por conteúdo, download, falha parcial de upload, plano multilinha")


def check_mobile(browser, url, expect):
    with local_page(browser, url, mobile=True) as page:
        login(page, url, "familia", READER_PASSWORD, expect, FIXTURE_COUNT + 1)
        go_to_page(page, "library", mobile=True)
        expect(page.locator(".file-card")).to_have_count(PAGE_SIZE)
        expect(page.locator("#uploadButton")).to_be_hidden()
        go_to_page(page, "emergency", mobile=True)
        contacts = contacts_card(page)
        expect(contacts.locator(".plan-body")).to_have_text(re.compile(re.escape(PLAN)))
        expect(contacts.get_by_role("button", name="Editar seção")).to_have_count(0)
        expect(page.locator("[data-page=accounts]")).to_be_hidden()
        print("Mobile OK — login leitor, menu, biblioteca e consulta do plano multilinha")


def open_network(page, url, expect, *, mobile=False):
    with page.expect_response(lambda response: response.url == url + "/api/access") as event:
        go_to_page(page, "guide", mobile=mobile)
    response = event.value
    assert response.ok, f"Falha ao consultar endereços: HTTP {response.status}"
    expect(page.locator("#network-session-url")).to_have_value(url)
    return response.json()


def check_network(browser, url, expect, *, lan):
    from playwright.sync_api import Error as PlaywrightError

    with local_page(browser, url) as page:
        login(page, url, "admin", ADMIN_PASSWORD, expect, FIXTURE_COUNT + 1)
        access = open_network(page, url, expect)
        assert access["lan_enabled"] is lan
        if not lan:
            expect(page.locator("#network-access-state")).to_contain_text("Acesso apenas neste computador")
            print("Acesso na rede OK — modo local identificado.")
            return

        addresses = [entry["url"] for entry in access["access_urls"] if entry["kind"] == "lan"]
        if not addresses:
            expect(page.locator("#network-access-state")).to_contain_text("Nenhum endereço de rede encontrado")
            print("Acesso na rede OK — nenhuma interface LAN encontrada para o teste.")
            return
        expect(page.locator("#network-share-url")).to_have_value(addresses[0])
        expect(page.locator("#network-share-qr")).to_be_visible()

    for address in addresses:
        host = ipaddress.IPv4Address(urlsplit(address).hostname)
        assert host.is_private or host.is_link_local
        with local_page(browser, address, mobile=True) as mobile:
            try:
                response = mobile.goto(address + "/api/health", timeout=3000)
            except PlaywrightError:
                continue
            assert response.ok, f"Falha ao acessar o servidor na rede: HTTP {response.status}"
            login(mobile, address, "familia", READER_PASSWORD, expect, FIXTURE_COUNT + 1)
            open_network(mobile, address, expect, mobile=True)
            shared = mobile.locator("#network-share-url")
            expect(shared).to_have_value(address)
            expect(mobile.locator("#network-share-qr")).to_be_visible()
            mobile.evaluate("""() => {
                Object.defineProperty(navigator, 'clipboard', {value: undefined});
                document.execCommand = () => false;
            }""")
            mobile.locator("#network-share-copy").click()
            expect(shared).to_be_focused()
            assert shared.evaluate("input => [input.selectionStart, input.selectionEnd]") == [0, len(address)]
            print("Acesso na rede OK — login pelo IPv4 local, QR e seleção manual no navegador móvel.")
            return
    print("Acesso na rede: o servidor informou IPv4, mas o navegador não alcançou a interface LAN.")


def check_accounts(browser, url, expect):
    username = "vizinho"
    password = secrets.token_urlsafe(18)
    new_password = secrets.token_urlsafe(18)
    with local_page(browser, url) as admin, local_page(browser, url) as reader:
        login(admin, url, "admin", ADMIN_PASSWORD, expect, FIXTURE_COUNT + 1)
        go_to_page(admin, "accounts")
        rows = admin.locator("#accountRows")
        expect(rows).to_contain_text("familia")

        admin.locator("#accountUsername").fill(username)
        admin.locator("#accountPassword").fill(password)
        admin.locator("#accountRole").select_option("leitor")
        admin.get_by_role("button", name="Criar conta").click()
        account = rows.get_by_role("listitem").filter(has_text=username)
        expect(account).to_contain_text("Leitor")
        login(reader, url, username, password, expect, FIXTURE_COUNT + 1)
        expect(reader.locator("[data-page=accounts]")).to_be_hidden()
        assert reader.request.get(url + "/api/users").status == 403

        account.get_by_role("button", name="Alterar senha").click()
        dialog = admin.locator("#accountPasswordDialog")
        expect(dialog).to_be_visible()
        dialog.locator("#newAccountPassword").fill(new_password)
        dialog.get_by_role("button", name="Salvar senha").click()
        expect(dialog).to_be_hidden()
        assert reader.request.get(url + "/api/me").json()["session"] is None
        with reader.expect_response(lambda response: response.url == url + "/api/access") as event:
            go_to_page(reader, "guide")
        assert event.value.status == 401
        expect(reader.locator("#loginScreen")).to_be_visible()
        expect(reader.locator("#networkAccessDynamic")).to_be_empty()
        reader.locator("#loginUsername").fill(username)
        reader.locator("#loginPassword").fill(password)
        reader.get_by_role("button", name=re.compile("Entrar")).click()
        expect(reader.locator("#loginError")).to_be_visible()
        login(reader, url, username, new_password, expect, FIXTURE_COUNT + 1)

        admin.once("dialog", lambda confirmation: confirmation.accept())
        account.get_by_role("button", name="Excluir").click()
        expect(account).to_have_count(0)
        assert reader.request.get(url + "/api/me").json()["session"] is None
    print("Contas OK — criação, restrição ao leitor, troca de senha, revogação de sessão e exclusão.")


def capture_screenshots(browser, url, expect, directory):
    for mobile in (False, True):
        target = directory / "mobile" if mobile else directory
        target.mkdir(parents=True, exist_ok=True)
        with local_page(browser, url, mobile=mobile) as page:
            login(page, url, "admin", ADMIN_PASSWORD, expect, FIXTURE_COUNT + 1)
            if not mobile:
                page.screenshot(path=target / "dashboard.png", animations="disabled")

            # O teste de rede real roda sem esta substituição.
            page.route(url + "/api/access", lambda route: route.fulfill(json={
                "access_urls": [{"url": DEMO_LAN, "kind": "lan"}, {"url": url, "kind": "loopback"}],
                "lan_enabled": True,
                "scheme": "http",
                "port": 8765,
            }))
            open_network(page, url, expect, mobile=mobile)
            expect(page.locator("#network-share-url")).to_have_value(DEMO_LAN)
            expect(page.locator("#network-share-qr")).to_be_visible()
            page.screenshot(path=target / "acesso-rede.png", animations="disabled", full_page=mobile)

            go_to_page(page, "accounts", mobile=mobile)
            expect(page.locator("#accountRows")).to_contain_text("familia")
            page.screenshot(path=target / "contas.png", animations="disabled", full_page=mobile)
    print(f"Capturas com IPv4 de demonstração: {directory}")


def run(channel=None, *, lan=False, screenshots=None):
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "biblioteca"
        seed_library(data)
        binding = "0.0.0.0" if lan else "127.0.0.1"
        with core.SurvivalHTTPServer((binding, 0), data) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel=channel)
                    try:
                        check_desktop(browser, url, expect)
                        check_mobile(browser, url, expect)
                        check_network(browser, url, expect, lan=lan)
                        check_accounts(browser, url, expect)
                        if screenshots is not None:
                            capture_screenshots(browser, url, expect, screenshots)
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Testar o portal em Chromium ou Edge.")
    parser.add_argument("--channel", help="Canal do navegador instalado, como msedge ou chrome.")
    parser.add_argument("--lan", action="store_true", help="Também testa o acesso pelo IPv4 local quando disponível.")
    parser.add_argument("--screenshots", type=Path, metavar="DIR", help="Salva capturas opcionais com dados de demonstração.")
    args = parser.parse_args()
    run(args.channel, lan=args.lan, screenshots=args.screenshots)
