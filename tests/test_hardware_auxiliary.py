from __future__ import annotations

import base64
import io
from pathlib import Path

from difra.hardware import auxiliary


def test_encode_image_to_base64_roundtrip_bytes(tmp_path: Path):
    image_path = tmp_path / "sample.bin"
    payload = b"\x00\x01\x02demo-bytes"
    image_path.write_bytes(payload)

    encoded = auxiliary.encode_image_to_base64(str(image_path))

    assert encoded == base64.b64encode(payload).decode("utf-8")


def test_encode_image_to_base64_returns_none_when_file_missing(tmp_path: Path):
    encoded = auxiliary.encode_image_to_base64(str(tmp_path / "missing.bin"))

    assert encoded is None


def test_decode_base64_to_image_returns_none_on_invalid_payload():
    # The helper currently handles decode/import errors by returning None.
    assert auxiliary.decode_base64_to_image("not-base64") is None


def test_decode_base64_to_image_can_succeed_when_image_hooks_are_present(monkeypatch):
    image_bytes = b"fake-image"
    encoded = base64.b64encode(image_bytes).decode("utf-8")

    class _FakeImageModule:
        @staticmethod
        def open(stream):
            return {"opened_bytes": stream.read()}

    monkeypatch.setattr(auxiliary, "BytesIO", io.BytesIO, raising=False)
    monkeypatch.setattr(auxiliary, "Image", _FakeImageModule, raising=False)

    decoded = auxiliary.decode_base64_to_image(encoded)

    assert decoded == {"opened_bytes": image_bytes}


def test_geometry_helpers_cover_circle_square_and_lines():
    assert auxiliary.in_circle(1, 1, 0, 0, radius=2) is True
    assert auxiliary.in_circle(3, 0, 0, 0, radius=2) is False

    assert auxiliary.in_square(1, 1, 0, 0, side=4) is True
    assert auxiliary.in_square(5, 0, 0, 0, side=4) is False

    assert auxiliary.in_x_line(2, 0, 0, 0, side=5) is True
    assert auxiliary.in_x_line(2, 1, 0, 0, side=5) is False

    assert auxiliary.in_y_line(0, 2, 0, 0, side=5) is True
    assert auxiliary.in_y_line(1, 2, 0, 0, side=5) is False
