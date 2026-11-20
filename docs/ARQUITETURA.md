# Arquitetura do Survival Server

```text
              SEM NECESSIDADE DE INTERNET

    Celular Android  ──────┐
    iPhone / iPad    ──────┤
    Notebook         ──────┤   Wi-Fi local
                           ▼
                    ┌────────────┐
                    │ Roteador/AP│ (sem internet)
                    └─────┬──────┘
                          │
                    ┌─────▼──────────────────┐
                    │ Survival Server        │
                    │ Windows / Linux / Pi   │
                    │                        │
                    │ Web UI (HTML/CSS/JS)   │
                    │ HTTP API (Python)      │
                    │ SQLite (metadados)     │
    │ Arquivos com SHA-256   │
                    └────────────────────────┘
```

## Persistência

A pasta `dados_survival` é criada localmente após `init`:

```text
dados_survival/
├── server.sqlite3       # metadados, usuários e sessões
└── objetos/             # arquivos físicos com identificadores aleatórios
    ├── aabbcc...        # nome interno, sem extensão
    └── ...
```

O banco relaciona cada objeto a nome, categoria, descrição, tamanho, tipo MIME e hash SHA-256. A pesquisa de conteúdo é restrita a formatos textuais suportados.

O backup cria um snapshot SQLite e abre os objetos listados nesse catálogo enquanto uma transação SQLite impede alterações concorrentes. Cada objeto é conferido durante a cópia, e o ZIP temporário substitui o destino somente após a conclusão. O servidor e os comandos de manutenção coordenam o acesso à pasta de dados por um arquivo SQLite irmão. A restauração obtém acesso exclusivo, extrai e valida em uma pasta temporária e aplica as migrações aditivas antes da troca.

## API

| Método | Rota | Permissão |
|---|---|---|
| GET | `/api/health` | Pública (estado resumido) |
| POST | `/api/login` | Pública (credenciais) |
| GET | `/api/me` | Identificação da sessão |
| POST | `/api/logout` | Autenticado + CSRF |
| GET | `/api/access` | Autenticado |
| GET | `/api/users` | Admin |
| POST | `/api/users` | Admin + CSRF |
| PUT | `/api/users/:username/password` | Admin + CSRF |
| DELETE | `/api/users/:username` | Admin + CSRF |
| GET | `/api/files` | Autenticado |
| GET | `/api/manifest` | Autenticado |
| GET | `/api/stats` | Autenticado |
| GET | `/api/emergency` | Autenticado |
| PUT | `/api/emergency/:key` | Admin + CSRF |
| GET | `/api/diagnostics`, `/api/audit` | Admin |
| POST | `/api/verify` | Admin + CSRF |
| POST | `/api/files/upload` | Admin + CSRF |
| GET | `/api/files/:id/view` | Autenticado |
| GET | `/api/files/:id/download` | Autenticado |
| PUT | `/api/files/:id/metadata` | Admin + CSRF |
| DELETE | `/api/files/:id` | Admin + CSRF |

A API JSON usa cookie de sessão HttpOnly, SameSite=Strict e expiração em 8 horas. As senhas usam PBKDF2-HMAC-SHA256 com sal aleatório por usuário. **A rede HTTP não é criptografada; senha e conteúdo são visíveis para quem interceptar o tráfego local.**

`/api/files?page=1&page_size=50` retorna `files`, `total`, `page` e `page_size`. A busca por `q` e o filtro por `category` são aplicados antes da paginação. A chamada sem os parâmetros de paginação mantém o formato original e o teto de 500 itens. `/api/manifest` inclui todos os registros.

`/api/access` informa o modo de acesso e a porta em `lan_enabled`, `scheme` e `port`. Cada item de `access_urls` traz `url` e `kind` (`lan` ou `loopback`). A escuta somente em loopback oferece acesso pelo próprio computador. O QR da tela **Acesso na rede** usa um endereço LAN selecionado.

## Pesquisa aplicada à versão 0.4

