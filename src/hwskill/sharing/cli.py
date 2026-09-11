from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable

from .feed_reader import FeedAuthenticationError, FeedReadError, FeedReader
from .models import UpdateFilters
from .output import write_json_atomic
from .service import (
    InitializationRequiredError,
    PendingBatchStateError,
    SharingConfig,
    SharingStateError,
    acknowledge,
    prepare_updates,
)
from .store import SQLiteUpdateStore, UpdateStoreError, UpdateStoreLockedError
from .validation import FeedValidationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwskill-sharing")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--state-db", required=True)
    prepare.add_argument("--consumer-id", required=True)
    prepare.add_argument("--feed-url", required=True)
    prepare.add_argument("--output", required=True)
    mode = prepare.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true")
    mode.add_argument("--replay-from", type=int)
    prepare.add_argument("--purpose", action="append", default=[])
    prepare.add_argument("--skill-id", action="append", default=[])
    prepare.add_argument("--change-type", action="append", default=[])
    prepare.add_argument("--lifecycle", action="append", default=[])
    prepare.add_argument("--topic", action="append", default=[])
    prepare.add_argument("--private", action="store_true")
    prepare.add_argument("--token-env", default="HWSKILL_FEED_TOKEN")
    ack = commands.add_parser("ack")
    ack.add_argument("--state-db", required=True)
    ack.add_argument("--consumer-id", required=True)
    ack.add_argument("--batch-id", required=True)
    return parser


def _validate_output_path(state_path: Path, output_path: Path) -> None:
    state = state_path.resolve(strict=False)
    output = output_path.resolve(strict=False)
    forbidden = {
        state,
        Path(str(state) + ".lock"),
        Path(str(state) + "-journal"),
        Path(str(state) + "-wal"),
        Path(str(state) + "-shm"),
    }
    if output in forbidden:
        raise ValueError("output 不能覆盖 SQLite 状态库、锁或事务辅助文件")


def main(
    argv: list[str] | None = None,
    *,
    reader_factory: Callable[..., FeedReader] = FeedReader,
) -> int:
    args = _parser().parse_args(argv)
    try:
        state_path = Path(args.state_db)
        if args.command == "prepare":
            _validate_output_path(state_path, Path(args.output))
        store = SQLiteUpdateStore(state_path)
        if args.command == "ack":
            result = acknowledge(store, args.consumer_id, args.batch_id)
            print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
            return 0
        initialization = "baseline" if args.baseline else "replay" if args.replay_from is not None else None
        config = SharingConfig(
            consumer_id=args.consumer_id,
            feed_url=args.feed_url,
            filters=UpdateFilters(
                purposes=tuple(args.purpose),
                skill_ids=tuple(args.skill_id),
                change_types=tuple(args.change_type),
                lifecycles=tuple(args.lifecycle),
                topics=tuple(args.topic),
            ),
            initialization=initialization,
            replay_from_sequence=args.replay_from or 0,
        )
        token = os.environ.get(args.token_env) if args.private else None
        batch = prepare_updates(reader_factory(private=args.private, token=token), store, config)
        batch_value = batch.to_dict()
        with store.lock():
            if store.load_pending(config.consumer_id) != batch_value:
                raise PendingBatchStateError("待导出 batch 已被更新快照替代，请重新 prepare")
            write_json_atomic(Path(args.output), batch_value)
        print(json.dumps({"result": "prepared", **batch.to_dict()}, ensure_ascii=False, sort_keys=True))
        return 0
    except InitializationRequiredError as exc:
        print(json.dumps({"result": "blocked", "code": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 3
    except (UpdateStoreLockedError, FeedAuthenticationError) as exc:
        print(json.dumps({"result": "blocked", "code": "environment-blocked", "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 3
    except (SharingStateError, UpdateStoreError, FeedReadError, FeedValidationError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "operation-failed")
        print(json.dumps({"result": "error", "code": code, "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
