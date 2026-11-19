#!/usr/bin/env python3
"""Survival Server - Biblioteca e portal familiar 100% offline.
Python >= 3.10, sem dependencias externas. Use somente em rede local confiavel.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import zipfile
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_VERSION = "0.3.0"
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "dados_survival"
PBKDF2_ROUNDS = 350_000
SESSION_SECONDS = 8 * 3600
MAX_JSON = 16 * 1024
MAX_BACKUP_BYTES = 200 * 1024**3
FILE_COLUMNS = ("id", "filename", "mime", "size", "sha256", "category", "description", "created_at")
CATEGORIES = ("Documentos", "Manuais", "Mapas", "Vídeos", "Wikipedia/ZIM", "Guias", "Outros")
INLINE_MIMES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif", "video/mp4", "video/webm", "audio/mpeg", "audio/mp4", "audio/ogg", "text/plain"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','leitor')), created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  digest TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, filename TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, sha256 TEXT NOT NULL, category TEXT NOT NULL,
  description TEXT NOT NULL, search_text TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_date ON files(created_at);
CREATE TABLE IF NOT EXISTS emergency_plan (
  key TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  username TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_date ON audit(ts);
"""


PLAN_KEYS = {
    "contacts": "Contatos essenciais",
    "meeting": "Pontos de encontro",
    "departure": "Plano de evacuação",
    "supplies": "Suprimentos e recursos",
    "instructions": "Instruções para a família",
}


def plan_get(data):
    with contextlib.closing(connect(data)) as con:
        values = {r["key"]: {"body": r["body"], "updated_at": r["updated_at"]} for r in con.execute("SELECT key,body,updated_at FROM emergency_plan")}
    return {k: {"title": title, "body": values.get(k, {}).get("body", ""), "updated_at": values.get(k, {}).get("updated_at", 0)} for k, title in PLAN_KEYS.items()}


def plan_update(data, key, body):
    if key not in PLAN_KEYS:
        raise ValueError("Seção do plano desconhecida")
    if not isinstance(body, str) or len(body) > 4000:
        raise ValueError("O texto deve ter até 4000 caracteres")
    now = utc_ts()
    with contextlib.closing(connect(data)) as con:
        con.execute("INSERT INTO emergency_plan(key,body,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at", (key, body.strip(), now))
        con.commit()
    return now


def record_audit(con, username, action, detail=""):
    con.execute("INSERT INTO audit(ts,username,action,detail) VALUES (?,?,?,?)", (utc_ts(), username[:32], action[:40], detail[:120]))


def audit(data, username, action, detail=""):
    with contextlib.closing(connect(data)) as con:
        record_audit(con, username, action, detail)
        con.commit()


def audit_recent(data, limit=30):
    with contextlib.closing(connect(data)) as con:
        return [dict(x) for x in con.execute("SELECT ts,username,action,detail FROM audit ORDER BY id DESC LIMIT ?", (min(max(int(limit),1),100),))]


def diagnostics(data):
    with contextlib.closing(connect(data)) as con:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        count = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    usage=shutil.disk_usage(data)
    return {"sqlite_integrity": integrity, "files": count, "disk_free_bytes": usage.free, "disk_total_bytes": usage.total, "data_directory": str(data)}


def utc_ts():
    return int(time.time())


def data_paths(data: Path):
    return data / "server.sqlite3", data / "objetos"


def data_lock_path(data: Path):
    return data.with_name(f".{data.name}.survival-lock.sqlite3")


@contextlib.contextmanager
def data_access(data: Path, *, exclusive=False):
    """Coordinate directory swaps across processes without locking the catalog."""
    data.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(data_lock_path(data), timeout=0 if exclusive else 5, check_same_thread=False)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
            # A read starts a shared lock that remains until this transaction closes.
            lock.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                if exclusive:
                    raise ValueError("Pasta de dados em uso. Encerre o servidor e os outros comandos antes de restaurar.") from exc
                raise ValueError("Pasta de dados em uso. Aguarde a restauração terminar.") from exc
            raise
        yield


def connect(data: Path):
    dbpath, _ = data_paths(data)
    con = sqlite3.connect(dbpath, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_db(data: Path):
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    try:
        data.chmod(0o700)
    except OSError:
        pass
    _, objects = data_paths(data)
    objects.mkdir(exist_ok=True)
    with contextlib.closing(connect(data)) as con:
        con.executescript(SCHEMA)
        con.commit()


def validate_username(username):
    if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,32}", username):
        raise ValueError("Usuario deve ter 3 a 32 caracteres: letras, numeros, _, ., -")
    return username


