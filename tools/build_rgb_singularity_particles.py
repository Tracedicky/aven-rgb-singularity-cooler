#!/usr/bin/env python3
"""Composite colourful bouncing particles onto the RGB Singularity Cooler loop.

The source clip (Aven_RGB_Singularity_Cooler.mp4) is a 480x480, 30fps, 6s
(180 frame) seamless loop of a real cooler face. This script decodes every
frame, screen-blends a swarm of rainbow particles that bounce radially inside
the ring assembly on top, and re-encodes.

Every particle's radial and angular motion is driven by whole-number cycle
counts across the 180 frame loop, so frame 180 of the particle field is
bit-for-bit the same phase as frame 0 — the added motion cannot introduce a
seam into a source clip that already loops cleanly.
"""

from __future__ import annotations

import argparse
import math
import subprocess
from pathlib import Path

import numpy as np

SIZE = 480
FPS = 30
DURATION = 6.0
FRAMES = int(round(FPS * DURATION))
FRAME_BYTES = SIZE * SIZE * 3

# Product-face geometry, measured from a radial brightness profile averaged
# across sample frames: bright reflective dome in the centre, then the ring
# assembly running from just past the dome out to the black chassis rim.
CENTER = (240.0, 238.0)
RADIUS_MIN = 62.0
RADIUS_MAX = 224.0

TAU = math.tau


