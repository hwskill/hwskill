from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Protocol

from .filtering import build_items
from .models import FeedSnapshot, SourceVersion, UpdateFilters, UpdateItem
from .store import SQLiteUpdateStore, StoreAcknowledgementError, UpdateStoreError
from .validation import stable_digest, validate_snapshot


class SharingStateError(RuntimeError):
    code = "state-error"


class InitializationRequiredError(SharingStateError):
    code = "initialization-required"


class ConsumerConfigurationError(SharingStateError):
    code = "consumer-configuration"


class AcknowledgementError(SharingStateError):
    code = "acknowledgement-error"


class SnapshotCursorError(SharingStateError):
    code = "snapshot-cursor"


class PendingBatchStateError(SharingStateError):
    code = "pending-batch-corrupt"


class SnapshotReader(Protocol):
    def read_snapshot(self, feed_url: str) -> FeedSnapshot: ...


_CONSUMER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BATCH_ID = re.compile(r"^batch-[0-9a-f]{64}$")


def _filters_dict(filters: UpdateFilters) -> dict[str, list[str]]:
    return {
        "purposes": list(filters.purposes),
        "skill_ids": list(filters.skill_ids),
        "change_types": list(filters.change_types),
        "lifecycles": list(filters.lifecycles),
        "topics": list(filters.topics),
    }


def _item_from_dict(value: Mapping[str, Any]) -> UpdateItem:
    return UpdateItem(
        schema_version=value["schema_version"],
        item_id=value["item_id"],
        sequence=value["sequence"],
        event_ids=tuple(value["event_ids"]),
        change_type=value["change_type"],
        title=value["title"],
        summary=value["summary"],
        skill_refs=tuple(value["skill_refs"]),
        recommendation_refs=tuple(value["recommendation_refs"]),
        source_versions=tuple(SourceVersion.from_dict(item) for item in value["source_versions"]),
        detail_url=value["detail_url"],
        install_urls=tuple(value["install_urls"]),
        lifecycle=value["lifecycle"],
        install_capability=value["install_capability"],
        purposes=tuple(value.get("purposes", ())),
        topics=tuple(value.get("topics", ())),
    )


@dataclass(frozen=True)
class SharingConfig:
    consumer_id: str
    feed_url: str
    filters: UpdateFilters = field(default_factory=UpdateFilters)
    initialization: str | None = None
    replay_from_sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.consumer_id, str) or not _CONSUMER_ID.fullmatch(self.consumer_id):
            raise ValueError("consumer_id 必须是安全的非空标识")
        normalized_url = self.feed_url.rstrip("/")
        if not normalized_url:
            raise ValueError("feed_url 不能为空")
        object.__setattr__(self, "feed_url", normalized_url)
        if self.initialization not in {None, "baseline", "replay"}:
            raise ValueError("initialization 必须是 baseline、replay 或空")
        if type(self.replay_from_sequence) is not int or self.replay_from_sequence < 0:
            raise ValueError("replay_from_sequence 必须是非负整数")


