from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.check_release import TARGETS, validate_release


def complete_release() -> dict:
    assets = []
    for target in TARGETS:
        suffix = ".exe" if target.startswith("win32-") else ""
        name = f"pawbot-tui-{target}{suffix}.zip"
        assets.extend({"name": name + tail, "size": 100} for tail in ("", ".sha256"))
    return {"tagName": "v0.7.4", "isDraft": False, "assets": assets}


def test_release_without_native_archives_cannot_publish_python() -> None:
    release = complete_release()
    release["assets"].pop()
    with pytest.raises(ValueError, match="assets are missing"):
        validate_release("v0.7.4", "0.7.4", release)


def test_empty_archives_and_drafts_do_not_pass_preflight() -> None:
    release = complete_release()
    release["assets"][0]["size"] = 0
    with pytest.raises(ValueError, match="assets are missing"):
        validate_release("v0.7.4", "0.7.4", release)
    release = complete_release()
    release["isDraft"] = True
    with pytest.raises(ValueError, match="must be published"):
        validate_release("v0.7.4", "0.7.4", release)


def test_tag_and_version_must_match_for_both_publish_steps() -> None:
    validate_release("v0.7.4", "0.7.4")
    validate_release("v0.7.4", "0.7.4", complete_release())
    with pytest.raises(ValueError, match="does not match"):
        validate_release("v0.7.3", "0.7.4")
    release = deepcopy(complete_release())
    release["tagName"] = "v0.7.3"
    with pytest.raises(ValueError, match="matching GitHub release"):
        validate_release("v0.7.4", "0.7.4", release)