def validate_password(password):
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError("Senha deve conter pelo menos 10 caracteres")
    if len(password) > 256:
        raise ValueError("Senha muito longa")
    return password


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def add_user(data, username, password, role="leitor"):
    validate_username(username)
    validate_password(password)
    if role not in ("admin", "leitor"):
        raise ValueError("Papel invalido")
    salt = secrets.token_hex(16)
    digest = password_hash(password, salt)
    with contextlib.closing(connect(data)) as con:
        con.execute("INSERT INTO users VALUES (?,?,?,?,?)", (username, salt, digest, role, utc_ts()))
        con.commit()


def check_password(data, username, password):
    if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
        return None
    with contextlib.closing(connect(data)) as con:
        row = con.execute("SELECT username,salt,password_hash,role FROM users WHERE username=?", (username,)).fetchone()
    if row and hmac.compare_digest(password_hash(password, row["salt"]), row["password_hash"]):
        return {"username": row["username"], "role": row["role"]}
    return None


def safe_filename(filename):
    if not isinstance(filename, str):
        raise ValueError("Nome de arquivo invalido")
    filename = filename.replace("\\", "/").split("/")[-1]
    filename = "".join(ch for ch in filename if ch.isprintable() and ch not in "\x00\r\n").strip(" .")
    if not filename or filename in (".", ".."):
        raise ValueError("Nome de arquivo invalido")
    if len(filename) > 180:
        raise ValueError("Nome de arquivo muito longo (maximo 180 caracteres)")
    return filename


def sanitize_metadata(category, description):
    if not isinstance(category, str) or category not in CATEGORIES:
        raise ValueError("Categoria invalida")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("Descricao invalida (maximo 500 caracteres)")
    return category, description.strip()


def text_index(path, filename, max_bytes=100_000):
    if Path(filename).suffix.lower() not in (".txt", ".md", ".csv", ".json", ".log"):
        return ""
    try:
        with open(path, "rb") as stream:
            return stream.read(max_bytes).decode("utf-8-sig", errors="replace")
    except OSError:
        return ""


def add_stream(data, filename, stream, length, category="Outros", description="", limit=None, *, username=None):
    filename = safe_filename(filename)
    category, description = sanitize_metadata(category, description)
    if not isinstance(length, int) or length <= 0:
        raise ValueError("O arquivo deve ter tamanho maior que zero")
    if limit is not None and length > limit:
        raise ValueError("Arquivo excede o limite de upload")
    dbpath, objects = data_paths(data)
    if not dbpath.exists():
        raise ValueError("Servidor ainda nao inicializado")
    file_id = secrets.token_hex(16)
    tmp = objects / (".upload-" + file_id)
    dest = objects / file_id
    sha = hashlib.sha256()
    remaining = length
    published = False
    try:
        with open(tmp, "xb") as out:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("Upload interrompido: tamanho incompleto")
                out.write(chunk)
                sha.update(chunk)
                remaining -= len(chunk)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        index = text_index(tmp, filename)
        with contextlib.closing(connect(data)) as con:
            con.execute(
                "INSERT INTO files(id,filename,mime,size,sha256,category,description,search_text,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (file_id, filename, mime, length, sha.hexdigest(), category, description, index, utc_ts()),
            )
            if username is not None:
                record_audit(con, username, "file_upload", filename[:100])
            # Publish under the SQLite write lock, before the row becomes visible.
            os.replace(tmp, dest)
            published = True
            con.commit()
        return file_id
    except Exception:
        tmp.unlink(missing_ok=True)
        if published:
            dest.unlink(missing_ok=True)
        raise


def import_file(data, path, category="Outros", description=""):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.is_symlink():
        raise ValueError("Nao importe links simbolicos")
    # Evita autoimportar arquivos do diretorio gerenciado.
    _, objects = data_paths(data)
    if objects.resolve() in path.resolve().parents:
        raise ValueError("Arquivo ja esta na pasta interna do servidor")
    with path.open("rb") as src:
        return add_stream(data, path.name, src, path.stat().st_size, category, description)


def file_dict(row):
    return {key: row[key] for key in FILE_COLUMNS}


