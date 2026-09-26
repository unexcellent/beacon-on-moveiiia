#!/usr/bin/env python3
"""HIL test: with both cameras enabled, SSTV sends the RGB then the thermal image.

transmit_sstv sends one Robot36 image per camera, in camera order (src/main.rs),
waiting 5 s between them. We capture both off the Pi's I2S slave input, decode
them, check they look like real pictures, and check the gap between them.

Requires:
  - ESP running firmware with both cameras enabled:
        cargo build --release   (flash / OTA it)
  - Both cameras connected and working, the thermal one pointed at a scene with
    some temperature contrast (e.g. a hand / warm object).
  - I2S capture on the Pi as an ALSA card named 'esp-i2s' (see hosts/odin nix).
  - `arecord` (alsa-utils) and the `sstv` Python package (pip install sstv).

Skips cleanly (exit 77) when the capture card / arecord is missing.

Run:  ./.venv/bin/python tests/test_both_cameras_transmitting.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.util.ota_hil import run_case
from tests.util.payload_board import MockPayloadBoard
from tests.util.sstv_capture import (
    assert_gap_between_images,
    assert_valid_image,
    decode_images,
    record_transmission,
    send_sstv_command,
)


def test_both_cameras_transmitting(board: MockPayloadBoard) -> None:
    send_sstv_command(board)
    samples, rate = record_transmission(board, seconds=90)
    rgb, thermal = decode_images(samples, rate, count=2)
    assert_valid_image(rgb, min_std=6.0, min_smoothness=0.35)
    assert_valid_image(thermal, min_std=5.0, min_smoothness=0.35)
    assert_gap_between_images(samples, rate, seconds=5.0)


if __name__ == "__main__":
    sys.exit(run_case(test_both_cameras_transmitting))
