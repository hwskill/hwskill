#!/usr/bin/env python3

"""Render GitCode changed-file payloads as standard Git text patches."""

import re
from typing import Mapping, Optional, Sequence, Tuple


_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)
_REGULAR_FILE_MODES = {"100644", "100755"}


class PatchFormatError(ValueError):
    """Raised when changed-file data cannot produce a complete text patch."""


def render_git_patch(files: Sequence[Mapping[str, object]]) -> str:
    validated = [_validate_file_patch(file_info) for file_info in files]
    return "".join(_render_file_patch(file_info) for file_info in validated)


def _validate_file_patch(
    file_info: Mapping[str, object],
) -> Mapping[str, object]:
    patch = file_info.get("patch")
    if not isinstance(patch, Mapping):
        raise PatchFormatError("changed file is missing patch data")
    if _required_boolean(patch, "too_large"):
        raise PatchFormatError("changed file is too large for a text patch")

    diff = patch.get("diff")
    if not isinstance(diff, str):
        raise PatchFormatError("changed file is missing a text diff")
    diff_lines = diff.splitlines(keepends=True)
    if any(
        line.startswith("Binary files ") or line.rstrip("\r\n") == "GIT binary patch"
        for line in diff_lines
    ):
        raise PatchFormatError("changed file contains an unsupported binary patch")

    is_new_file = _required_boolean(patch, "new_file")
    is_deleted_file = _required_boolean(patch, "deleted_file")
    is_renamed_file = _required_boolean(patch, "renamed_file")
    if sum((is_new_file, is_deleted_file, is_renamed_file)) > 1:
        raise PatchFormatError("changed file has contradictory status flags")

    _validate_status_matrix(
        patch, diff, is_new_file, is_deleted_file, is_renamed_file
    )

    (
        added_lines,
        removed_lines,
        has_hunk,
        consumed_old_lines,
        consumed_new_lines,
    ) = _count_hunk_changes(diff)
    if diff and not has_hunk:
        raise PatchFormatError("changed file has content changes but is missing a hunk")
    if is_new_file and consumed_old_lines:
        raise PatchFormatError("new file diff cannot consume old lines")
    if is_deleted_file and consumed_new_lines:
        raise PatchFormatError("deleted file diff cannot produce new lines")
    _validate_line_count(
        "added",
        added_lines,
        _expected_line_count(file_info, patch, "added_lines", "additions"),
    )
    _validate_line_count(
        "removed",
        removed_lines,
        _expected_line_count(file_info, patch, "removed_lines", "deletions"),
    )
    return file_info


def _validate_status_matrix(
    patch: Mapping[str, object],
    diff: str,
    is_new_file: bool,
    is_deleted_file: bool,
    is_renamed_file: bool,
) -> None:
    if is_new_file:
        new_path = _required_path(patch.get("new_path"), "new")
        _required_mode(patch.get("b_mode"), "new")
        old_path = patch.get("old_path")
        old_mode = patch.get("a_mode")
        if old_path is None:
            if old_mode is not None:
                raise PatchFormatError("new file cannot have an old mode")
            return
        old_path = _required_path(old_path, "old")
        if old_path != new_path:
            raise PatchFormatError("new file old path must match the new path")
        if old_mode != "0":
            raise PatchFormatError("new file old mode sentinel must be 0")
        return

    if is_deleted_file:
        _required_path(patch.get("old_path"), "old")
        _required_mode(patch.get("a_mode"), "old")
        if patch.get("new_path") is not None:
            raise PatchFormatError("deleted file cannot have a new path")
        if patch.get("b_mode") is not None:
            raise PatchFormatError("deleted file cannot have a new mode")
        return

    old_path = _required_path(patch.get("old_path"), "old")
    new_path = _required_path(patch.get("new_path"), "new")
    old_mode = _required_mode(patch.get("a_mode"), "old")
    new_mode = _required_mode(patch.get("b_mode"), "new")
    if is_renamed_file:
        if old_path == new_path:
            raise PatchFormatError("rename must use different paths")
    elif old_path != new_path:
        raise PatchFormatError("path change requires renamed_file=true")
    elif not diff and old_mode == new_mode:
        raise PatchFormatError("ordinary file has no changes")