- **Servidor único na rede local.** O mesmo processo entrega interface, API e arquivos estáticos na mesma origem. O SQLite e os objetos ficam no disco do host, com backup e restauração pela CLI. Isso permite operar com o roteador fora da internet e dispensa serviços de nuvem, DNS externo e instalação nos clientes. A opção por uma só origem também evita a travessia entre redes que aciona as permissões de acesso local no Chromium. [SQLite](https://www.sqlite.org/wal.html) · [Chrome for Developers](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access?hl=en)
- **Conexão em outro aparelho.** A tela mostra os candidatos IPv4 do host para que a pessoa escolha o endereço de sua rede. O gerador de QR de Kazuhiko Arase, incluído como arquivo estático sob licença MIT, codifica somente a URL LAN. A câmera do celular pode ler o QR. A URL também pode ser selecionada como texto, inclusive no HTTP por IP privado, onde o Clipboard assíncrono fica indisponível. [Gerador QR](https://github.com/kazuhikoarase/qrcode-generator) · [DENSO WAVE](https://www.qrcode.com/en/howto/code.html) · [Apple](https://support.apple.com/en-la/102680) · [Google Pixel](https://support.google.com/pixelcamera/answer/16561572?hl=pt-BR) · [W3C Secure Contexts](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy) · [W3C Clipboard API](https://www.w3.org/TR/clipboard-apis/)
- **Diagnóstico da LAN.** Um IP atribuído dinamicamente pode mudar, e uma reserva DHCP no roteador pode estabilizá-lo. Se o serviço abre no host e falha no celular, confirme a rede Wi‑Fi, o isolamento de clientes do ponto de acesso e a regra de entrada do host. Quando o Windows exigir uma regra, restrinja o escopo ao TCP na porta configurada, ao perfil Privado e à sub-rede local. A conexão por QR, o firewall e o isolamento entre clientes requerem teste físico em uma LAN real com a internet desligada. [IETF RFC 2131](https://www.rfc-editor.org/rfc/rfc2131.html#section-2) · [Microsoft Learn](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=win10-ps) · [Cisco Meraki](https://documentation.meraki.com/Wireless/Operate_and_Maintain/How_Tos/Firewall_and_Traffic_Shaping/Wireless_Client_Isolation)
- **Contas e sessões.** Administradores gerenciam contas de leitura e administração pelo navegador. As operações de escrita exigem CSRF e as sessões afetadas são revogadas ao trocar a senha ou excluir uma conta. A exclusão preserva a conta em uso e pelo menos um administrador. Uma futura tela de sessões pode permitir revogação individual e mostrar a última atividade. [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#simultaneous-session-logons) · [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- **Acessibilidade nos fluxos principais.** Formulários rotulados, editor multilinha, estados textuais de erro e status de busca e envio ajudam na operação por teclado ou tecnologia assistiva. O roteiro de revisão inclui foco visível, alvos de toque de pelo menos 24 × 24 CSS px conforme as exceções da norma, contraste de 4,5:1 para texto comum e leitura sem rolagem horizontal a 320 CSS px. O smoke de navegador verifica os fluxos e as dimensões emuladas. [WCAG 2.2](https://www.w3.org/TR/WCAG22/) · [W3C WAI Status Messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
- **Transporte protegido e descoberta.** A versão usa HTTP local com acesso pelo endereço anunciado. HTTPS em uma LAN isolada pode usar uma CA privada, com instalação e verificação da confiança em cada cliente. mDNS é outra opção para nomes `.local` no mesmo enlace e deve manter o IPv4 como alternativa diante de filtragem multicast ou conflito de nomes. [CA/Browser Forum, § 4.2.2](https://cabforum.org/working-groups/server/baseline-requirements/documents/CA-Browser-Forum-TLS-BR-2.2.5.pdf) · [Caddy, HTTPS local](https://caddyserver.com/docs/automatic-https#local-https) · [Apple, confiança em certificados](https://support.apple.com/en-ie/102390) · [IETF RFC 6762](https://datatracker.ietf.org/doc/html/rfc6762#section-3)

## Estratégia de testes

1. Testes unitários e de integração HTTP: `python -m unittest discover -s tests -v`.
2. Teste de JavaScript: `node --check static/app.js`, quando Node estiver disponível.
3. Teste funcional de navegador opcional com Playwright, usando Chromium instalado pelo Playwright ou Edge com `python tests/test_browser_smoke.py --channel msedge`.
4. Teste real: desligue a internet mantendo Wi-Fi, abra em dois celulares, pesquise, baixe, faça upload, verifique hashes e restaure backup em pasta temporária.
