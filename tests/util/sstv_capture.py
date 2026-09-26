#!/usr/bin/env python3
"""Building blocks for the SSTV image-capture HIL tests (RGB and thermal).

The tests compose three steps: send the SSTV command, capture + decode the image
the ESP transmits over I2S, and check the image is valid. Both cameras render
into the same Robot36 320x240 frame, so only the firmware camera selection and
the validity thresholds differ between the RGB and thermal tests.
"""

import array
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import sstv
from PIL import Image, ImageStat

from tests.util.ota_hil import drain, skip
from tests.util.payload_board import NODE_PAYLOAD, MockPayloadBoard

log = logging.getLogger("sstv_capture")

SAMPLE_RATE = 16000       # ESP audio rate (src/audio/move-iiia.rs)
RECORD_SECONDS = 42       # Robot36 is ~36 s; record a bit longer
AVAILABLE_TIMEOUT = 60.0  # SSTV takes ~36 s before AVAILABLE
AVAILABLE_SLACK = 18      # AVAILABLE may arrive this long after recording ends
ROBOT36 = (320, 240)
LEADER_HZ = 1900          # SSTV calibration header leader tone
GAP_TOLERANCE = 0.5       # window resolution, DMA drain and encoder setup


def send_sstv_command(board: MockPayloadBoard) -> None:
    """Send the SSTV command, discarding any messages still queued from before."""
    drain(board)
    board.send_sstv()


def wait_for_busy(board: MockPayloadBoard) -> None:
    """Wait for the ESP's BUSY status, i.e. it accepted the SSTV command."""
    busy = board.wait_for_text(b"BUSY", timeout=5.0)
    assert busy is not None and busy.dst == NODE_PAYLOAD, "no BUSY after the SSTV command"
    log.info("ESP is BUSY, transmitting SSTV ...")


def wait_for_available(board: MockPayloadBoard) -> None:
    """Wait for the ESP's AVAILABLE status, i.e. transmit_sstv finished."""
    available = board.wait_for_text(b"AVAILABLE", timeout=AVAILABLE_TIMEOUT)
    assert available is not None and available.dst == NODE_PAYLOAD, (
        f"no AVAILABLE within {AVAILABLE_TIMEOUT:.0f}s of BUSY — transmit_sstv never finished"
    )


def capture_and_decode(board: MockPayloadBoard):
    """Record the I2S audio through the transmission, then decode the Robot36 image.

    Waits for the ESP's AVAILABLE (transmission done), verifies the recording
    isn't silent (a silent-but-clocked capture means no I2S data reached the Pi),
    and returns the decoded PIL image.
    """
    samples, rate = record_transmission(board)
    return decode_images(samples, rate, count=1)[0]