@dataclass(frozen=True)
class PreparedBatch:
    schema_version: int
    consumer_id: str
    batch_id: str
    feed_id: str
    from_sequence: int
    through_sequence: int
    filter_digest: str
    snapshot_identity: str
    items: tuple[UpdateItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consumer_id": self.consumer_id,
            "batch_id": self.batch_id,
            "feed_id": self.feed_id,
            "from_sequence": self.from_sequence,
            "through_sequence": self.through_sequence,
            "filter_digest": self.filter_digest,
            "snapshot_identity": self.snapshot_identity,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedBatch":
        expected = {
            "schema_version", "consumer_id", "batch_id", "feed_id", "from_sequence",
            "through_sequence", "filter_digest", "snapshot_identity", "items",
        }
        if set(value) != expected or value.get("schema_version") != 1:
            raise ValueError("pending batch Schema 或字段无效")
        if not isinstance(value.get("batch_id"), str) or not _BATCH_ID.fullmatch(value["batch_id"]):
            raise ValueError("pending batch ID 无效")
        if not isinstance(value.get("items"), list):
            raise ValueError("pending batch items 必须是数组")
        return cls(
            schema_version=1,
            consumer_id=value["consumer_id"],
            batch_id=value["batch_id"],
            feed_id=value["feed_id"],
            from_sequence=value["from_sequence"],
            through_sequence=value["through_sequence"],
            filter_digest=value["filter_digest"],
            snapshot_identity=value["snapshot_identity"],
            items=tuple(_item_from_dict(item) for item in value["items"]),
        )


@dataclass(frozen=True)
class Acknowledgement:
    schema_version: int
    consumer_id: str
    batch_id: str
    feed_id: str
    through_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consumer_id": self.consumer_id,
            "batch_id": self.batch_id,
            "feed_id": self.feed_id,
            "through_sequence": self.through_sequence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Acknowledgement":
        return cls(**dict(value))


def _new_batch(
    snapshot: FeedSnapshot,
    config: SharingConfig,
    *,
    from_sequence: int,
    filter_digest: str,
    previous_acknowledgement: str | None,
) -> PreparedBatch:
    items = tuple(build_items(snapshot, from_sequence, config.filters))
    identity = {
        "consumer_id": config.consumer_id,
        "feed_id": snapshot.feed_id,
        "from_sequence": from_sequence,
        "through_sequence": snapshot.head_sequence,
        "filter_digest": filter_digest,
        "snapshot_identity": snapshot.snapshot_identity,
        "previous_acknowledgement": previous_acknowledgement,
        "items": [item.to_dict() for item in items],
    }
    batch_id = "batch-" + stable_digest(identity).removeprefix("sha256:")
    return PreparedBatch(
        schema_version=1,
        consumer_id=config.consumer_id,
        batch_id=batch_id,
        feed_id=snapshot.feed_id,
        from_sequence=from_sequence,
        through_sequence=snapshot.head_sequence,
        filter_digest=filter_digest,
        snapshot_identity=snapshot.snapshot_identity,
        items=items,
    )


def prepare_updates(reader: SnapshotReader, store: SQLiteUpdateStore, config: SharingConfig) -> PreparedBatch:
    filter_digest = stable_digest(_filters_dict(config.filters))
    with store.lock():
        consumer = store.load_consumer(config.consumer_id)
        if consumer is None and config.initialization is None:
            raise InitializationRequiredError("首次运行或状态库丢失时必须显式选择 baseline 或 replay")
        pending_value = store.load_pending(config.consumer_id) if consumer is not None else None
        if consumer is not None and config.initialization is not None:
            same_unacknowledged_initialization = (
                consumer["last_acknowledged_batch_id"] is None
                and consumer["initialization"] == config.initialization
                and (
                    config.initialization == "baseline"
                    or consumer["cursor_sequence"] == config.replay_from_sequence
                )
            )
            if not same_unacknowledged_initialization:
                raise ConsumerConfigurationError("已初始化 consumer 不能再次套用不同的首次启动模式")
        if consumer is not None and (
            consumer["feed_url"] != config.feed_url or consumer["filter_digest"] != filter_digest
        ):
            raise ConsumerConfigurationError("consumer 已绑定不同 feed URL 或筛选配置；请使用新 consumer")

        snapshot = reader.read_snapshot(config.feed_url)
        validate_snapshot(snapshot)
        if snapshot.feed_url.rstrip("/") != config.feed_url:
            raise ConsumerConfigurationError("reader 返回的 feed URL 与配置不一致")

        if consumer is None:
            cursor = snapshot.head_sequence if config.initialization == "baseline" else config.replay_from_sequence
            if cursor > snapshot.head_sequence:
                raise SnapshotCursorError("replay 起点超过当前 feed head")
            store.initialize_consumer(
                consumer_id=config.consumer_id,
                feed_id=snapshot.feed_id,
                feed_url=config.feed_url,
                filter_digest=filter_digest,
                cursor_sequence=cursor,
                initialization=config.initialization or "replay",
            )
            consumer = store.load_consumer(config.consumer_id)
            assert consumer is not None
            pending_value = None
        elif consumer["feed_id"] != snapshot.feed_id:
            raise ConsumerConfigurationError("consumer 已绑定不同 feed ID")

        cursor = consumer["cursor_sequence"]
        if cursor > snapshot.head_sequence:
            raise SnapshotCursorError("已确认游标超过当前 feed head，拒绝回退")
        batch = _new_batch(
            snapshot,
            config,
            from_sequence=cursor,
            filter_digest=filter_digest,
            previous_acknowledgement=consumer["last_acknowledged_batch_id"],
        )
        if pending_value is not None:
            try:
                pending = PreparedBatch.from_dict(pending_value)
            except (KeyError, TypeError, ValueError) as exc:
                raise PendingBatchStateError("持久化 pending batch 无法按固定 Schema 恢复") from exc
            if pending.snapshot_identity == snapshot.snapshot_identity:
                if stable_digest(pending_value) != stable_digest(batch.to_dict()) or pending != batch:
                    raise PendingBatchStateError("持久化 pending batch 与当前固定输入重派生结果不一致")
                return pending
        store.save_pending(batch.to_dict())
        return batch


def acknowledge(store: SQLiteUpdateStore, consumer_id: str, batch_id: str) -> Acknowledgement:
    if not isinstance(consumer_id, str) or not _CONSUMER_ID.fullmatch(consumer_id):
        raise ValueError("consumer_id 无效")
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        raise AcknowledgementError("batch_id 无效")
    with store.lock():
        try:
            return Acknowledgement.from_dict(store.commit_acknowledgement(consumer_id, batch_id))
        except StoreAcknowledgementError as exc:
            raise AcknowledgementError(str(exc)) from exc
        except UpdateStoreError:
            raise
