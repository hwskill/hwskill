from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

from hwskill.directory.digest import canonical_json, sha256_bytes
from hwskill.directory.entries import (
    _is_safe_external_path,
    _is_safe_requested_ref,
    _normalise_identity_path,
    _normalise_repository,
    _url_is_http,
)
from hwskill.directory.schema import validator_for

from .events import derive_events
from .models import Candidate, CandidateState, ReleaseRequest
from .store import (
    FileReleaseStore,
    ImmutableReleaseError,
    ReleaseIntegrityError,
    directory_digest,
    event_references,
)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("publishing clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_public_base_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("public_base_url is required")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("public_base_url has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("public_base_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("public_base_url cannot contain credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    if parsed.scheme != "https" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("public_base_url requires https except for localhost tests")
    decoded_path = unquote(parsed.path)
    components = decoded_path.split("/")
    if any(component in {".", ".."} for component in components) or "//" in decoded_path:
        raise ValueError("public_base_url path must be canonical and contain no dot or empty segments")
    if decoded_path != parsed.path:
        raise ValueError("public_base_url path must not contain percent-encoded segments")
    host_text = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_text + (f":{port}" if port is not None else "")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _read_json(documents: Mapping[str, bytes], relative: str, label: str) -> dict[str, Any]:
    try:
        body = documents[relative]
        value = json.loads(body.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseIntegrityError(f"{label} must be a JSON object")
    return value


def _schema_check(schema_name: str, value: Mapping[str, Any], label: str) -> None:
    errors = sorted(validator_for(schema_name).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ReleaseIntegrityError(f"{label} fails {schema_name} schema at {location}: {first.message}")


def _require_public_url(value: object, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not _url_is_http(value):
        raise ReleaseIntegrityError(f"{label} must be a credential-free public http(s) URL")


def _validate_install(install: Mapping[str, Any], item: Mapping[str, Any], path: str) -> None:
    required = {
        "schema_version",
        "skill_id",
        "entry_digest",
        "source",
        "install",
        "verification_summary",
        "install_digest",
    }
    if set(install) != required or install.get("schema_version") != 1:
        raise ReleaseIntegrityError(f"install machine JSON has unknown or missing fields: {path}")
    if install.get("skill_id") != item["entry"]["id"] or install.get("entry_digest") != item["entry_digest"]:
        raise ReleaseIntegrityError(f"install machine JSON is not bound to its catalog entry: {path}")
    if not isinstance(install.get("source"), Mapping) or not isinstance(install.get("install"), Mapping):
        raise ReleaseIntegrityError(f"install machine JSON has invalid source/install objects: {path}")
    if set(install["install"]) != {"method", "default_scope", "instructions_url"}:
        raise ReleaseIntegrityError(f"install machine JSON has unknown install fields: {path}")
    _require_public_url(install["install"].get("instructions_url"), f"install instructions URL in {path}", optional=True)
    entry = item["entry"]
    identity = item["source_identity"]
    source = install["source"]
    if source.get("kind") != identity.get("kind") or entry["source"] != {
        "kind": identity.get("kind"),
        "identity": identity.get("identity"),
    }:
        raise ReleaseIntegrityError(f"install source kind or catalog identity differs: {path}")
    source_fields = set(source)
    hosted_fields = {"kind", "path", "resolved_revision"}
    git_fields = {"kind", "repository", "path", "requested_ref", "resolved_revision"}
    web_fields = {"kind", "url", "requested_ref", "resolved_revision"}
    if source.get("kind") == "external" and source_fields == git_fields:
        repository, source_path = source.get("repository"), source.get("path")
        _require_public_url(repository, f"install external git source URL in {path}")
        if not _is_safe_external_path(source_path):
            raise ReleaseIntegrityError(f"install external git source path is unsafe: {path}")
        if not _is_safe_requested_ref(source.get("requested_ref")):
            raise ReleaseIntegrityError(f"install external git requested_ref is unsafe: {path}")
        try:
            locator_identity = (
                f"git:{_normalise_repository(repository)}\0{_normalise_identity_path(source_path)}"
            )
        except ValueError as exc:
            raise ReleaseIntegrityError(f"install external git source locator is invalid: {path}") from exc
        if (
            locator_identity != identity.get("identity")
            or source.get("requested_ref") != identity.get("requested_ref")
            or source.get("resolved_revision") != identity.get("resolved_revision")
        ):
            raise ReleaseIntegrityError(f"install external source differs from catalog identity: {path}")
    elif source.get("kind") == "external" and source_fields == web_fields:
        url = source.get("url")
        _require_public_url(url, f"install external web source URL in {path}")
        try:
            locator_identity = f"web:{_normalise_repository(url)}"
        except ValueError as exc:
            raise ReleaseIntegrityError(f"install external web source locator is invalid: {path}") from exc
        if (
            locator_identity != identity.get("identity")
            or source.get("requested_ref") != identity.get("requested_ref")
            or source.get("resolved_revision") != identity.get("resolved_revision")
        ):
            raise ReleaseIntegrityError(f"install external source differs from catalog identity: {path}")
    elif source.get("kind") == "hosted" and source_fields == hosted_fields:
        source_path = source.get("path")
        if not isinstance(source_path, str):
            raise ReleaseIntegrityError(f"install hosted source locator is incomplete: {path}")
        locator_identity = f"hosted:{_normalise_identity_path(source_path)}"
        if (
            locator_identity != identity.get("identity")
            or source.get("resolved_revision") != identity.get("resolved_revision")
        ):
            raise ReleaseIntegrityError(f"install hosted source differs from catalog identity: {path}")
    else:
        raise ReleaseIntegrityError(f"install source is not an exact hosted/external locator variant: {path}")
    if install["install"] != entry["install"]:
        raise ReleaseIntegrityError(f"install method differs from catalog entry: {path}")
    if install["verification_summary"] != item["verification_summary"]:
        raise ReleaseIntegrityError(f"install verification summary differs from catalog entry: {path}")
    unsigned = {key: value for key, value in install.items() if key != "install_digest"}
    if sha256_bytes(canonical_json(unsigned)) != install["install_digest"]:
        raise ReleaseIntegrityError(f"install machine JSON digest differs: {path}")


def _validate_artifact(
    documents: Mapping[str, bytes],
    *,
    source_commit: str,
    snapshot_digest: str,
) -> None:
    catalog = _read_json(documents, "data/catalog.json", "catalog")
    _schema_check("catalog", catalog, "catalog")
    if catalog.get("source_commit") != source_commit:
        raise ReleaseIntegrityError("catalog source commit does not match the release request")
    entry_ids: set[str] = set()
    for item in catalog["entries"]:
        skill_id = item["entry"]["id"]
        if skill_id in entry_ids:
            raise ReleaseIntegrityError(f"catalog contains duplicate skill {skill_id}")
        entry_ids.add(skill_id)
        if sha256_bytes(canonical_json(item["entry"])) != item["entry_digest"]:
            raise ReleaseIntegrityError(f"catalog entry digest differs for {skill_id}")
        entry = item["entry"]
        _require_public_url(entry["install"].get("instructions_url"), f"catalog instructions URL for {skill_id}", optional=True)
        _require_public_url(entry["license"].get("url"), f"catalog license URL for {skill_id}", optional=True)
        if item["lifecycle"] != item["entry"]["lifecycle"]:
            raise ReleaseIntegrityError(f"catalog lifecycle differs for {skill_id}")
        if item["lifecycle"] == "withdrawn" and item["install_capability"] != "disabled":
            raise ReleaseIntegrityError(f"withdrawn skill {skill_id} must disable installation")
        install_path = f"data/skills/{skill_id}/install.json"
        _validate_install(_read_json(documents, install_path, f"install {skill_id}"), item, install_path)

    recommendations = _read_json(documents, "data/recommendations.json", "recommendations")
    if set(recommendations) != {"schema_version", "recommendations"} or recommendations.get("schema_version") != 1:
        raise ReleaseIntegrityError("recommendations machine JSON has unknown or missing fields")
    seen_recommendations: set[str] = set()
    for recommendation in recommendations["recommendations"]:
        _schema_check("recommendation", recommendation, "recommendation")
        recommendation_id = recommendation["id"]
        for evidence in recommendation.get("evidence", []):
            _require_public_url(evidence.get("url"), f"recommendation evidence URL for {recommendation_id}")
        if recommendation_id in seen_recommendations:
            raise ReleaseIntegrityError(f"recommendations contains duplicate ID {recommendation_id}")
        seen_recommendations.add(recommendation_id)
        if recommendation["status"] == "draft":
            raise ReleaseIntegrityError(f"draft recommendation {recommendation_id} cannot enter a checked release")
        referenced = [skill["id"] for skill in recommendation["skills"]]
        if any(skill_id not in entry_ids for skill_id in referenced):
            raise ReleaseIntegrityError(f"recommendation {recommendation_id} references an absent skill")
        if recommendation["status"] == "ready":
            entries_by_id = {item["entry"]["id"]: item for item in catalog["entries"]}
            if any(entries_by_id[skill_id]["lifecycle"] != "active" for skill_id in referenced):
                raise ReleaseIntegrityError(f"ready recommendation {recommendation_id} references a non-active skill")

    if "data/status.json" in documents:
        status = _read_json(documents, "data/status.json", "status")
        input_identity = status.get("input_digest")
        if input_identity is not None and input_identity not in {
            snapshot_digest,
            snapshot_digest.removeprefix("sha256:"),
        }:
            raise ReleaseIntegrityError("artifact input identity does not match the release request")


def _expected_record(candidate: Candidate) -> dict[str, object]:
    if candidate.committed_at is None:
        raise ReleaseIntegrityError("activated candidate has no frozen committed_at")
    return {
        "schema_version": 1,
        "release_id": candidate.release_id,
        "sequence": candidate.sequence,
        "source_commit": candidate.source_commit,
        "publication_time": candidate.publication_time,
        "committed_at": candidate.committed_at,
        "previous_sequence": candidate.previous_sequence,
        "events": list(candidate.events),
    }


def _validate_record(record: dict[str, object]) -> None:
    _schema_check("release", record, "release record")
    for event in record["events"]:
        _require_public_url(event.get("page_url"), f"event page URL for {event.get('event_id')}")
        _require_public_url(event.get("machine_url"), f"event machine URL for {event.get('event_id')}")


class Publisher:
    def __init__(
        self,
        store: FileReleaseStore,
        *,
        feed_id: str,
        public_base_url: str,
        clock: Callable[[], datetime] | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ):
        if not isinstance(feed_id, str) or not feed_id or any(character.isspace() for character in feed_id):
            raise ValueError("feed_id must be a non-empty token")
        self.store = store
        self.feed_id = feed_id
        self.public_base_url = _canonical_public_base_url(public_base_url)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fault_injector = fault_injector or (lambda _stage: None)

    def _bind_and_validate_history(self, build_config_identity: str) -> list[dict[str, Any]]:
        self.store.bind_namespace(self.feed_id, self.public_base_url, build_config_identity)
        records = self.store.read_feed()
        for record in records:
            candidate = self.store.load_candidate(record["release_id"])
            if candidate is None:
                raise ReleaseIntegrityError(f"history lacks candidate {record['release_id']}")
            self._verify_candidate_release(candidate, require_record=True, records=records)
        return records

    def publish(self, request: ReleaseRequest) -> Candidate:
        with self.store.writer_lock():
            self._bind_and_validate_history(request.build_config_identity)
            pending_candidates = self.store.pending_candidates()
            self.store.validate_pending_chain(pending_candidates)
            recovered: dict[str, Candidate] = {}
            for pending in pending_candidates:
                recovered[pending.release_id] = self._advance(pending)
            if request.release_id in recovered:
                return recovered[request.release_id]
            existing = self.store.load_candidate(request.release_id)
            if existing is not None:
                self._verify_candidate_release(existing, require_record=existing.state == CandidateState.COMMITTED)
                if existing.artifact_snapshot_digest != directory_digest(request.artifact_dir):
                    raise ImmutableReleaseError("identical release inputs produced different artifact bytes")
                return existing if existing.state == CandidateState.COMMITTED else self._advance(existing)
            candidate = self._prepare(request)
            self.fault_injector("after_prepared")
            return self._advance(candidate)

    def _prepare(self, request: ReleaseRequest) -> Candidate:
        artifact = Path(request.artifact_dir).resolve(strict=False)
        store_root = self.store.root.resolve(strict=False)
        if artifact == store_root or artifact in store_root.parents or store_root in artifact.parents:
            raise ReleaseIntegrityError("artifact directory and publication store overlap")
        if not request.artifact_dir.is_dir():
            raise FileNotFoundError(request.artifact_dir)
        snapshot_path, artifact_digest = self.store.freeze_artifact(request.release_id, request.artifact_dir)
        current_release = self.store.read_verified_snapshot(request.release_id, artifact_digest)
        actual_catalog_digest = "sha256:" + hashlib.sha256(current_release["data/catalog.json"]).hexdigest()
        if actual_catalog_digest != request.catalog_digest:
            raise ReleaseIntegrityError("declared catalog digest does not match data/catalog.json")
        _validate_artifact(
            current_release,
            source_commit=request.source_commit,
            snapshot_digest=request.snapshot_digest,
        )
        prior_records = self.store.read_feed()
        head = self.store.read_feed_head()
        previous_sequence = None if head is None else head["sequence"]
        sequence = 1 if previous_sequence is None else previous_sequence + 1
        publication_time = _utc_text(self.clock())
        previous_release = None
        if head is not None:
            previous_candidate = self.store.load_candidate(head["release_id"])
            if previous_candidate is None:
                raise ReleaseIntegrityError("feed head lacks its immutable candidate")
            previous_release = self.store.read_verified_release(
                previous_candidate.release_id,
                previous_candidate.artifact_snapshot_digest,
            )
        events = derive_events(
            feed_id=self.feed_id,
            release_id=request.release_id,
            sequence=sequence,
            publication_time=publication_time,
            current_release=current_release,
            previous_release=previous_release,
            prior_records=prior_records,
            public_base_url=self.public_base_url,
        )
        candidate = Candidate(
            release_id=request.release_id,
            state=CandidateState.PREPARED,
            source_commit=request.source_commit,
            catalog_digest=request.catalog_digest,
            snapshot_digest=request.snapshot_digest,
            build_config_identity=request.build_config_identity,
            artifact_snapshot_path=snapshot_path,
            artifact_snapshot_digest=artifact_digest,
            publication_time=publication_time,
            committed_at=None,
            sequence=sequence,
            previous_sequence=previous_sequence,
            events=events,
        )
        self.store.save_candidate(candidate)
        return candidate

    def _verify_candidate_release(
        self,
        candidate: Candidate,
        *,
        require_record: bool,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        if any(
            event.get("release_id") != candidate.release_id
            or event.get("sequence") != candidate.sequence
            or event.get("published_at") != candidate.publication_time
            or event.get("feed_id") != self.feed_id
            for event in candidate.events
        ):
            raise ReleaseIntegrityError(f"candidate {candidate.release_id} contains an unbound event")
        records = self.store.read_feed() if records is None else records
        preceding = records[: candidate.sequence - 1]
        if len(preceding) != candidate.sequence - 1:
            raise ReleaseIntegrityError(f"candidate {candidate.release_id} lacks its verified prior history")
        expected_previous = None if not preceding else preceding[-1]["sequence"]
        if candidate.previous_sequence != expected_previous:
            raise ReleaseIntegrityError(f"candidate {candidate.release_id} differs from its prior history")

        snapshot = self.store.read_verified_snapshot(
            candidate.release_id,
            candidate.artifact_snapshot_digest,
        )
        if "data/catalog.json" not in snapshot or (
            "sha256:" + hashlib.sha256(snapshot["data/catalog.json"]).hexdigest()
        ) != candidate.catalog_digest:
            raise ReleaseIntegrityError(f"candidate {candidate.release_id} catalog digest differs")
        _validate_artifact(
            snapshot,
            source_commit=candidate.source_commit,
            snapshot_digest=candidate.snapshot_digest,
        )
        previous_release = None
        if preceding:
            previous_candidate = self.store.load_candidate(preceding[-1]["release_id"])
            if previous_candidate is None:
                raise ReleaseIntegrityError("prior release lacks its immutable candidate")
            previous_release = self.store.read_verified_release(
                previous_candidate.release_id,
                previous_candidate.artifact_snapshot_digest,
            )
        expected_events = derive_events(
            feed_id=self.feed_id,
            release_id=candidate.release_id,
            sequence=candidate.sequence,
            publication_time=candidate.publication_time,
            current_release=snapshot,
            previous_release=previous_release,
            prior_records=preceding,
            public_base_url=self.public_base_url,
        )
        if candidate.events != expected_events:
            raise ReleaseIntegrityError(f"candidate {candidate.release_id} events differ from its frozen snapshot")

        release = self.store.read_verified_release(candidate.release_id, candidate.artifact_snapshot_digest)
        _validate_artifact(
            release,
            source_commit=candidate.source_commit,
            snapshot_digest=candidate.snapshot_digest,
        )
        for reference_release, relative in event_references(candidate.events, self.public_base_url):
            if reference_release != candidate.release_id or relative not in release:
                raise ReleaseIntegrityError(f"candidate {candidate.release_id} event reference is outside its release")
        prospective = {
            "schema_version": 1,
            "release_id": candidate.release_id,
            "sequence": candidate.sequence,
            "source_commit": candidate.source_commit,
            "publication_time": candidate.publication_time,
            "committed_at": candidate.committed_at or candidate.publication_time,
            "previous_sequence": candidate.previous_sequence,
            "events": list(candidate.events),
        }
        _validate_record(prospective)
        if require_record:
            record = self.store.load_record(candidate.sequence)
            if record is None or record != _expected_record(candidate):
                raise ReleaseIntegrityError(f"committed candidate {candidate.release_id} differs from its feed record")
            _validate_record(record)

    def _advance(self, candidate: Candidate) -> Candidate:
        if candidate.state == CandidateState.PREPARED:
            uploaded_digest = self.store.upload_snapshot(
                candidate.release_id,
                candidate.artifact_snapshot_digest,
            )
            if uploaded_digest != candidate.artifact_snapshot_digest:
                raise ImmutableReleaseError("candidate snapshot changed after preparation")
            candidate = candidate.advance(CandidateState.UPLOADED)
            self.store.save_candidate(candidate)
            self.fault_injector("after_uploaded")
        if candidate.state == CandidateState.UPLOADED:
            self._verify_candidate_release(candidate, require_record=False)
            candidate = candidate.advance(CandidateState.CHECKED)
            self.store.save_candidate(candidate)
            self.fault_injector("after_checked")
        if candidate.state == CandidateState.CHECKED:
            self._verify_candidate_release(candidate, require_record=False)
            self.store.activate(candidate.release_id, candidate.sequence)
            candidate = candidate.advance(CandidateState.ACTIVATED).freeze_committed_at(candidate.publication_time)
            self.store.save_candidate(candidate)
            self.fault_injector("after_activated")
        if candidate.state == CandidateState.ACTIVATED:
            self._verify_candidate_release(candidate, require_record=False)
            expected_record = _expected_record(candidate)
            record = self.store.load_record(candidate.sequence)
            if record is None:
                record = expected_record
            elif record != expected_record:
                raise ImmutableReleaseError(f"feed sequence {candidate.sequence} does not match its candidate")
            _validate_record(record)
            self.store.append_record(record)
            self.fault_injector("after_log_record")
            self.store.publish_feed_head(candidate.sequence, candidate.release_id)
            candidate = candidate.advance(CandidateState.COMMITTED)
            self.store.save_candidate(candidate)
        return candidate

    def rollback(self, release_id: str) -> None:
        with self.store.writer_lock():
            candidate = self.store.load_candidate(release_id)
            if candidate is None:
                raise FileNotFoundError(f"unknown release {release_id}")
            self._bind_and_validate_history(candidate.build_config_identity)
            pending_candidates = self.store.pending_candidates()
            self.store.validate_pending_chain(pending_candidates)
            for pending in pending_candidates:
                self._advance(pending)
            self._verify_candidate_release(candidate, require_record=True)
            self.store.rollback(release_id)