def list_files(data, query="", category="", *, limit=500, offset=0, include_total=False):
    where = []
    values = []
    if category and category != "Todas":
        where.append("category=?")
        values.append(category)
    if query:
        escaped = query[:120].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(filename LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR search_text LIKE ? ESCAPE '\\')")
        values.extend([f"%{escaped}%"] * 3)
    filtered = "FROM files" + (" WHERE " + " AND ".join(where) if where else "")
    sql = f"SELECT {','.join(FILE_COLUMNS)} {filtered} ORDER BY created_at DESC,id DESC"
    page_values = values
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        page_values = [*values, limit, offset]
    with contextlib.closing(connect(data)) as con:
        if include_total:
            con.execute("BEGIN")
            total = con.execute(f"SELECT COUNT(*) {filtered}", values).fetchone()[0]
        files = [file_dict(row) for row in con.execute(sql, page_values)]
    return (files, total) if include_total else files


def stats(data):
    with contextlib.closing(connect(data)) as con:
        r = con.execute("SELECT COUNT(*) AS total, COALESCE(SUM(size),0) AS bytes FROM files").fetchone()
        cats = {x["category"]: x["n"] for x in con.execute("SELECT category,COUNT(*) n FROM files GROUP BY category")}
    return {"files": r["total"], "bytes": r["bytes"], "categories": cats}


def get_file(data, file_id):
    if not re.fullmatch(r"[0-9a-f]{32}", file_id):
        return None
    with contextlib.closing(connect(data)) as con:
        row = con.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    return file_dict(row) if row else None