def hsv_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    hue = np.mod(hue, 1.0) * 6.0
    sector = np.floor(hue).astype(np.int32)
    fraction = hue - sector

    p = value * (1.0 - saturation)
    q = value * (1.0 - saturation * fraction)
    t = value * (1.0 - saturation * (1.0 - fraction))

    sector = sector % 6
    red = np.select([sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
                     [value, q, p, p, t], default=value)
    green = np.select([sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
                       [t, value, value, q, p], default=p)
    blue = np.select([sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
                      [p, p, t, value, value], default=q)
    return np.stack([red, green, blue], axis=-1).astype(np.float32)


def box_blur(array: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius < 1:
        return array
    length = array.shape[axis]
    padding = [(0, 0)] * array.ndim
    padding[axis] = (radius, radius)
    padded = np.pad(array, padding, mode="edge")
    lead_shape = list(padded.shape)
    lead_shape[axis] = 1
    lead = np.zeros(lead_shape, dtype=np.float32)
    running = np.concatenate([lead, np.cumsum(padded, axis=axis, dtype=np.float32)], axis=axis)
    span = 2 * radius + 1
    index_hi: list = [slice(None)] * array.ndim
    index_hi[axis] = slice(span, span + length)
    index_lo: list = [slice(None)] * array.ndim
    index_lo[axis] = slice(0, length)
    return (running[tuple(index_hi)] - running[tuple(index_lo)]) / np.float32(span)


def soft_blur(array: np.ndarray, radius: int, passes: int = 2) -> np.ndarray:
    result = array
    for _ in range(passes):
        result = box_blur(result, radius, 0)
        result = box_blur(result, radius, 1)
    return result


class ParticleSwarm:
    def __init__(self, count: int = 110, seed: int = 7421) -> None:
        rng = np.random.default_rng(seed)

        # Whole-number cycle counts: every quantity below returns to its
        # starting value after exactly one 180 frame loop.
        self.radial_freq = rng.choice([1, 2, 3], size=count).astype(np.float32)
        self.radial_phase = rng.random(count).astype(np.float32)
        self.angular_freq = rng.choice([-2, -1, 1, 2], size=count).astype(np.float32)
        self.angular_phase = rng.random(count).astype(np.float32)
        self.twinkle_freq = rng.choice([3, 4, 5, 6], size=count).astype(np.float32)
        self.twinkle_phase = rng.random(count).astype(np.float32)

        band_lo = rng.uniform(RADIUS_MIN, RADIUS_MIN + 55.0, size=count)
        band_hi = rng.uniform(RADIUS_MAX - 60.0, RADIUS_MAX, size=count)
        self.radius_lo = np.minimum(band_lo, band_hi).astype(np.float32)
        self.radius_hi = np.maximum(band_lo, band_hi).astype(np.float32)

        self.base_angle = rng.random(count).astype(np.float32) * TAU
        self.hue = rng.random(count).astype(np.float32)
        self.hue_drift = rng.uniform(0.3, 1.2, size=count).astype(np.float32) * rng.choice([-1, 1], size=count)
        self.energy = rng.uniform(0.55, 1.35, size=count).astype(np.float32)
        # A handful of larger "hero" sparks stand out against the general
        # fine dust so the effect reads at a glance, not just up close.
        hero = rng.random(count) > 0.86
        self.size = np.where(hero, rng.uniform(3.4, 5.2, size=count), rng.uniform(1.1, 2.2, size=count)).astype(np.float32)
        self.energy = np.where(hero, self.energy * 1.35, self.energy).astype(np.float32)

    def _positions(self, phase: float):
        bounce = 0.5 + 0.5 * np.sin(TAU * (self.radial_freq * phase + self.radial_phase))
        r = self.radius_lo + (self.radius_hi - self.radius_lo) * bounce
        angle = self.base_angle + TAU * (self.angular_freq * phase + self.angular_phase)
        x = CENTER[0] + r * np.cos(angle)
        y = CENTER[1] + r * np.sin(angle)
        return x, y

    def render(self, frame: int) -> np.ndarray:
        phase = frame / FRAMES
        layer = np.zeros((SIZE, SIZE, 3), dtype=np.float32)

        twinkle = 0.55 + 0.45 * np.sin(TAU * (self.twinkle_freq * phase + self.twinkle_phase))
        hue = self.hue + self.hue_drift * phase
        colour = hsv_to_rgb(hue, np.full_like(hue, 0.90), self.energy * twinkle)

        # A short trail of time-lagged copies, fading out, gives each bounce
        # a comet-like streak instead of a static pixel popping frame to
        # frame. The lag is expressed as a phase offset, so it stays exactly
        # periodic across the loop just like the main position.
        for lag, weight in ((0.0, 1.0), (-0.010, 0.55), (-0.020, 0.28), (-0.032, 0.12)):
            x, y = self._positions(phase + lag)
            px = np.clip(np.round(x).astype(np.int32), 1, SIZE - 2)
            py = np.clip(np.round(y).astype(np.int32), 1, SIZE - 2)

            core = colour * (self.size * weight)[:, None]
            np.add.at(layer, (py, px), core)
            np.add.at(layer, (py + 1, px), core * 0.42)
            np.add.at(layer, (py - 1, px), core * 0.42)
            np.add.at(layer, (py, px + 1), core * 0.42)
            np.add.at(layer, (py, px - 1), core * 0.42)
            np.add.at(layer, (py + 1, px + 1), core * 0.20)
            np.add.at(layer, (py + 1, px - 1), core * 0.20)
            np.add.at(layer, (py - 1, px + 1), core * 0.20)
            np.add.at(layer, (py - 1, px - 1), core * 0.20)

        return layer


def band_mask() -> np.ndarray:
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    r = np.hypot(xx - CENTER[0], yy - CENTER[1])
    inner = np.clip((r - (RADIUS_MIN - 14.0)) / 14.0, 0.0, 1.0)
    outer = np.clip(((RADIUS_MAX + 14.0) - r) / 14.0, 0.0, 1.0)
    return (inner * outer).astype(np.float32)


def decode_frames(source: Path, ffmpeg: str):
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-vsync", "0",
        "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    for _ in range(FRAMES):
        raw = process.stdout.read(FRAME_BYTES)
        if len(raw) < FRAME_BYTES:
            break
        yield np.frombuffer(raw, dtype=np.uint8).reshape(SIZE, SIZE, 3)
    process.stdout.close()
    process.wait()


def encode_frames(frames_iter, destination: Path, ffmpeg: str, bitrate: str) -> None:
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SIZE}x{SIZE}", "-r", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "2M",
        "-profile:v", "high", "-preset", "slow",
        "-movflags", "+faststart",
        str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames_iter:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the output")


def main() -> None:
    parser = argparse.ArgumentParser(description="Add bouncing rainbow particles to the RGB Singularity Cooler loop.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent / "Aven_RGB_Singularity_Cooler.mp4")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent / "Aven_RGB_Singularity_Cooler_Particles.mp4")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--bitrate", default="1650k")
    parser.add_argument("--preview", type=Path, help="write sample PNG frames here instead of encoding")
    parser.add_argument("--preview-frames", type=int, nargs="*", default=[0, 45, 90, 135, 179])
    args = parser.parse_args()

    swarm = ParticleSwarm()
    mask = band_mask()

    if args.preview:
        from PIL import Image

        args.preview.mkdir(parents=True, exist_ok=True)
        wanted = set(args.preview_frames)
        index = 0
        for base in decode_frames(args.source, args.ffmpeg):
            if index in wanted:
                particles = swarm.render(index) * mask[..., None]
                glow = soft_blur(particles, radius=3, passes=2) * 0.70
                combined = np.clip(particles + glow, 0.0, 4.0)
                base_f = base.astype(np.float32) / 255.0
                blended = 1.0 - (1.0 - base_f) * np.exp(-combined)
                out = np.clip(blended * 255.0 + 0.5, 0, 255).astype(np.uint8)
                Image.fromarray(out).save(args.preview / f"particles_{index:03d}.png")
                print(f"preview frame {index}")
            index += 1
        return

    def frames():
        index = 0
        for base in decode_frames(args.source, args.ffmpeg):
            particles = swarm.render(index) * mask[..., None]
            glow = soft_blur(particles, radius=3, passes=2) * 0.70
            combined = np.clip(particles + glow, 0.0, 4.0)
            base_f = base.astype(np.float32) / 255.0
            blended = 1.0 - (1.0 - base_f) * np.exp(-combined)
            out = np.clip(blended * 255.0 + 0.5, 0, 255).astype(np.uint8)
            if index % 30 == 0:
                print(f"  frame {index}/{FRAMES}", flush=True)
            index += 1
            yield out

    encode_frames(frames(), args.output, args.ffmpeg, args.bitrate)
    size_mb = args.output.stat().st_size / 1_048_576
    print(f"encoded {FRAMES} frames -> {args.output} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