def _render_file_patch(file_info: Mapping[str, object]) -> str:
    patch = file_info["patch"]
    assert isinstance(patch, Mapping)
    diff = patch["diff"]
    assert isinstance(diff, str)

    is_new_file = patch.get("new_file") is True
    is_deleted_file = patch.get("deleted_file") is True
    is_renamed_file = patch.get("renamed_file") is True

    if is_new_file:
        new_path = _required_path(patch.get("new_path"), "new")
        file_patch = (
            f"diff --git {_quote_git_path('a/' + new_path)} "
            f"{_quote_git_path('b/' + new_path)}\n"
            f"new file mode {_required_mode(patch.get('b_mode'), 'new')}\n"
        )
        if diff:
            file_patch += (
                "--- /dev/null\n"
                f"+++ {_quote_git_path('b/' + new_path)}\n"
                f"{diff}"
            )
    elif is_deleted_file:
        old_path = _required_path(patch.get("old_path"), "old")
        file_patch = (
            f"diff --git {_quote_git_path('a/' + old_path)} "
            f"{_quote_git_path('b/' + old_path)}\n"
            f"deleted file mode {_required_mode(patch.get('a_mode'), 'old')}\n"
        )
        if diff:
            file_patch += (
                f"--- {_quote_git_path('a/' + old_path)}\n"
                "+++ /dev/null\n"
                f"{diff}"
            )
    else:
        old_path = _required_path(patch.get("old_path"), "old")
        new_path = _required_path(patch.get("new_path"), "new")
        old_mode = _required_mode(patch.get("a_mode"), "old")
        new_mode = _required_mode(patch.get("b_mode"), "new")
        file_patch = (
            f"diff --git {_quote_git_path('a/' + old_path)} "
            f"{_quote_git_path('b/' + new_path)}\n"
        )
        if is_renamed_file:
            if not diff:
                file_patch += "similarity index 100%\n"
            file_patch += (
                f"rename from {_quote_git_path(old_path)}\n"
                f"rename to {_quote_git_path(new_path)}\n"
            )
        if old_mode != new_mode:
            file_patch += f"old mode {old_mode}\nnew mode {new_mode}\n"
        if diff:
            file_patch += (
                f"--- {_quote_git_path('a/' + old_path)}\n"
                f"+++ {_quote_git_path('b/' + new_path)}\n"
                f"{diff}"
            )

    return _finish_file_patch(file_patch)


def _finish_file_patch(file_patch: str) -> str:
    return file_patch if file_patch.endswith("\n") else f"{file_patch}\n"


def _required_path(value: object, side: str) -> str:
    if not isinstance(value, str) or not value:
        raise PatchFormatError(f"changed file is missing a {side} path")
    _validate_path(value)
    return value


def _validate_path(path: str) -> None:
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise PatchFormatError(f"changed file has an unsafe path: {path!r}") from None

    components = path.split("/")
    if any(component in ("", ".", "..") for component in components) or any(
        character in path for character in ("\0", "\r", "\n")
    ):
        raise PatchFormatError(f"changed file has an unsafe path: {path!r}")


def _quote_git_path(path: str) -> str:
    needs_quotes = any(
        character.isspace()
        or character in ('"', "\\")
        or ord(character) < 0x20
        or ord(character) >= 0x7F
        for character in path
    )
    if not needs_quotes:
        return path

    try:
        encoded_path = path.encode("utf-8")
    except UnicodeEncodeError:
        raise PatchFormatError(f"changed file has an unsafe path: {path!r}") from None

    escaped = []
    for byte in encoded_path:
        character = chr(byte)
        if character == '"':
            escaped.append('\\"')
        elif character == "\\":
            escaped.append("\\\\")
        elif character == "\t":
            escaped.append("\\t")
        elif 0x20 <= byte < 0x7F:
            escaped.append(character)
        else:
            escaped.append(f"\\{byte:03o}")
    return f'"{"".join(escaped)}"'


def _required_mode(value: object, side: str) -> str:
    if not isinstance(value, str) or not value:
        raise PatchFormatError(f"changed file is missing a {side} mode")
    if value not in _REGULAR_FILE_MODES:
        raise PatchFormatError(f"changed file has an unsupported {side} mode: {value}")
    return value


def _required_boolean(patch: Mapping[str, object], name: str) -> bool:
    value = patch.get(name)
    if not isinstance(value, bool):
        raise PatchFormatError(f"changed file {name} must be a boolean")
    return value


