"""Transactional catalog; legacy bytes are immutable and never moved/deleted."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import uuid
import warnings

from PIL import Image, UnidentifiedImageError


class StorageError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status, self.code = status, code


def safe_path(value, allow_empty=False):
    if value == "" and allow_empty:
        return ""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise StorageError("Invalid relative path")
    if value.startswith("/") or "\\" in value or ":" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise StorageError("Invalid relative path")
    for part in value.split("/"):
        if not part or part.startswith(".") or len(part) > 255 or part.rstrip(" .") != part:
            raise StorageError("Invalid relative path")
        if re.match(r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", part, re.I):
            raise StorageError("Reserved path component")
    return value


def safe_name(value):
    value = safe_path(value)
    if "/" in value:
        raise StorageError("Name must be a single component")
    return value


def contained(root, relative):
    relative = safe_path(relative)
    root = Path(root).resolve()
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        if path.is_symlink():
            raise StorageError("Symlinks are forbidden", 403, "forbidden_path")
    if not path.resolve().is_relative_to(root):
        raise StorageError("Path outside storage", 403, "forbidden_path")
    return path


def inspect_image(path, strict=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                fmt = image.format
                if fmt not in {"JPEG", "PNG", "GIF", "WEBP", "BMP", "ICO", "TIFF"}:
                    raise ValueError("Unsupported format")
                if image.width * image.height > 40_000_000:
                    raise ValueError("Too many pixels")
                image.verify()
            with Image.open(path) as image:
                total = 0
                for frame in range(getattr(image, "n_frames", 1)):
                    if frame >= 200:
                        raise ValueError("Too many frames")
                    image.seek(frame)
                    total += image.width * image.height
                    if total > 80_000_000:
                        raise ValueError("Too many pixels")
                    image.load()
            return {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp", "BMP": "image/bmp", "ICO": "image/x-icon", "TIFF": "image/tiff"}[fmt], True
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        if strict:
            raise StorageError("A valid raster image within pixel/frame limits is required") from None
        return "application/octet-stream", False


class Store:
    def __init__(self, legacy_root, state_root, max_file_bytes=100 * 1024**2, max_storage_bytes=10 * 1024**3):
        self.legacy, self.state = Path(legacy_root).resolve(), Path(state_root).resolve()
        if self.state == self.legacy or self.state.is_relative_to(self.legacy) or self.legacy.is_relative_to(self.state):
            raise RuntimeError("State and legacy storage must be separate directories")
        if not self.legacy.is_dir():
            raise RuntimeError("Legacy storage must exist")
        self.blobs, self.staging = self.state / "blobs", self.state / "staging"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state / "catalog.sqlite3"
        self.max_file_bytes, self.max_storage_bytes = max_file_bytes, max_storage_bytes
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_path TEXT NOT NULL,
                    source_kind TEXT NOT NULL, source_path TEXT NOT NULL,
                    media_type TEXT NOT NULL, is_image INTEGER NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, modified_at REAL NOT NULL, deleted_at REAL);
                CREATE TABLE IF NOT EXISTS paths (
                    path TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS assets_created ON assets(created_at,id);
                CREATE INDEX IF NOT EXISTS paths_asset ON paths(asset_id);
            """)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def import_legacy(self):
        """One-time additive cataloging, all-or-nothing; no original bytes change."""
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='legacy_import_complete'").fetchone():
                return 0
            count = 0
            for path in sorted(self.legacy.rglob("*")):
                rel = path.relative_to(self.legacy).as_posix()
                if rel == ".gitkeep":
                    continue
                safe_path(rel)
                contained(self.legacy, rel)
                if path.is_dir():
                    db.execute("INSERT OR IGNORE INTO folders VALUES (?,?)", (rel, path.stat().st_mtime))
                    continue
                if not path.is_file():
                    raise StorageError("Unsupported legacy entry; migration stopped")
                with path.open("rb") as f:
                    digest = hashlib.file_digest(f, "sha256").hexdigest()
                stat = path.stat()
                mime, image = inspect_image(path)
                asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "davallka:legacy:" + rel))
                db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,1,?,?,NULL)", (asset_id, path.name, rel, "legacy", rel, mime, image, stat.st_size, digest, stat.st_mtime, stat.st_mtime))
                db.execute("INSERT INTO paths VALUES (?,?,1)", (rel, asset_id))
                count += 1
            db.execute("INSERT INTO metadata VALUES ('legacy_import_complete',?)", (str(count),))
            return count

    @staticmethod
    def row(db, asset_id):
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise StorageError("Asset not found", 404, "not_found")
        return dict(row)

    def get(self, asset_id):
        with self.connection() as db:
            return self.row(db, asset_id)

    def lookup(self, path):
        safe_path(path)
        with self.connection() as db:
            row = db.execute("SELECT a.*,p.enabled FROM paths p JOIN assets a ON a.id=p.asset_id WHERE p.path=?", (path,)).fetchone()
            if row is None:
                raise StorageError("File not found", 404, "not_found")
            if not row["enabled"] or row["deleted_at"] is not None:
                raise StorageError("File no longer available", 410, "gone")
            return dict(row)

    def file_path(self, asset):
        path = contained(self.legacy if asset["source_kind"] == "legacy" else self.blobs, asset["source_path"])
        if not path.is_file():
            raise StorageError("Stored file unavailable", 404, "not_found")
        return path

    def list(self, limit=50, offset=0, folder=None, deleted=False, images_only=True, older_than=None):
        if not 1 <= limit <= 200 or not 0 <= offset <= 2**31 - 1:
            raise StorageError("Invalid pagination")
        clauses, params = ["deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"], []
        if images_only:
            clauses.append("is_image=1")
        if folder is not None:
            safe_path(folder, allow_empty=True)
            if folder:
                prefix = folder + "/"
                clauses.append("substr(canonical_path,1,?)=? AND instr(substr(canonical_path,?),'/')=0")
                params.extend([len(prefix), prefix, len(prefix) + 1])
            else:
                clauses.append("instr(canonical_path,'/')=0")
        if older_than is not None:
            clauses.append("created_at < ?")
            params.append(older_than)
        where = " AND ".join(clauses)
        with self.connection() as db:
            count = db.execute("SELECT count(*) FROM assets WHERE " + where, params).fetchone()[0]
            rows = [dict(r) for r in db.execute("SELECT * FROM assets WHERE " + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?", params + [limit, offset])]
        return {"items": rows, "total": count, "limit": limit, "offset": offset}

    @staticmethod
    def ensure_parents(db, path):
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            parent = "/".join(parts[:i])
            if db.execute("SELECT 1 FROM paths WHERE path=?", (parent,)).fetchone():
                raise StorageError("Parent path reserved by a file", 409, "conflict")
            db.execute("INSERT OR IGNORE INTO folders VALUES (?,?)", (parent, time.time()))

    def upload(self, stream, name, path=None, idempotency_key=None, strict_image=True, choose_suffix=False):
        name, path = safe_name(name), safe_path(path or name)
        if idempotency_key and (len(idempotency_key) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", idempotency_key)):
            raise StorageError("Invalid idempotency key")
        fd, staging = tempfile.mkstemp(dir=self.staging)
        temp_path = Path(staging)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as f:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise StorageError("File too large", 413, "too_large")
                    digest.update(chunk)
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            mime, image = inspect_image(temp_path, strict=strict_image)
            sha = digest.hexdigest()
            request_digest = hashlib.sha256((sha + "\0" + name + "\0" + path).encode()).hexdigest()
            with self.connection(write=True) as db:
                if idempotency_key:
                    old = db.execute("SELECT * FROM idempotency WHERE key=?", (idempotency_key,)).fetchone()
                    if old:
                        if old["digest"] != request_digest:
                            raise StorageError("Idempotency key reused with different input", 409, "conflict")
                        return self.row(db, old["asset_id"]), False
                if choose_suffix:
                    original, counter = PurePosixPath(path), 1
                    while db.execute("SELECT 1 FROM paths WHERE path=?", (path,)).fetchone() or db.execute("SELECT 1 FROM folders WHERE path=?", (path,)).fetchone():
                        path = str(original.with_name(f"{original.stem}_{counter}{original.suffix}"))
                        counter += 1
                if db.execute("SELECT 1 FROM paths WHERE path=?", (path,)).fetchone() or db.execute("SELECT 1 FROM folders WHERE path=?", (path,)).fetchone():
                    raise StorageError("Path reserved", 409, "conflict")
                used = db.execute("SELECT coalesce(sum(size),0) FROM assets").fetchone()[0]
                if used + size > self.max_storage_bytes:
                    raise StorageError("Storage quota exceeded", 507, "quota_exceeded")
                self.ensure_parents(db, path)
                asset_id, now = str(uuid.uuid4()), time.time()
                os.replace(temp_path, self.blobs / asset_id)
                if os.name != "nt":
                    dir_fd = os.open(self.blobs, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,1,?,?,NULL)", (asset_id, name, path, "blob", asset_id, mime, image, size, sha, now, now))
                db.execute("INSERT INTO paths VALUES (?,?,1)", (path, asset_id))
                if idempotency_key:
                    db.execute("INSERT INTO idempotency VALUES (?,?,?)", (idempotency_key, asset_id, request_digest))
                return self.row(db, asset_id), True
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def check_version(asset, version):
        if type(version) is not int or version != asset["version"]:
            raise StorageError("Expected version does not match", 409, "version_conflict")

    def mutate(self, asset_id, operation, version, value=None, keep_old_alias=True):
        with self.connection(write=True) as db:
            asset = self.row(db, asset_id)
            self.check_version(asset, version)
            if operation == "restore":
                if asset["deleted_at"] is None:
                    return asset
                self.file_path(asset)
                db.execute("UPDATE assets SET deleted_at=NULL,version=version+1,modified_at=? WHERE id=?", (time.time(), asset_id))
            else:
                if asset["deleted_at"] is not None:
                    raise StorageError("Asset deleted", 410, "gone")
                if operation == "rename":
                    db.execute("UPDATE assets SET name=?,version=version+1,modified_at=? WHERE id=?", (safe_name(value), time.time(), asset_id))
                elif operation in {"path", "browser_rename"}:
                    path = safe_path(value) if operation == "path" else str(PurePosixPath(asset["canonical_path"]).with_name(safe_name(value)))
                    if path == asset["canonical_path"]:
                        return asset
                    existing = db.execute("SELECT * FROM paths WHERE path=?", (path,)).fetchone()
                    if (existing and existing["asset_id"] != asset_id) or db.execute("SELECT 1 FROM folders WHERE path=?", (path,)).fetchone():
                        raise StorageError("Path reserved", 409, "conflict")
                    self.ensure_parents(db, path)
                    db.execute("UPDATE paths SET enabled=? WHERE path=?", (int(keep_old_alias), asset["canonical_path"]))
                    db.execute("INSERT INTO paths VALUES (?,?,1) ON CONFLICT(path) DO UPDATE SET enabled=1", (path, asset_id))
                    db.execute("UPDATE assets SET canonical_path=?,version=version+1,modified_at=? WHERE id=?", (path, time.time(), asset_id))
                    if operation == "browser_rename":
                        db.execute("UPDATE assets SET name=? WHERE id=?", (value, asset_id))
                elif operation == "delete":
                    db.execute("UPDATE assets SET deleted_at=?,version=version+1,modified_at=? WHERE id=?", (time.time(), time.time(), asset_id))
                else:
                    raise StorageError("Unknown operation")
            return self.row(db, asset_id)

    def browse(self, folder=""):
        safe_path(folder, allow_empty=True)
        with self.connection() as db:
            if folder and not db.execute("SELECT 1 FROM folders WHERE path=?", (folder,)).fetchone():
                raise StorageError("Folder not found", 404, "not_found")
            files = [dict(r) for r in db.execute("SELECT * FROM assets WHERE deleted_at IS NULL ORDER BY modified_at DESC") if str(PurePosixPath(r["canonical_path"]).parent) == (folder or ".")]
            folders = [{"name": PurePosixPath(r["path"]).name, "path": r["path"], "modified": r["created_at"]} for r in db.execute("SELECT * FROM folders ORDER BY created_at DESC") if str(PurePosixPath(r["path"]).parent) == (folder or ".")]
        return files, folders

    def create_folder(self, path):
        safe_path(path)
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM folders WHERE path=?", (path,)).fetchone() or db.execute("SELECT 1 FROM paths WHERE path=?", (path,)).fetchone():
                raise StorageError("Path reserved", 409, "conflict")
            self.ensure_parents(db, path)
            db.execute("INSERT INTO folders VALUES (?,?)", (path, time.time()))

    def delete_folder(self, path):
        safe_path(path)
        with self.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM folders WHERE path=?", (path,)).fetchone():
                raise StorageError("Folder not found", 404, "not_found")
            if any(r[0].startswith(path + "/") for r in db.execute("SELECT canonical_path FROM assets WHERE deleted_at IS NULL")) or any(r[0].startswith(path + "/") for r in db.execute("SELECT path FROM folders")):
                raise StorageError("Folder not empty", 409, "conflict")
            db.execute("DELETE FROM folders WHERE path=?", (path,))

    def rename_folder(self, path, new_name):
        safe_path(path)
        safe_name(new_name)
        target = str(PurePosixPath(path).with_name(new_name))
        with self.connection(write=True) as db:
            folders = [r[0] for r in db.execute("SELECT path FROM folders") if r[0] == path or r[0].startswith(path + "/")]
            if path not in folders:
                raise StorageError("Folder not found", 404, "not_found")
            if target == path:
                return
            assets = [dict(r) for r in db.execute("SELECT * FROM assets") if r["canonical_path"].startswith(path + "/")]
            for old in folders + [a["canonical_path"] for a in assets]:
                new = target + old[len(path):]
                if db.execute("SELECT 1 FROM paths WHERE path=?", (new,)).fetchone() or db.execute("SELECT 1 FROM folders WHERE path=?", (new,)).fetchone():
                    raise StorageError("Destination reserved", 409, "conflict")
            for old in folders:
                db.execute("UPDATE folders SET path=? WHERE path=?", (target + old[len(path):], old))
            for asset in assets:
                new = target + asset["canonical_path"][len(path):]
                db.execute("INSERT INTO paths VALUES (?,?,1)", (new, asset["id"]))
                db.execute("UPDATE assets SET canonical_path=?,version=version+1,modified_at=? WHERE id=?", (new, time.time(), asset["id"]))

    def login_attempt(self, key, failed=False):
        now = time.time()
        with self.connection(write=True) as db:
            db.execute("DELETE FROM login_attempts WHERE until < ?", (now,))
            row = db.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
            if row and row["count"] >= 10:
                raise StorageError("Too many login attempts; try later", 429, "rate_limited")
            if failed:
                db.execute("INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now + 900))