def write_backup_entry(archive, path, name):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as src, archive.open(name, "w", force_zip64=True) as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def backup_data(data, output):
    data, output = Path(data).resolve(), Path(output)
    resolved_output = output.resolve()
    if resolved_output.is_relative_to(data) or resolved_output == data_lock_path(data):
        raise ValueError("Salve o backup fora da pasta de dados e do arquivo de coordenação")
    dbpath, objects = data_paths(data)
    with data_access(data):
        if not dbpath.is_file():
            raise ValueError("Servidor ainda nao inicializado")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp, contextlib.closing(connect(data)) as guard:
            # Prevent catalog writes and deletions while copying the matching objects.
            guard.execute("BEGIN IMMEDIATE")
            catalog = guard.execute("SELECT id,filename,sha256,size FROM files ORDER BY id").fetchall()
            db_copy = Path(temp) / "server.sqlite3"
            with contextlib.closing(connect(data)) as src, contextlib.closing(sqlite3.connect(db_copy)) as dst:
                src.backup(dst)
                dst.execute("DELETE FROM sessions")
                dst.commit()
            if db_copy.stat().st_size + sum(row["size"] for row in catalog) > MAX_BACKUP_BYTES:
                raise ValueError("O backup excede o limite de 200 GiB")
            entries = {}
            manifest = {"format": "survival-server-backup-v1", "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "entries": entries}
            fd, temp_name = tempfile.mkstemp(prefix=".survival-backup-", suffix=".tmp", dir=output.parent)
            tmp_zip = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as out, zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as z:
                    entries["server.sqlite3"] = write_backup_entry(z, db_copy, "server.sqlite3")
                    for row in catalog:
                        file_id = row["id"]
                        if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{32}", file_id):
                            raise ValueError("Identificador inválido no catálogo")
                        path = objects / file_id
                        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"]:
                            raise ValueError(f"Objeto ausente ou com tamanho incorreto: {row['filename']}")
                        name = f"objetos/{file_id}"
                        entry = write_backup_entry(z, path, name)
                        if entry != {"sha256": row["sha256"], "bytes": row["size"]}:
                            raise ValueError(f"Integridade incorreta: {row['filename']}")
                        entries[name] = entry
                    z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                os.replace(tmp_zip, output)
            finally:
                tmp_zip.unlink(missing_ok=True)
    return len(entries), output


def restore_data(data, archive):
    data, archive = Path(data).resolve(), Path(archive).resolve()
    if not archive.is_file():
        raise ValueError("Arquivo de backup inexistente")
    if archive.is_relative_to(data) or archive == data_lock_path(data):
        raise ValueError("Backup deve estar fora da pasta de dados")
    with data_access(data, exclusive=True):
        return restore_locked(data, archive)


def restore_locked(data, archive):
    stage = Path(tempfile.mkdtemp(prefix="survival_restore_", dir=data.parent))
    try:
        with zipfile.ZipFile(archive) as z:
            filenames = z.namelist()
            if len(filenames) != len(set(filenames)):
                raise ValueError("Backup invalido: arquivos duplicados")
            if "manifest.json" not in filenames:
                raise ValueError("Manifesto do backup ausente")
            manifest = json.loads(z.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != "survival-server-backup-v1":
                raise ValueError("Formato de backup nao reconhecido")
            entries = manifest.get("entries")
            if not isinstance(entries, dict) or "server.sqlite3" not in entries or set(filenames) != set(entries) | {"manifest.json"}:
                raise ValueError("Lista de arquivos do backup inconsistente")
            total_bytes = 0
            for name, meta in entries.items():
                if name != "server.sqlite3" and not re.fullmatch(r"objetos/[0-9a-f]{32}", name):
                    raise ValueError("Caminho nao permitido no backup")
                if (not isinstance(meta, dict) or type(meta.get("bytes")) is not int or meta["bytes"] < 0
                        or not isinstance(meta.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])):
                    raise ValueError("Metadados de backup invalidos")
                info = z.getinfo(name)
                total_bytes += info.file_size
                if info.file_size != meta["bytes"] or total_bytes > MAX_BACKUP_BYTES:
                    raise ValueError("Tamanho invalido do backup")
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                copied = 0
                with z.open(name) as src, target.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > meta["bytes"]:
                            raise ValueError(f"Tamanho incorreto: {name}")
                        digest.update(chunk)
                        dst.write(chunk)
                if copied != meta["bytes"] or not hmac.compare_digest(digest.hexdigest(), meta["sha256"]):
                    raise ValueError(f"Integridade incorreta: {name}")
        # Check the legacy core schema before applying the additive migrations.
        with contextlib.closing(sqlite3.connect(stage / "server.sqlite3")) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Banco SQLite danificado")
            con.execute("SELECT username,salt,password_hash,role,created_at FROM users LIMIT 0")
            con.execute("SELECT digest,username,csrf,expires_at FROM sessions LIMIT 0")
            con.execute(f"SELECT {','.join(FILE_COLUMNS)},search_text FROM files LIMIT 0")
            for file_id, digest, size in con.execute("SELECT id,sha256,size FROM files"):
                if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{32}", file_id):
                    raise ValueError("Identificador inválido no catálogo do backup")
                entry = entries.get(f"objetos/{file_id}")
                if entry is None or entry["sha256"] != digest or entry["bytes"] != size:
                    raise ValueError(f"Objeto ausente ou divergente no catálogo do backup: {file_id}")
            con.execute("DELETE FROM sessions")
            con.commit()
        ensure_db(stage)
        old = None
        if data.exists():
            old = data.with_name(data.name + ".anterior_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3))
            os.replace(data, old)
        try:
            os.replace(stage, data)
        except Exception:
            if old:
                os.replace(old, data)
            raise
        return old
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_data(data):
    dbpath, objects = data_paths(data)
    if not dbpath.exists():
        raise ValueError("Servidor nao inicializado")
    failures = []
    with contextlib.closing(connect(data)) as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("SELECT id,filename,sha256,size FROM files").fetchall()
        for row in rows:
            file_id = row["id"]
            if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{32}", file_id):
                failures.append(row["filename"] + " (identificador inválido)")
                continue
            path = objects / file_id
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"]:
                    failures.append(row["filename"] + " (ausente ou tamanho errado)")
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as f:
                    while chunk := f.read(1024 * 1024):
                        digest.update(chunk)
            except OSError:
                failures.append(row["filename"] + " (falha de leitura)")
                continue
            if digest.hexdigest() != row["sha256"]:
                failures.append(row["filename"] + " (SHA-256 divergente)")
    return len(rows), failures


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class LoginLimiter:
    def __init__(self):
        self.bad = {}
        self.lock = threading.Lock()

    def is_blocked(self, ip):
        now = time.monotonic()
        with self.lock:
            arr = [t for t in self.bad.get(ip, []) if now - t < 300]
            self.bad[ip] = arr
            return len(arr) >= 5

    def fail(self, ip):
        with self.lock:
            self.bad.setdefault(ip, []).append(time.monotonic())

    def clear(self, ip):
        with self.lock:
            self.bad.pop(ip, None)


class SurvivalHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, address, data, upload_limit_mb=512):
        self.data_dir = Path(data).resolve()
        self._storage = contextlib.ExitStack()
        with contextlib.ExitStack() as startup:
            startup.enter_context(data_access(self.data_dir))
            ensure_db(self.data_dir)
            self.upload_limit = int(upload_limit_mb) * 1024 * 1024
            self.login_limiter = LoginLimiter()
            super().__init__(address, SurvivalHandler)
            self._storage = startup.pop_all()

    def server_close(self):
        try:
            super().server_close()
        finally:
            self._storage.close()


class SurvivalHandler(BaseHTTPRequestHandler):
    server_version = f"SurvivalServer/{APP_VERSION}"
    sys_version = ""
    timeout = 30

    def log_message(self, fmt, *args):
        safe = re.sub(r"[\x00-\x1f\x7f]", "?", fmt % args)
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {self.client_address[0]} {safe}")

    def _headers(self, code, ctype="application/json; charset=utf-8", length=0, more=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(),camera=(),microphone=()")
        headers = {"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}
        headers.update(more or {})
        for key, val in headers.items():
            self.send_header(key, str(val))
        self.end_headers()

    def _json(self, code, obj, more=None):
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers(code, length=len(payload), more=more)
        self.wfile.write(payload)

    def _error(self, code, msg):
        self.close_connection = True
        self._json(code, {"error": msg})

    def _host_valid(self):
        host = self.headers.get("Host", "")
        try:
            parsed = urllib.parse.urlsplit("http://" + host)
            name = (parsed.hostname or "").lower()
            if not name or parsed.username or parsed.password or parsed.path or parsed.query:
                return False
            if name == "localhost" or name.endswith(".local"):
                return True
            addr = ipaddress.ip_address(name)
            return addr.is_private or addr.is_loopback or addr.is_link_local
        except (ValueError, AttributeError):
            return False

    def _origin_valid(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in ("http://" + self.headers.get("Host", ""), "https://" + self.headers.get("Host", ""))

    def _body_json(self, limit=MAX_JSON):
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise ApiError(400, "Content-Length invalido")
        if length < 0 or length > limit:
            raise ApiError(413, "JSON muito grande ou tamanho ausente")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ApiError(400, "JSON invalido")
        if not isinstance(obj, dict):
            raise ApiError(400, "JSON deve ser um objeto")
        return obj

    def _session(self, required=True, admin=False, csrf=False):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        item = cookie.get("survival_session")
        token = item.value if item else ""
        if not token or len(token) > 128:
            if required:
                raise ApiError(401, "Faça login para continuar")
            return None
        digest = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
        with contextlib.closing(connect(self.server.data_dir)) as con:
            row = con.execute("SELECT sessions.csrf,sessions.expires_at,users.username,users.role FROM sessions JOIN users ON sessions.username=users.username WHERE digest=?", (digest,)).fetchone()
        if not row or row["expires_at"] <= utc_ts():
            if required:
                raise ApiError(401, "Sessao expirada; entre novamente")
            return None
        if admin and row["role"] != "admin":
            raise ApiError(403, "Somente administradores")
        if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), row["csrf"]):
            raise ApiError(403, "Token CSRF ausente ou invalido")
        return {"username": row["username"], "role": row["role"], "csrf": row["csrf"], "digest": digest}

    def _route(self, method):
        if not self._host_valid():
            raise ApiError(400, "Endereco Host nao autorizado")
        if method in ("POST", "PUT", "PATCH", "DELETE") and not self._origin_valid():
            raise ApiError(403, "Origem nao autorizada")
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if method == "GET" and path in ("/", "/index.html", "/app.css", "/app.js"):
            return self._static("index.html" if path == "/" else path.lstrip("/"))
        if method == "GET" and path == "/api/health":
            return self._json(200, {"ok": True, "name": "Survival Server", "version": APP_VERSION})
        if method == "POST" and path == "/api/login":
            return self._login()
        if method == "GET" and path == "/api/me":
            session = self._session(required=False)
            return self._json(200, {"session": {"username": session["username"], "role": session["role"], "csrf": session["csrf"]} if session else None})
        if method == "POST" and path == "/api/logout":
            session = self._session(csrf=True)
            with contextlib.closing(connect(self.server.data_dir)) as con:
                con.execute("DELETE FROM sessions WHERE digest=?", (session["digest"],))
                con.commit()
            return self._json(200, {"ok": True}, {"Set-Cookie": "survival_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        self._session()
        if method == "GET" and path == "/api/stats":
            return self._json(200, stats(self.server.data_dir))
        if method == "GET" and path == "/api/emergency":
            return self._json(200, {"plan": plan_get(self.server.data_dir)})
        if method == "PUT" and path.startswith("/api/emergency/"):
            self._session(admin=True, csrf=True)
            key = path.rsplit("/", 1)[-1]
            obj = self._body_json(limit=32 * 1024)
            try: updated = plan_update(self.server.data_dir, key, obj.get("body"))
            except ValueError as ex: raise ApiError(400, str(ex))
            session=self._session()
            audit(self.server.data_dir, session["username"], "plan_change", key)
            return self._json(200, {"ok": True, "updated_at": updated})
        if method == "GET" and path == "/api/diagnostics":
            self._session(admin=True)
            return self._json(200, diagnostics(self.server.data_dir))
        if method == "POST" and path == "/api/verify":
            self._session(admin=True,csrf=True)
            total, failures = verify_data(self.server.data_dir)
            return self._json(200, {"ok": len(failures)==0, "files":total, "failures":failures[:100], "truncated":len(failures)>100})
        if method == "GET" and path == "/api/audit":
            self._session(admin=True)
            return self._json(200, {"events":audit_recent(self.server.data_dir)})
        if method == "GET" and path == "/api/manifest":
            return self._json(200, {"format":"survival-server-manifest-v1", "created_utc":dt.datetime.now(dt.timezone.utc).isoformat(), "files":list_files(self.server.data_dir, limit=None)})
        if method == "GET" and path == "/api/files":
            q = params.get("q", [""])[0]
            category = params.get("category", [""])[0]
            if category and category not in CATEGORIES and category != "Todas":
                raise ApiError(400, "Categoria invalida")
            if "page" in params or "page_size" in params:
                try:
                    page = int(params.get("page", ["1"])[0])
                    page_size = int(params.get("page_size", ["50"])[0])
                    if not (1 <= page <= 2**31 - 1 and 1 <= page_size <= 500):
                        raise ValueError
                except ValueError:
                    raise ApiError(400, "Página inválida ou tamanho de página fora do intervalo de 1 a 500")
                files, total = list_files(self.server.data_dir, q, category, limit=page_size,
                                          offset=(page - 1) * page_size, include_total=True)
                return self._json(200, {"files": files, "total": total, "page": page, "page_size": page_size})
            return self._json(200, {"files": list_files(self.server.data_dir, q, category)})
        if method == "POST" and path == "/api/files/upload":
            session = self._session(admin=True, csrf=True)
            return self._upload(params, session["username"])
        match = re.fullmatch(r"/api/files/([0-9a-f]{32})(?:/(download|view|metadata))?", path)
        if match:
            file_id, action = match.groups()
            if method == "GET" and action in ("download", "view"):
                return self._send_file(file_id, inline=action == "view")
            if method == "DELETE" and not action:
                self._session(admin=True, csrf=True)
                return self._delete(file_id)
            if method == "PUT" and action == "metadata":
                self._session(admin=True, csrf=True)
                obj = self._body_json()
                try:
                    cat, description = sanitize_metadata(obj.get("category", ""), obj.get("description", ""))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                with contextlib.closing(connect(self.server.data_dir)) as con:
                    res = con.execute("UPDATE files SET category=?, description=? WHERE id=?", (cat, description, file_id))
                    con.commit()
                if res.rowcount == 0:
                    raise ApiError(404, "Arquivo nao encontrado")
                return self._json(200, {"ok": True})
        raise ApiError(404, "Recurso nao encontrado")

    def _static(self, name):
        if name not in ("index.html", "app.css", "app.js"):
            raise ApiError(404, "Nao encontrado")
        path = ROOT / "static" / name
        content = path.read_bytes()
        ctype = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8"}[name]
        csp = "default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        self._headers(200, ctype, len(content), {"Content-Security-Policy": csp})
        self.wfile.write(content)

    def _login(self):
        ip = self.client_address[0]
        if self.server.login_limiter.is_blocked(ip):
            raise ApiError(429, "Muitas tentativas. Aguarde cinco minutos")
        obj = self._body_json()
        user = check_password(self.server.data_dir, obj.get("username"), obj.get("password"))
        if not user:
            self.server.login_limiter.fail(ip)
            raise ApiError(401, "Usuario ou senha incorretos")
        self.server.login_limiter.clear(ip)
        audit(self.server.data_dir, user["username"], "login", "successful")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with contextlib.closing(connect(self.server.data_dir)) as con:
            con.execute("DELETE FROM sessions WHERE expires_at<=?", (utc_ts(),))
            con.execute("INSERT INTO sessions(digest,username,csrf,expires_at) VALUES (?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["username"], csrf, utc_ts() + SESSION_SECONDS))
            con.commit()
        cookie = f"survival_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
        return self._json(200, {"username": user["username"], "role": user["role"]}, {"Set-Cookie": cookie})

    def _upload(self, params, username):
        filename = params.get("filename", [""])[0]
        category = params.get("category", ["Outros"])[0]
        description = params.get("description", [""])[0]
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise ApiError(400, "Tamanho invalido")
        if size <= 0:
            raise ApiError(400, "Arquivo vazio ou Content-Length ausente")
        if size > self.server.upload_limit:
            raise ApiError(413, f"Limite do upload: {self.server.upload_limit // 1024**2} MB. Para ZIM grande, use o comando import-file")
        try:
            file_id = add_stream(self.server.data_dir, filename, self.rfile, size, category, description,
                                 self.server.upload_limit, username=username)
        except (ValueError, OSError) as exc:
            raise ApiError(400, str(exc))
        return self._json(201, {"ok": True, "id": file_id})

    def _send_file(self, file_id, inline=False):
        meta = get_file(self.server.data_dir, file_id)
        if not meta:
            raise ApiError(404, "Arquivo nao encontrado")
        path = self.server.data_dir / "objetos" / file_id
        if not path.is_file() or path.is_symlink():
            raise ApiError(404, "Arquivo ausente no armazenamento")
        size = path.stat().st_size
        if size != meta["size"]:
            raise ApiError(409, "Integridade do tamanho do arquivo nao confere")
        safe_inline = inline and meta["mime"] in INLINE_MIMES
        disposition = "inline" if safe_inline else "attachment"
        filename = meta["filename"]
        encoded = urllib.parse.quote(filename, safe="")
        headers = {
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}",
            "Accept-Ranges": "bytes",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
        start, end = 0, size - 1
        range_header = self.headers.get("Range", "")
        code = 200
        if range_header:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not m or (not m[1] and not m[2]):
                raise ApiError(416, "Intervalo de bytes invalido")
            if not m[1]:
                suffix = int(m[2])
                if suffix == 0:
                    raise ApiError(416, "Intervalo de bytes invalido")
                start = max(0, size - suffix)
            else:
                start = int(m[1])
                if m[2]:
                    end = min(size - 1, int(m[2]))
            if start > end or start >= size:
                raise ApiError(416, "Intervalo indisponivel")
            code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        remaining = end - start + 1
        self._headers(code, meta["mime"], remaining, headers)
        try:
            with path.open("rb") as f:
                f.seek(start)
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _delete(self, file_id):
        with contextlib.closing(connect(self.server.data_dir)) as con:
            r = con.execute("DELETE FROM files WHERE id=?", (file_id,))
            con.commit()
        if r.rowcount == 0:
            raise ApiError(404, "Arquivo nao encontrado")
        (self.server.data_dir / "objetos" / file_id).unlink(missing_ok=True)
        audit(self.server.data_dir, self._session()["username"], "file_delete", file_id)
        return self._json(200, {"ok": True})

    def _safe_dispatch(self, method):
        try:
            self._route(method)
        except ApiError as exc:
            self._error(exc.code, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._error(500, "Erro interno. Veja o terminal do servidor")
            except OSError:
                pass

    def do_GET(self):
        self._safe_dispatch("GET")

    def do_POST(self):
        self._safe_dispatch("POST")

    def do_PUT(self):
        self._safe_dispatch("PUT")

    def do_DELETE(self):
        self._safe_dispatch("DELETE")


def local_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "<IP_LOCAL>"


def main(argv=None):
    p = argparse.ArgumentParser(description="Survival Server - biblioteca offline para sua familia")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Pasta de dados (padrao: ./dados_survival)")
    sp = p.add_subparsers(dest="cmd", required=True)
    init = sp.add_parser("init", help="Configurar usuarios e biblioteca inicial")
    init.add_argument("--vazio", action="store_true", help="Nao importar os guias de exemplo")
    init.add_argument("--if-needed", action="store_true", help="Pular se a pasta já tiver usuários")
    add = sp.add_parser("add-user", help="Criar usuario adicional")
    add.add_argument("username")
    add.add_argument("--role", choices=("admin", "leitor"), default="leitor")
    run = sp.add_parser("run", help="Executar o servidor")
    run.add_argument("--lan", action="store_true", help="Permitir acesso pela rede Wi-Fi local")
    run.add_argument("--host", default=None, help="IP ao qual vincular (avancado)")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--max-upload-mb", type=int, default=512)
    imp = sp.add_parser("import-file", help="Importar arquivo, inclusive ZIM grande")
    imp.add_argument("path", type=Path)
    imp.add_argument("--category", choices=CATEGORIES, default="Outros")
    imp.add_argument("--description", default="")
    back = sp.add_parser("backup", help="Criar ZIP de backup (SEM criptografia)")
    back.add_argument("path", type=Path, nargs="?")
    restore = sp.add_parser("restore", help="Restaurar ZIP de backup (servidor DESLIGADO)")
    restore.add_argument("path", type=Path)
    restore.add_argument("--yes", action="store_true")
    sp.add_parser("verify", help="Validar SHA-256 de todos os arquivos")
    sp.add_parser("doctor", help="Checar SQLite, espaco livre e integridade dos arquivos")
    args = p.parse_args(argv)
    data = args.data.resolve()
    if args.cmd == "restore":
        if not args.yes:
            p.error("A restauracao substitui os dados atuais. Execute com --yes e o servidor desligado")
        previous = restore_data(data, args.path)
        print("Backup restaurado com sucesso.")
        if previous:
            print(f"Dados anteriores preservados em: {previous}")
        return 0
    if args.cmd in ("run", "backup"):
        return run_command(p, args, data)
    with data_access(data):
        return run_command(p, args, data)


def run_command(p, args, data):
    if args.cmd == "init":
        ensure_db(data)
        with contextlib.closing(connect(data)) as con:
            exists = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if exists:
            if args.if_needed:
                return 0
            p.error("A pasta ja tem usuarios. Use add-user ou escolha outra pasta --data")
        print(f"\nSURVIVAL SERVER {APP_VERSION} - Configuracao inicial\n")
        pw = getpass.getpass("Crie a SENHA do administrador (min. 10 caracteres): ")
        verify = getpass.getpass("Repita a senha: ")
        if pw != verify:
            p.error("As senhas nao coincidem")
        validate_password(pw)
        answer = input("Criar usuario de leitura 'familia'? [S/n] ").strip().lower()
        reader_pw = None
        if answer not in ("n", "nao", "não"):
            reader_pw = getpass.getpass("Senha para 'familia' (min. 10 caracteres): ")
            validate_password(reader_pw)
        add_user(data, "admin", pw, "admin")
        print("Administrador 'admin' criado.")
        if reader_pw is not None:
            add_user(data, "familia", reader_pw, "leitor")
        if not args.vazio:
            for example in sorted((ROOT / "exemplos").glob("*.md")):
                import_file(data, example, "Guias", "Guia inicial editavel de preparacao")
        print(f"\nPronto. Dados em: {data}\nPara iniciar: python server.py run --lan\n")
    else:
        dbpath, _ = data_paths(data)
        if not dbpath.exists():
            p.error("Pasta nao inicializada. Primeiro execute: python server.py init")
        if args.cmd == "add-user":
            pw = getpass.getpass(f"Senha para {args.username}: ")
            confirm = getpass.getpass("Confirme a senha: ")
            if pw != confirm:
                p.error("Senhas nao coincidem")
            add_user(data, args.username, pw, args.role)
            print("Usuario criado")
        elif args.cmd == "run":
            if args.port < 1024 or args.port > 65535 or args.max_upload_mb <= 0:
                p.error("Porta deve ser 1024..65535 e upload positivo")
            host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
            server = SurvivalHTTPServer((host, args.port), data, args.max_upload_mb)
            print(f"\nSURVIVAL SERVER {APP_VERSION}")
            print(f"Este computador: http://127.0.0.1:{args.port}")
            if host != "127.0.0.1":
                print(f"Outros aparelhos na mesma rede: http://{local_address()}:{args.port}")
            print("AVISO: HTTP local nao e criptografado. Use somente Wi-Fi de confianca; NUNCA exponha a internet.")
            print("Para parar: Ctrl+C\n")
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                print("\nServidor encerrado")
            finally:
                server.server_close()
        elif args.cmd == "import-file":
            fid = import_file(data, args.path, args.category, args.description)
            print(f"Importado: {args.path.name} | ID {fid}")
        elif args.cmd == "backup":
            path = args.path or (data.parent / ("SurvivalServer_Backup_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"))
            qty, output = backup_data(data, path)
            print(f"Backup gerado: {output} ({qty} itens incluindo banco de dados). ATENCAO: NAO CRIPTOGRAFADO")
        elif args.cmd == "doctor":
            report=diagnostics(data)
            total,failures=verify_data(data)
            report["verified_files"]=total;report["failed_hashes"]=failures
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if report["sqlite_integrity"]=="ok" and not failures else 2
        elif args.cmd == "verify":
            total, failures = verify_data(data)
            print(f"Verificados: {total} arquivo(s). Falhas: {len(failures)}")
            for failure in failures:
                print("-", failure)
            if failures:
                return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)