def _count_hunk_changes(diff: str) -> Tuple[int, int, bool, int, int]:
    diff_lines = diff.splitlines(keepends=True)
    if diff_lines and not diff_lines[0].startswith("@@"):
        raise PatchFormatError("text diff must start with a hunk header")

    added_lines = 0
    removed_lines = 0
    has_hunk = False
    total_old_lines = 0
    total_new_lines = 0
    expected_old_lines: Optional[int] = None
    expected_new_lines: Optional[int] = None
    consumed_old_lines = 0
    consumed_new_lines = 0
    hunk_has_change = False
    last_content_prefix: Optional[str] = None
    old_side_ended = False
    new_side_ended = False
    previous_old_end: Optional[int] = None
    previous_new_end: Optional[int] = None
    zero_range_hunk_seen = False

    def validate_previous_hunk() -> None:
        if expected_old_lines is None or expected_new_lines is None:
            return
        if consumed_old_lines != expected_old_lines:
            raise PatchFormatError(
                "hunk old line count is "
                f"{consumed_old_lines}, expected {expected_old_lines}"
            )
        if consumed_new_lines != expected_new_lines:
            raise PatchFormatError(
                "hunk new line count is "
                f"{consumed_new_lines}, expected {expected_new_lines}"
            )
        if not hunk_has_change:
            raise PatchFormatError("hunk must contain an added or removed line")

    for line in diff_lines:
        if line.startswith("@@"):
            validate_previous_hunk()
            if zero_range_hunk_seen:
                raise PatchFormatError("zero-count range must be the only hunk")
            header = line.rstrip("\r\n")
            match = _HUNK_HEADER.fullmatch(header)
            if match is None:
                raise PatchFormatError(f"malformed hunk header: {header!r}")
            old_start = int(match.group(1))
            expected_old_lines = int(match.group(2) or "1")
            new_start = int(match.group(3))
            expected_new_lines = int(match.group(4) or "1")
            if expected_old_lines and old_start == 0:
                raise PatchFormatError(
                    "old range with nonzero count must start at 1 or later"
                )
            if expected_new_lines and new_start == 0:
                raise PatchFormatError(
                    "new range with nonzero count must start at 1 or later"
                )
            if not expected_old_lines and old_start != 0:
                raise PatchFormatError(
                    "zero-count old range must start at 0"
                )
            if not expected_new_lines and new_start != 0:
                raise PatchFormatError(
                    "zero-count new range must start at 0"
                )

            has_zero_range = not expected_old_lines or not expected_new_lines
            if has_zero_range and has_hunk:
                raise PatchFormatError("zero-count range must be the only hunk")
            zero_range_hunk_seen = has_zero_range

            old_begin = old_start - 1 if expected_old_lines else old_start
            new_begin = new_start - 1 if expected_new_lines else new_start
            old_gap = (
                old_begin
                if previous_old_end is None
                else old_begin - previous_old_end
            )
            new_gap = (
                new_begin
                if previous_new_end is None
                else new_begin - previous_new_end
            )
            if old_gap < 0:
                raise PatchFormatError("old hunks are out of order or overlapping")
            if new_gap < 0:
                raise PatchFormatError("new hunks are out of order or overlapping")
            if old_gap != new_gap:
                raise PatchFormatError("old and new hunk gaps differ")
            previous_old_end = old_begin + expected_old_lines
            previous_new_end = new_begin + expected_new_lines
            consumed_old_lines = 0
            consumed_new_lines = 0
            hunk_has_change = False
            last_content_prefix = None
            has_hunk = True
        elif expected_old_lines is None:
            continue
        elif line.startswith(" "):
            if old_side_ended:
                raise PatchFormatError("old side continues after no-newline marker")
            if new_side_ended:
                raise PatchFormatError("new side continues after no-newline marker")
            consumed_old_lines += 1
            consumed_new_lines += 1
            total_old_lines += 1
            total_new_lines += 1
            last_content_prefix = " "
        elif line.startswith("+"):
            if new_side_ended:
                raise PatchFormatError("new side continues after no-newline marker")
            added_lines += 1
            consumed_new_lines += 1
            total_new_lines += 1
            hunk_has_change = True
            last_content_prefix = "+"
        elif line.startswith("-"):
            if old_side_ended:
                raise PatchFormatError("old side continues after no-newline marker")
            removed_lines += 1
            consumed_old_lines += 1
            total_old_lines += 1
            hunk_has_change = True
            last_content_prefix = "-"
        elif line.rstrip("\r\n") == "\\ No newline at end of file":
            if last_content_prefix is None:
                raise PatchFormatError(
                    "no-newline marker must follow a hunk content line"
                )
            if last_content_prefix in (" ", "-"):
                old_side_ended = True
            if last_content_prefix in (" ", "+"):
                new_side_ended = True
            last_content_prefix = None
        else:
            raise PatchFormatError(f"malformed hunk body line: {line!r}")

        if expected_old_lines is not None and consumed_old_lines > expected_old_lines:
            raise PatchFormatError(
                "hunk old line count exceeds " f"declared count {expected_old_lines}"
            )
        if expected_new_lines is not None and consumed_new_lines > expected_new_lines:
            raise PatchFormatError(
                "hunk new line count exceeds " f"declared count {expected_new_lines}"
            )

    validate_previous_hunk()
    return (
        added_lines,
        removed_lines,
        has_hunk,
        total_old_lines,
        total_new_lines,
    )


def _expected_line_count(
    file_info: Mapping[str, object],
    patch: Mapping[str, object],
    nested_name: str,
    top_level_name: str,
) -> Optional[object]:
    if nested_name in patch:
        return patch[nested_name]
    return file_info.get(top_level_name)


def _validate_line_count(
    kind: str, actual: int, expected: Optional[object]
) -> None:
    if expected is None:
        return
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise PatchFormatError(f"changed file has an invalid {kind} line count")
    if actual != expected:
        raise PatchFormatError(
            f"changed file {kind} line count is {actual}, expected {expected}"
        )
