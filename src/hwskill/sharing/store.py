from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from typing import Any, Callable, Iterator, Mapping


class UpdateStoreError(RuntimeError):
    pass


class StateDatabaseMissingError(UpdateStoreError):
    pass


class StateDatabaseCorruptError(UpdateStoreError):
    pass


class StateCommitUncertainError(StateDatabaseCorruptError):
    code = "state-commit-uncertain"


class UpdateStoreLockedError(UpdateStoreError):
    pass


class StoreAcknowledgementError(UpdateStoreError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumers (
    consumer_id TEXT PRIMARY KEY,
    feed_id TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    filter_digest TEXT NOT NULL,
    cursor_sequence INTEGER NOT NULL CHECK (cursor_sequence >= 0),
    initialization TEXT NOT NULL CHECK (initialization IN ('baseline', 'replay')),
    last_acknowledged_batch_id TEXT
);
CREATE TABLE IF NOT EXISTS pending_batches (
    consumer_id TEXT PRIMARY KEY REFERENCES consumers(consumer_id),
    batch_id TEXT NOT NULL UNIQUE,
    snapshot_identity TEXT NOT NULL,
    from_sequence INTEGER NOT NULL,
    through_sequence INTEGER NOT NULL,
    filter_digest TEXT NOT NULL,
    batch_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acknowledgements (
    consumer_id TEXT NOT NULL REFERENCES consumers(consumer_id),
    batch_id TEXT NOT NULL,
    acknowledgement_json TEXT NOT NULL,
    PRIMARY KEY (consumer_id, batch_id)
);
"""
_RENAME_NOREPLACE = 1
_CONSUMER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BATCH_ID = re.compile(r"^batch-[0-9a-f]{64}$")


def _inode_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _translated_sqlite_error(exc: sqlite3.Error, operation: str) -> UpdateStoreError:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return UpdateStoreLockedError(f"sharing 状态库正忙：{exc}")
    return UpdateStoreError(f"sharing 状态库{operation}失败：{exc}")


def _rename_noreplace(parent_descriptor: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise UpdateStoreError("系统不支持状态库原子 no-replace 发布")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(
        parent_descriptor,
        os.fsencode(source),
        parent_descriptor,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    ) == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise UpdateStoreError("状态库 final 名称已被并发创建，拒绝覆盖")
    raise UpdateStoreError(f"无法原子发布 sharing 状态库：{os.strerror(error)}")


@dataclass(frozen=True)
class _OpenDatabase:
    connection: sqlite3.Connection
    verify: Callable[[], None]


@dataclass
class _OpenDirectory:
    descriptors: list[int]
    names: tuple[str, ...]

    @property
    def leaf(self) -> int:
        return self.descriptors[-1]

    def close(self) -> None:
        while self.descriptors:
            os.close(self.descriptors.pop())


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _exact_row(row: sqlite3.Row, expected: set[str], label: str) -> dict[str, Any]:
    if set(row.keys()) != expected:
        raise StateDatabaseCorruptError(f"{label} SQLite 字段集合无效")
    return dict(row)


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _decode_consumer(
    row: sqlite3.Row,
    *,
    expected_consumer_id: str | None = None,
) -> dict[str, Any]:
    value = _exact_row(
        row,
        {
            "consumer_id",
            "feed_id",
            "feed_url",
            "filter_digest",
            "cursor_sequence",
            "initialization",
            "last_acknowledged_batch_id",
        },
        "consumer",
    )
    last_ack = value["last_acknowledged_batch_id"]
    if (
        not _nonempty_string(value["consumer_id"])
        or not _CONSUMER_ID.fullmatch(value["consumer_id"])
        or (expected_consumer_id is not None and value["consumer_id"] != expected_consumer_id)
        or not _nonempty_string(value["feed_id"])
        or not _nonempty_string(value["feed_url"])
        or not _nonempty_string(value["filter_digest"])
        or type(value["cursor_sequence"]) is not int
        or value["cursor_sequence"] < 0
        or value["initialization"] not in {"baseline", "replay"}
        or (last_ack is not None and (type(last_ack) is not str or not _BATCH_ID.fullmatch(last_ack)))
    ):
        raise StateDatabaseCorruptError("consumer SQLite 值、类型或身份无效")
    return value


def _decode_acknowledgement(
    body: str,
    *,
    consumer_id: str,
    batch_id: str,
    feed_id: str,
    cursor_sequence: int,
) -> dict[str, Any]:
    if (
        type(consumer_id) is not str
        or not _CONSUMER_ID.fullmatch(consumer_id)
        or type(batch_id) is not str
        or not _BATCH_ID.fullmatch(batch_id)
        or not _nonempty_string(feed_id)
        or type(cursor_sequence) is not int
        or cursor_sequence < 0
        or type(body) is not str
    ):
        raise StateDatabaseCorruptError("acknowledgement 的 consumer 绑定无效")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise StateDatabaseCorruptError("acknowledgement JSON 已损坏") from exc
    expected_fields = {"schema_version", "consumer_id", "batch_id", "feed_id", "through_sequence"}
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("consumer_id") != consumer_id
        or value.get("batch_id") != batch_id
        or value.get("feed_id") != feed_id
        or isinstance(value.get("through_sequence"), bool)
        or not isinstance(value.get("through_sequence"), int)
        or value["through_sequence"] < 0
        or value["through_sequence"] > cursor_sequence
    ):
        raise StateDatabaseCorruptError("acknowledgement 与 consumer 游标或身份不一致")
    return value


def _decode_pending(
    row: sqlite3.Row,
    *,
    consumer: Mapping[str, Any],
) -> dict[str, Any]:
    persisted = _exact_row(
        row,
        {
            "consumer_id",
            "batch_id",
            "snapshot_identity",
            "from_sequence",
            "through_sequence",
            "filter_digest",
            "batch_json",
        },
        "pending batch",
    )
    if (
        type(persisted["consumer_id"]) is not str
        or not _CONSUMER_ID.fullmatch(persisted["consumer_id"])
        or persisted["consumer_id"] != consumer["consumer_id"]
        or type(persisted["batch_id"]) is not str
        or not _BATCH_ID.fullmatch(persisted["batch_id"])
        or not _nonempty_string(persisted["snapshot_identity"])
        or type(persisted["from_sequence"]) is not int
        or persisted["from_sequence"] < 0
        or type(persisted["through_sequence"]) is not int
        or persisted["through_sequence"] < persisted["from_sequence"]
        or not _nonempty_string(persisted["filter_digest"])
        or persisted["filter_digest"] != consumer["filter_digest"]
        or persisted["from_sequence"] != consumer["cursor_sequence"]
        or type(persisted["batch_json"]) is not str
    ):
        raise StateDatabaseCorruptError("pending batch SQLite 值、类型或 consumer 绑定无效")
    try:
        value = json.loads(persisted["batch_json"])
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise StateDatabaseCorruptError("pending batch JSON 已损坏") from exc
    expected_fields = {
        "schema_version",
        "consumer_id",
        "batch_id",
        "feed_id",
        "from_sequence",
        "through_sequence",
        "filter_digest",
        "snapshot_identity",
        "items",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or type(value.get("items")) is not list
    ):
        raise StateDatabaseCorruptError("pending batch JSON Schema 或字段无效")
    indexed = {
        "consumer_id": persisted["consumer_id"],
        "batch_id": persisted["batch_id"],
        "feed_id": consumer["feed_id"],
        "snapshot_identity": persisted["snapshot_identity"],
        "from_sequence": persisted["from_sequence"],
        "through_sequence": persisted["through_sequence"],
        "filter_digest": persisted["filter_digest"],
    }
    if any(
        type(value.get(field)) is not type(expected) or value.get(field) != expected
        for field, expected in indexed.items()
    ):
        raise StateDatabaseCorruptError("pending batch JSON 与 SQLite 索引字段不一致")
    return value


class SQLiteUpdateStore:
    def __init__(
        self,
        path: Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path)
        if not self.path.name:
            raise ValueError("状态库路径必须指向文件")
        self.fault_injector = fault_injector or (lambda _stage: None)
        self._held_lock: tuple[_OpenDirectory, tuple[int, int]] | None = None

    def _open_parent(self, *, create: bool) -> _OpenDirectory:
        absolute = Path(os.path.abspath(os.fspath(self.path.parent)))
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptors: list[int] = []
        names = tuple(absolute.parts[1:])
        try:
            descriptors.append(os.open("/", flags))
            for name in names:
                if create:
                    try:
                        os.mkdir(name, 0o755, dir_fd=descriptors[-1])
                        os.fsync(descriptors[-1])
                    except FileExistsError:
                        pass
                descriptors.append(os.open(name, flags, dir_fd=descriptors[-1]))
        except FileNotFoundError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise
        except OSError as exc:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise StateDatabaseCorruptError(f"无法安全打开状态库父目录：{exc}") from exc
        binding = _OpenDirectory(descriptors=descriptors, names=names)
        self._verify_parent(binding)
        return binding

    def _verify_parent(self, binding: _OpenDirectory) -> None:
        if len(binding.descriptors) != len(binding.names) + 1:
            raise StateDatabaseCorruptError("状态库父目录 fd 链损坏")
        if not stat.S_ISDIR(os.fstat(binding.descriptors[0]).st_mode):
            raise StateDatabaseCorruptError("状态库根目录 fd 无效")
        for index, name in enumerate(binding.names):
            opened = os.fstat(binding.descriptors[index + 1])
            try:
                named = os.stat(name, dir_fd=binding.descriptors[index], follow_symlinks=False)
            except OSError as exc:
                raise StateDatabaseCorruptError("状态库父目录在操作期间消失或被替换") from exc
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or _inode_identity(opened) != _inode_identity(named)
            ):
                raise StateDatabaseCorruptError("状态库父目录在操作期间被替换")

    def _verify_lock(self) -> None:
        if self._held_lock is None:
            return
        binding, identity = self._held_lock
        if _inode_identity(os.fstat(binding.leaf)) != identity:
            raise StateDatabaseCorruptError("sharing 协调锁 inode 已变化")
        self._verify_parent(binding)

    def _open_database(self, *, create: bool) -> tuple[_OpenDirectory, int, bool, str]:
        self._verify_lock()
        try:
            parent = self._open_parent(create=create)
        except FileNotFoundError as exc:
            raise StateDatabaseMissingError("sharing 状态库不存在，必须显式恢复或初始化") from exc
        flags = os.O_RDWR | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        created = False
        database_name = self.path.name
        try:
            if create:
                try:
                    database_descriptor = os.open(self.path.name, flags, dir_fd=parent.leaf)
                except FileNotFoundError:
                    for _attempt in range(128):
                        database_name = f".{self.path.name}.{secrets.token_hex(16)}.init"
                        try:
                            database_descriptor = os.open(
                                database_name,
                                flags | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=parent.leaf,
                            )
                            created = True
                            break
                        except FileExistsError:
                            continue
                    else:
                        raise UpdateStoreError("无法分配状态库初始化临时名称")
            else:
                database_descriptor = os.open(self.path.name, flags, dir_fd=parent.leaf)
        except FileNotFoundError as exc:
            parent.close()
            raise StateDatabaseMissingError("sharing 状态库不存在，必须显式恢复或初始化") from exc
        except OSError as exc:
            parent.close()
            raise StateDatabaseCorruptError(f"无法安全打开 sharing 状态库：{exc}") from exc
        metadata = os.fstat(database_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(database_descriptor)
            parent.close()
            raise StateDatabaseCorruptError("状态库路径必须是普通文件且不能是符号链接")
        return parent, database_descriptor, created, database_name

    def _verify_database(
        self,
        parent: _OpenDirectory,
        database_descriptor: int,
        database_name: str,
        expected_identity: tuple[int, int],
        connection: sqlite3.Connection,
        *,
        verify_connection_path: bool,
    ) -> None:
        self._verify_lock()
        self._verify_parent(parent)
        opened = os.fstat(database_descriptor)
        if not stat.S_ISREG(opened.st_mode) or _inode_identity(opened) != expected_identity:
            raise StateDatabaseCorruptError("已打开的状态库 inode 已变化")
        try:
            named = os.stat(database_name, dir_fd=parent.leaf, follow_symlinks=False)
        except (FileNotFoundError, OSError) as exc:
            raise StateDatabaseCorruptError("状态库名称在操作期间消失或被替换") from exc
        if not stat.S_ISREG(named.st_mode) or _inode_identity(named) != expected_identity:
            raise StateDatabaseCorruptError("状态库名称在操作期间被替换")
        if verify_connection_path:
            row = connection.execute("PRAGMA database_list").fetchone()
            if row is None or not row[2]:
                raise StateDatabaseCorruptError("SQLite 未返回主状态库路径")
            try:
                connected = os.stat(row[2], follow_symlinks=False)
            except OSError as exc:
                raise StateDatabaseCorruptError("无法核对 SQLite 主状态库 inode") from exc
            if not stat.S_ISREG(connected.st_mode) or _inode_identity(connected) != expected_identity:
                raise StateDatabaseCorruptError("SQLite 连接未绑定已安全打开的状态库 inode")

    @property
    def database_exists(self) -> bool:
        try:
            parent = self._open_parent(create=False)
        except FileNotFoundError:
            return False
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            try:
                descriptor = os.open(self.path.name, flags, dir_fd=parent.leaf)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise StateDatabaseCorruptError(f"无法安全检查 sharing 状态库：{exc}") from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise StateDatabaseCorruptError("状态库路径必须是普通文件且不能是符号链接")
                self._verify_parent(parent)
                named = os.stat(self.path.name, dir_fd=parent.leaf, follow_symlinks=False)
                if _inode_identity(named) != _inode_identity(metadata):
                    raise StateDatabaseCorruptError("状态库在检查期间被替换")
                return True
            finally:
                os.close(descriptor)
        finally:
            parent.close()

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            parent = self._open_parent(create=True)
        except FileNotFoundError as exc:
            raise UpdateStoreError("无法创建 sharing 状态目录") from exc
        try:
            try:
                fcntl.flock(parent.leaf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                if not isinstance(exc, BlockingIOError) and exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise UpdateStoreError(f"无法取得 sharing 协调锁：{exc}") from exc
                raise UpdateStoreLockedError("另一个 sharing 实例正在使用此状态库") from exc
            self._verify_parent(parent)
            if self._held_lock is not None:
                raise UpdateStoreError("同一 store 不能嵌套取得协调锁")
            self._held_lock = (parent, _inode_identity(os.fstat(parent.leaf)))
            yield
        finally:
            self._held_lock = None
            try:
                fcntl.flock(parent.leaf, fcntl.LOCK_UN)
            finally:
                parent.close()

    @contextmanager
    def _connect(self, *, create: bool) -> Iterator[_OpenDatabase]:
        parent, database_descriptor, created, database_name = self._open_database(create=create)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"/proc/self/fd/{database_descriptor}", timeout=0, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            expected_identity = _inode_identity(os.fstat(database_descriptor))
            connection_path_verified = False

            def verify() -> None:
                nonlocal connection_path_verified
                assert connection is not None
                self._verify_database(
                    parent,
                    database_descriptor,
                    database_name,
                    expected_identity,
                    connection,
                    verify_connection_path=not connection_path_verified,
                )
                connection_path_verified = True

            verify()
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            if created:
                connection.executescript(_SCHEMA)
                connection.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '1')")
                self.fault_injector("after_database_schema")
            row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
            if row is None or row["value"] != "1":
                raise StateDatabaseCorruptError("sharing 状态库 Schema 缺失或版本不受支持")
            verify()
            if created:
                os.fsync(database_descriptor)
                self.fault_injector("before_database_publish")
                verify()
                _rename_noreplace(parent.leaf, database_name, self.path.name)
                database_name = self.path.name
                os.fsync(parent.leaf)
                verify()
                connection.close()
                connection = None
                os.close(database_descriptor)
                database_descriptor = -1
                parent.close()
                # SQLite 的 journal 路径绑定打开时的文件名；发布后必须从 final 名安全重开。
                with self._connect(create=False) as published:
                    yield published
                return
            yield _OpenDatabase(connection=connection, verify=verify)
        except (sqlite3.Error, UpdateStoreError) as exc:
            if isinstance(exc, UpdateStoreError):
                raise
            raise _translated_sqlite_error(exc, "访问") from exc
        finally:
            if connection is not None:
                connection.close()
            if database_descriptor >= 0:
                os.close(database_descriptor)
            parent.close()

    @contextmanager
    def _transaction(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        with self._connect(create=create) as opened:
            connection = opened.connection
            try:
                opened.verify()
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                opened.verify()
            except BaseException:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    # 回滚失败只影响诊断，不能覆盖触发回滚的原始异常。
                    pass
                raise
            try:
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise
            try:
                opened.verify()
            except UpdateStoreError as exc:
                # commit 已返回成功，不能声称回滚；只报告命名空间/耐久结果不确定。
                raise StateCommitUncertainError(
                    "SQLite 事务已提交，但状态库路径绑定随后变化，提交结果不确定"
                ) from exc

    def load_consumer(self, consumer_id: str) -> dict[str, Any] | None:
        if not self.database_exists:
            return None
        with self._connect(create=False) as opened:
            connection = opened.connection
            row = connection.execute("SELECT * FROM consumers WHERE consumer_id = ?", (consumer_id,)).fetchone()
            return None if row is None else _decode_consumer(row, expected_consumer_id=consumer_id)

    def initialize_consumer(
        self,
        *,
        consumer_id: str,
        feed_id: str,
        feed_url: str,
        filter_digest: str,
        cursor_sequence: int,
        initialization: str,
    ) -> None:
        with self._transaction(create=True) as connection:
            try:
                connection.execute(
                    """INSERT INTO consumers(
                           consumer_id, feed_id, feed_url, filter_digest, cursor_sequence, initialization
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (consumer_id, feed_id, feed_url, filter_digest, cursor_sequence, initialization),
                )
            except sqlite3.IntegrityError as exc:
                raise UpdateStoreError(f"consumer {consumer_id!r} 已初始化") from exc

    def load_pending(self, consumer_id: str) -> dict[str, Any] | None:
        if not self.database_exists:
            return None
        with self._connect(create=False) as opened:
            connection = opened.connection
            consumer_row = connection.execute(
                "SELECT * FROM consumers WHERE consumer_id = ?", (consumer_id,)
            ).fetchone()
            consumer = (
                None
                if consumer_row is None
                else _decode_consumer(consumer_row, expected_consumer_id=consumer_id)
            )
            row = connection.execute(
                "SELECT * FROM pending_batches WHERE consumer_id = ?",
                (consumer_id,),
            ).fetchone()
            if row is None:
                return None
            if consumer is None:
                raise StateDatabaseCorruptError("pending batch 缺少对应 consumer")
            return _decode_pending(row, consumer=consumer)

    def save_pending(self, batch: Mapping[str, Any]) -> None:
        with self._transaction() as connection:
            consumer = connection.execute(
                "SELECT * FROM consumers WHERE consumer_id = ?",
                (batch["consumer_id"],),
            ).fetchone()
            if consumer is None:
                raise UpdateStoreError("pending batch 没有对应 consumer")
            consumer = _decode_consumer(consumer, expected_consumer_id=batch["consumer_id"])
            if (
                consumer["feed_id"] != batch["feed_id"]
                or consumer["filter_digest"] != batch["filter_digest"]
                or consumer["cursor_sequence"] != batch["from_sequence"]
                or batch["through_sequence"] < batch["from_sequence"]
            ):
                raise UpdateStoreError("pending batch 与 consumer 游标或配置不一致")
            connection.execute(
                """INSERT INTO pending_batches(
                       consumer_id, batch_id, snapshot_identity, from_sequence,
                       through_sequence, filter_digest, batch_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(consumer_id) DO UPDATE SET
                       batch_id=excluded.batch_id,
                       snapshot_identity=excluded.snapshot_identity,
                       from_sequence=excluded.from_sequence,
                       through_sequence=excluded.through_sequence,
                       filter_digest=excluded.filter_digest,
                       batch_json=excluded.batch_json""",
                (
                    batch["consumer_id"],
                    batch["batch_id"],
                    batch["snapshot_identity"],
                    batch["from_sequence"],
                    batch["through_sequence"],
                    batch["filter_digest"],
                    _canonical_json(batch),
                ),
            )
            self.fault_injector("before_pending_commit")

    def load_acknowledgement(self, consumer_id: str, batch_id: str) -> dict[str, Any] | None:
        if not self.database_exists:
            return None
        with self._connect(create=False) as opened:
            connection = opened.connection
            consumer_row = connection.execute(
                "SELECT * FROM consumers WHERE consumer_id = ?", (consumer_id,)
            ).fetchone()
            consumer = (
                None
                if consumer_row is None
                else _decode_consumer(consumer_row, expected_consumer_id=consumer_id)
            )
            row = connection.execute(
                "SELECT * FROM acknowledgements WHERE consumer_id = ? AND batch_id = ?",
                (consumer_id, batch_id),
            ).fetchone()
            if row is None:
                return None
            if consumer is None:
                raise StateDatabaseCorruptError("acknowledgement 缺少对应 consumer")
            persisted = _exact_row(
                row,
                {"consumer_id", "batch_id", "acknowledgement_json"},
                "acknowledgement",
            )
            if persisted["consumer_id"] != consumer_id or persisted["batch_id"] != batch_id:
                raise StateDatabaseCorruptError("acknowledgement SQLite 身份无效")
            return _decode_acknowledgement(
                persisted["acknowledgement_json"],
                consumer_id=consumer_id,
                batch_id=batch_id,
                feed_id=consumer["feed_id"],
                cursor_sequence=consumer["cursor_sequence"],
            )

    def commit_acknowledgement(self, consumer_id: str, batch_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            consumer_row = connection.execute(
                "SELECT * FROM consumers WHERE consumer_id = ?", (consumer_id,)
            ).fetchone()
            if consumer_row is None:
                raise StoreAcknowledgementError("只能确认当前 pending batch 的精确 ID")
            consumer = _decode_consumer(consumer_row, expected_consumer_id=consumer_id)
            acknowledged = connection.execute(
                "SELECT * FROM acknowledgements WHERE consumer_id = ? AND batch_id = ?",
                (consumer_id, batch_id),
            ).fetchone()
            if acknowledged is not None:
                acknowledged = _exact_row(
                    acknowledged,
                    {"consumer_id", "batch_id", "acknowledgement_json"},
                    "acknowledgement",
                )
                if acknowledged["consumer_id"] != consumer_id or acknowledged["batch_id"] != batch_id:
                    raise StateDatabaseCorruptError("acknowledgement SQLite 身份无效")
                return _decode_acknowledgement(
                    acknowledged["acknowledgement_json"],
                    consumer_id=consumer_id,
                    batch_id=batch_id,
                    feed_id=consumer["feed_id"],
                    cursor_sequence=consumer["cursor_sequence"],
                )
            pending = connection.execute(
                "SELECT * FROM pending_batches WHERE consumer_id = ?",
                (consumer_id,),
            ).fetchone()
            if pending is None:
                raise StoreAcknowledgementError("只能确认当前 pending batch 的精确 ID")
            pending_value = _decode_pending(pending, consumer=consumer)
            if pending_value["batch_id"] != batch_id:
                raise StoreAcknowledgementError("只能确认当前 pending batch 的精确 ID")
            updated = connection.execute(
                """UPDATE consumers
                   SET cursor_sequence = ?, last_acknowledged_batch_id = ?
                   WHERE consumer_id = ? AND cursor_sequence = ?""",
                (pending["through_sequence"], batch_id, consumer_id, pending["from_sequence"]),
            )
            if updated.rowcount != 1:
                raise StateDatabaseCorruptError("consumer 游标与 pending batch 不一致")
            self.fault_injector("after_cursor_update")
            acknowledgement = {
                "schema_version": 1,
                "consumer_id": consumer_id,
                "batch_id": batch_id,
                "feed_id": consumer["feed_id"],
                "through_sequence": pending["through_sequence"],
            }
            # ack 只表示调用方已接收此交接批次，不表示任何消息或通知发送成功。
            connection.execute(
                "INSERT INTO acknowledgements VALUES (?, ?, ?)",
                (consumer_id, batch_id, _canonical_json(acknowledgement)),
            )
            connection.execute("DELETE FROM pending_batches WHERE consumer_id = ?", (consumer_id,))
            self.fault_injector("before_ack_commit")
            return acknowledgement