def record_transmission(
    board: MockPayloadBoard, *, seconds: int = RECORD_SECONDS
) -> tuple[list[int], int]:
    """Record the I2S audio until the ESP reports AVAILABLE; return (samples, rate).

    Fails if the recording is silent: a silent-but-clocked capture means no I2S
    data reached the Pi.
    """
    device = alsa_capture_device()
    wav = Path(tempfile.gettempdir()) / f"sstv_capture_{os.getpid()}.wav"

    log.info("recording I2S for %ds on %s", seconds, device)
    rec = subprocess.Popen(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "2", "-d", str(seconds), "-t", "wav", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    time.sleep(0.5)  # let capture start (the tones only begin a few seconds in)
    if rec.poll() is not None:  # arecord died immediately (bad device/format)
        err = rec.stderr.read().decode("utf-8", "replace") if rec.stderr else ""
        raise AssertionError(f"arecord failed to start: {err.strip()}")

    available = board.wait_for_text(b"AVAILABLE", timeout=seconds + AVAILABLE_SLACK)
    assert available is not None, "no AVAILABLE — SSTV never finished"
    log.info("AVAILABLE received; finishing recording")
    rc = rec.wait(timeout=seconds + 10)  # arecord stops itself at -d
    if rc not in (0, None):
        log.warning("arecord exited %s", rc)

    samples, rate = _read_active_channel(wav)
    peak = max((abs(s) for s in samples), default=0)
    log.info("recorded peak=%d (%.1f%% FS)", peak, 100 * peak / 32768)
    assert peak > 200, (
        f"capture is silent (peak={peak}). The I2S clock is working but no audio data "
        "arrived — check the data wire (ESP data-out -> Pi GPIO20 / pin 38), or the ESP "
        "was not actually transmitting SSTV."
    )
    return samples, rate


def decode_images(samples, rate: int, *, count: int) -> list:
    """Decode the recording, asserting it holds exactly `count` complete images."""
    images = sstv.decode(samples, rate, mode=sstv.Mode.ROBOT_36)
    assert len(images) == count, f"decoded {len(images)} SSTV images, expected {count}"
    assert all(i.info["sstv_complete"] for i in images), "an image was cut off"
    return images


def assert_gap_between_images(samples, rate: int, *, seconds: float) -> None:
    """Check the silence between two consecutive transmissions lasts `seconds`."""
    first, second = _transmission_starts(samples, rate)
    gap = second - first - _robot36_duration(rate)
    log.info("transmissions start at %.2fs and %.2fs; gap %.2fs", first, second, gap)
    assert abs(gap - seconds) <= GAP_TOLERANCE, f"gap is {gap:.2f}s, expected {seconds:.1f}s"


def assert_valid_image(image, *, min_std: float, min_smoothness: float) -> None:
    """Check the decoded image is a real picture: right size, has content, not noise."""
    (w, h), std, smooth = _image_stats(image)
    log.info("decoded %dx%d std=(%.0f,%.0f,%.0f) smoothness=%.2f", w, h, *std, smooth)
    assert (w, h) == ROBOT36, f"decoded {w}x{h}, expected {ROBOT36}"
    assert max(std) > min_std, f"image is flat/blank (per-channel std {std}) — no real picture"
    assert smooth > min_smoothness, f"image looks like noise (smoothness {smooth:.2f}) — bad capture/decode"


def alsa_capture_device() -> str:
    """The ESP I2S ALSA capture device (hw:<card>,0), or skip if not set up."""
    dev = os.environ.get("SSTV_ALSA_DEVICE")
    if dev:
        return dev
    if not shutil.which("arecord"):
        skip("arecord not installed (alsa-utils) — capture not set up")
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        skip("no ALSA cards — I2S capture not set up on this host")
    # Match the card by name/id and use its number (robust to renumbering; the
    # ALSA card id is 'espi2s', the pretty name 'esp-i2s').
    for line in cards.splitlines():
        if "espi2s" in line or "esp-i2s" in line:
            m = re.match(r"\s*(\d+)\s", line)
            if m:
                return f"hw:{m.group(1)},0"
    skip("no 'esp-i2s' capture card — set up the Pi I2S slave capture first")


def _read_active_channel(path: Path) -> tuple[list[int], int]:
    """Read a 16-bit WAV, returning (samples, rate) for the loudest channel.

    The ESP puts the SSTV tones on a single I2S channel and silence on the other,
    so pick the higher-energy channel rather than the first one.
    """
    with wave.open(str(path), "rb") as w:
        channels = max(w.getnchannels(), 1)
        rate = w.getframerate()
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
    if channels <= 1:
        return list(samples), rate
    energy = [sum(x * x for x in samples[c::channels]) for c in range(channels)]
    best = max(range(channels), key=energy.__getitem__)
    return list(samples[best::channels]), rate


def _image_stats(image) -> tuple[tuple[int, int], list[float], float]:
    """(size, per-channel stddev, horizontal-neighbour similarity fraction)."""
    image = image.convert("RGB")
    w, h = image.size
    std = ImageStat.Stat(image).stddev
    px = image.load()
    similar = pairs = 0
    for y in range(h):
        prev = px[0, y]
        for x in range(1, w):
            cur = px[x, y]
            for c in range(3):
                if abs(cur[c] - prev[c]) <= 24:
                    similar += 1
                pairs += 1
            prev = cur
    smoothness = similar / pairs if pairs else 0.0
    return (w, h), std, smoothness


def _robot36_duration(rate: int) -> float:
    """Length in seconds of one Robot36 transmission, header included."""
    return len(sstv.encode(Image.new("RGB", ROBOT36), sstv.Mode.ROBOT_36, rate)) / rate


def _transmission_starts(samples, rate: int) -> list[float]:
    """Start times in seconds of every SSTV header in the recording.

    A header is 300 ms of 1900 Hz leader, a 10 ms break and another 300 ms leader.
    Image lines carry a 1200 Hz sync pulse every 150 ms, so an unbroken ~610 ms
    stretch of 1900 Hz only occurs in a header. Much longer stretches are
    rejected too, in case the idle I2S output loops a mid-grey (1900 Hz) buffer.
    """
    window = rate // 100  # 10 ms: exactly 19 cycles of 1900 Hz, so no leakage
    x = np.asarray(samples, dtype=np.float64)
    frames = x[: len(x) // window * window].reshape(-1, window)
    probe = np.exp(-2j * np.pi * LEADER_HZ * np.arange(window) / rate)
    tone = 2 * np.abs(frames @ probe) ** 2 / window
    energy = np.maximum((frames**2).sum(axis=1), 1e-9)
    is_leader = np.concatenate([[False], tone / energy > 0.7, [False]])

    edges = np.flatnonzero(np.diff(is_leader.astype(int)))
    runs = []
    for start, end in edges.reshape(-1, 2):
        if runs and start - runs[-1][1] <= 3:  # bridge the 10 ms break
            runs[-1][1] = end
        else:
            runs.append([start, end])
    return [start * window / rate for start, end in runs if 50 <= end - start <= 80]
