from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import ReleaseRequest
from .service import Publisher
from .store import FileReleaseStore, PublishingStoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hwskill-publish")
    parser.add_argument("--store-root", required=True)
    parser.add_argument("--feed-id", default="hwskill-main")
    parser.add_argument("--public-base-url", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    release = commands.add_parser("release")
    release.add_argument("--artifact-dir", required=True)
    release.add_argument("--source-commit", required=True)
    release.add_argument("--catalog-digest", required=True)
    release.add_argument("--snapshot-digest", required=True)
    release.add_argument("--build-config-identity", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("release_id")
    args = parser.parse_args(argv)
    try:
        store = FileReleaseStore(Path(args.store_root))
        publisher = Publisher(store, feed_id=args.feed_id, public_base_url=args.public_base_url)
        if args.command == "rollback":
            publisher.rollback(args.release_id)
            print(json.dumps(store.read_current(), ensure_ascii=False, sort_keys=True))
            return 0
        request = ReleaseRequest(
            source_commit=args.source_commit,
            catalog_digest=args.catalog_digest,
            snapshot_digest=args.snapshot_digest,
            build_config_identity=args.build_config_identity,
            artifact_dir=Path(args.artifact_dir),
        )
        print(json.dumps(publisher.publish(request).to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, PublishingStoreError) as exc:
        print(json.dumps({"result": "error", "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
