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

## Estratégia de testes

1. Testes unitários e de integração HTTP: `python -m unittest discover -s tests -v`.
2. Teste de JavaScript: `node --check static/app.js`, quando Node estiver disponível.
3. Teste funcional de navegador opcional com Playwright, usando Chromium instalado pelo Playwright ou Edge com `python tests/test_browser_smoke.py --channel msedge`.
4. Teste real: desligue a internet mantendo Wi-Fi, abra em dois celulares, pesquise, baixe, faça upload, verifique hashes e restaure backup em pasta temporária.
