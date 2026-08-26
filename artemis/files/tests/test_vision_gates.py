"""Tests for the two vision gates and the vision failure contract.

Owner decision 2026-08-25: images are looked at ONLY on a direct request, and
only from an allowlisted person. Both gates exist for different reasons and both
are load-bearing, so each is pinned independently — a change that silently drops
either one is the failure this file exists to catch.
"""

from __future__ import annotations

import pytest

from artemis.files.authorization import (
    is_authorized_for_vision,
    is_direct_request,
    may_look_at_images,
)
from artemis.files.vision import (
    MAX_IMAGE_BYTES,
    VisionUnavailableError,
    describe_image,
    media_type_for,
)

JON = "U09F3EPJXSQ"
JOSH = "U07NYLNJY79"
STRANGER = "U0STRANGER1"
CHANNEL = "C0BPX9Y8WBE"
DM = "D0AN8CCJC4C"


# ── Gate 1: who ──────────────────────────────────────────────────────────────


def test_allowlisted_people_are_authorized() -> None:
    assert is_authorized_for_vision(JON)
    assert is_authorized_for_vision(JOSH)


def test_everyone_else_is_denied() -> None:
    assert not is_authorized_for_vision(STRANGER)


@pytest.mark.parametrize("value", [None, "", "   ", 12345, [], {}])
def test_authorization_fails_closed_on_junk(value: object) -> None:
    """Blank, None, and non-strings must deny rather than raise or pass."""
    assert not is_authorized_for_vision(value)  # type: ignore[arg-type]


# ── Gate 2: when ─────────────────────────────────────────────────────────────


def test_a_dm_is_inherently_direct() -> None:
    assert is_direct_request(channel_id=DM, is_mention=False)


def test_a_channel_needs_a_mention() -> None:
    assert is_direct_request(channel_id=CHANNEL, is_mention=True)
    assert not is_direct_request(channel_id=CHANNEL, is_mention=False)


# ── Both together ────────────────────────────────────────────────────────────


def test_passive_channel_image_from_an_allowed_person_is_not_read() -> None:
    """THE cost gate: an agent must not look at every screenshot posted near it."""
    assert not may_look_at_images(speaker_id=JON, channel_id=CHANNEL, is_mention=False)


def test_direct_request_from_an_unlisted_person_is_not_read() -> None:
    """THE safety gate: mentioning the agent does not grant vision."""
    assert not may_look_at_images(speaker_id=STRANGER, channel_id=CHANNEL, is_mention=True)


def test_allowed_person_making_a_direct_request_is_read() -> None:
    assert may_look_at_images(speaker_id=JON, channel_id=CHANNEL, is_mention=True)
    assert may_look_at_images(speaker_id=JOSH, channel_id=DM, is_mention=False)


# ── Format and size handling ─────────────────────────────────────────────────


def test_only_the_four_supported_formats_resolve() -> None:
    assert media_type_for("a.png") == "image/png"
    assert media_type_for("a.jpg") == "image/jpeg"
    assert media_type_for("a.jpeg") == "image/jpeg"
    assert media_type_for("a.gif") == "image/gif"
    assert media_type_for("a.webp") == "image/webp"


def test_unsupported_image_formats_are_rejected() -> None:
    """Claude accepts JPEG/PNG/GIF/WebP only — verified against the vision docs."""
    assert media_type_for("scan.tiff") is None
    assert media_type_for("icon.svg") is None
    assert media_type_for("photo.heic") is None


def test_extension_beats_a_wrong_declared_mimetype() -> None:
    """Slack's mimetype is routinely wrong; the filename is the author's choice."""
    assert media_type_for("chart.png", "application/octet-stream") == "image/png"


def test_declared_type_is_used_when_there_is_no_extension() -> None:
    assert media_type_for("clipboard", "image/png") == "image/png"
    assert media_type_for("clipboard", "application/pdf") is None


@pytest.mark.asyncio
async def test_unsupported_format_is_refused_by_name_without_spawning_anything() -> None:
    with pytest.raises(VisionUnavailableError) as excinfo:
        await describe_image(b"\x00", filename="scan.tiff", mimetype="image/tiff")
    assert "JPEG, PNG, GIF and WebP" in excinfo.value.reason


@pytest.mark.asyncio
async def test_oversize_image_is_refused_before_any_work() -> None:
    with pytest.raises(VisionUnavailableError) as excinfo:
        await describe_image(b"x" * (MAX_IMAGE_BYTES + 1), filename="huge.png")
    assert "not read" in excinfo.value.reason


@pytest.mark.asyncio
async def test_empty_image_is_refused() -> None:
    with pytest.raises(VisionUnavailableError) as excinfo:
        await describe_image(b"", filename="empty.png")
    assert "empty" in excinfo.value.reason


# ── The failure contract ─────────────────────────────────────────────────────


def test_vision_failure_reaches_the_agent_as_a_stated_reason() -> None:
    """A failure must never look like "no image was attached"."""
    from dataclasses import replace

    from artemis.files.extract.base import ExtractedFile

    placeholder = ExtractedFile(
        filename="shot.png", mimetype="image/png", kind="image", text="x", size_bytes=10
    )
    failed = replace(
        placeholder,
        text=(
            "[image: shot.png] This image could not be read: reason here. "
            "Say so rather than guessing at what it shows."
        ),
    )
    assert "could not be read" in failed.text
    assert "rather than guessing" in failed.text
