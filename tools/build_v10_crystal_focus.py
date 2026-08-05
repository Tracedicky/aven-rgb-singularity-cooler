#!/usr/bin/env python3
"""Create the V10 Crystal Focus cooler frames from a pushed-in V7 master."""

from __future__ import annotations

import argparse
import colorsys
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


WIDTH = 480
HEIGHT = 480
PALETTE = [
    (0, 225, 255),
    (48, 112, 255),
    (142, 54, 255),
    (255, 38, 206),
    (255, 86, 40),
    (255, 194, 32),
]


def cyclic_distance(a: float, b: float) -> float:
    distance = abs(a - b)
    return min(distance, 1.0 - distance)


def glow_ellipse(layer: Image.Image, box, color, alpha: int, blur: float) -> Image.Image:
    glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse(box, fill=(*color, alpha))
    return glow.filter(ImageFilter.GaussianBlur(blur))


def build_particles(count: int = 62):
    rng = random.Random(90317)
    particles = []
    for index in range(count):
        depth = rng.random()
        particles.append(
            {
                "x": rng.uniform(0, WIDTH),
                "y": rng.uniform(0, HEIGHT),
                "cycles": rng.choice((1, 1, 1, 2)),
                "orbit": rng.uniform(4, 25) * (0.35 + depth),
                "phase": rng.random(),
                "size": 1 + int(depth * 2.8),
                "alpha": int(34 + depth * 100),
                "color": PALETTE[index % len(PALETTE)],
            }
        )
    return particles


def crystal_wave(phase: float) -> Image.Image:
    crystal_mask = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(crystal_mask).polygon(
        [(240, 48), (294, 139), (316, 265), (310, 376), (240, 417), (168, 365), (176, 183)],
        fill=255,
    )

    sweep = (phase * 2.0) % 1.0
    y = 432 - sweep * 408
    hue = (0.53 + phase * 1.7) % 1.0
    rgb = tuple(int(channel * 255) for channel in colorsys.hsv_to_rgb(hue, 0.72, 1.0))

    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.line((135, y + 48, 345, y - 48), fill=(*rgb, 58), width=22)
    draw.line((150, y + 34, 330, y - 34), fill=(255, 255, 255, 55), width=4)
    layer = layer.filter(ImageFilter.GaussianBlur(8))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), crystal_mask))
    return layer


def aurora_layer(phase: float) -> Image.Image:
    broad = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    fine = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    broad_draw = ImageDraw.Draw(broad)
    fine_draw = ImageDraw.Draw(fine)

    specs = [
        (74, 31, (0, 178, 255), 0.00, 0.78),
        (118, 27, (126, 38, 255), 0.28, -0.62),
        (166, 23, (255, 31, 184), 0.55, 0.56),
    ]

    for base_y, amplitude, color, offset, direction in specs:
        points = []
        for x in range(-60, 541, 6):
            normalized_x = x / WIDTH
            y = (
                base_y
                + amplitude * math.sin(math.tau * (normalized_x * 0.86 + direction * phase + offset))
                + 10 * math.sin(math.tau * (normalized_x * 1.9 - direction * phase * 0.72 + offset))
            )
            # Keep the centre comparatively clear so the crystal remains dominant.
            y += 20 * math.exp(-((x - 240) / 115) ** 2)
            points.append((x, y))

        broad_draw.line(points, fill=(*color, 20), width=18, joint="curve")
        fine_draw.line(points, fill=(*color, 35), width=3, joint="curve")

    broad = broad.filter(ImageFilter.GaussianBlur(14))
    fine = fine.filter(ImageFilter.GaussianBlur(2.2))
    return Image.alpha_composite(broad, fine)


def particle_layer(phase: float, particles) -> Image.Image:
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    cores = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    core_draw = ImageDraw.Draw(cores)

    for particle in particles:
        progress = (particle["phase"] + particle["cycles"] * phase) % 1.0
        y = HEIGHT + 20 - progress * (HEIGHT + 40)
        x = particle["x"] + particle["orbit"] * math.sin(
            math.tau * (phase * particle["cycles"] + particle["phase"])
        )
        size = particle["size"]
        alpha = particle["alpha"]
        color = particle["color"]

        glow_draw.ellipse(
            (x - size * 2.7, y - size * 2.7, x + size * 2.7, y + size * 2.7),
            fill=(*color, int(alpha * 0.38)),
        )
        core_draw.ellipse(
            (x - max(0.65, size * 0.48), y - max(0.65, size * 0.48),
             x + max(0.65, size * 0.48), y + max(0.65, size * 0.48)),
            fill=(min(255, color[0] + 65), min(255, color[1] + 65), min(255, color[2] + 65), alpha),
        )

    glow = glow.filter(ImageFilter.GaussianBlur(3.1))
    return Image.alpha_composite(glow, cores)


def glint_layer(phase: float) -> Image.Image:
    events = [
        (0.10, (240, 52), (255, 135, 76)),
        (0.36, (294, 148), (255, 244, 188)),
        (0.61, (178, 282), (74, 224, 255)),
        (0.84, (301, 365), (255, 73, 229)),
    ]

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    core = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    core_draw = ImageDraw.Draw(core)

    for event_phase, (x, y), color in events:
        distance = cyclic_distance(phase, event_phase)
        envelope = max(0.0, 1.0 - distance / 0.042) ** 2
        if envelope <= 0:
            continue
        radius = 4 + 18 * envelope
        alpha = int(190 * envelope)
        glow_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, alpha))
        core_draw.line((x - radius * 1.8, y, x + radius * 1.8, y), fill=(255, 255, 255, alpha), width=1)
        core_draw.line((x, y - radius * 1.8, x, y + radius * 1.8), fill=(255, 255, 255, alpha), width=1)
        core_draw.ellipse((x - 1.3, y - 1.3, x + 1.3, y + 1.3), fill=(255, 255, 255, alpha))

    glow = glow.filter(ImageFilter.GaussianBlur(7))
    return Image.alpha_composite(glow, core)


def floor_layer(phase: float) -> Image.Image:
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sharp = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    sharp_draw = ImageDraw.Draw(sharp)

    for ring in range(5):
        progress = (phase * 2.0 + ring / 5.0) % 1.0
        radius = 25 + progress * 255
        vertical = radius * 0.19
        alpha = int(62 * (1.0 - progress) ** 1.7)
        color = PALETTE[(ring + int(phase * 6)) % len(PALETTE)]
        box = (240 - radius, 438 - vertical, 240 + radius, 438 + vertical)
        glow_draw.ellipse(box, outline=(*color, alpha), width=5)
        sharp_draw.ellipse(box, outline=(*color, int(alpha * 0.78)), width=1)

    # Slow caustic streaks across the reflected floor.
    for index, color in enumerate(((0, 213, 255), (255, 36, 210), (86, 72, 255))):
        points = []
        for x in range(-20, 501, 8):
            y = 442 + index * 11 + 5 * math.sin(math.tau * (x / 240 + phase * (1 + index * 0.25)))
            points.append((x, y))
        glow_draw.line(points, fill=(*color, 28), width=4)

    glow = glow.filter(ImageFilter.GaussianBlur(4.2))
    return Image.alpha_composite(glow, sharp)


def internal_glow_layer(phase: float) -> Image.Image:
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    pulse = 0.5 + 0.5 * math.sin(math.tau * phase)
    positions = [
        ((187, 84, 274, 204), (255, 76, 42), int(18 + 12 * pulse)),
        ((197, 178, 307, 314), (0, 220, 255), int(17 + 14 * (1 - pulse))),
        ((179, 294, 303, 421), (197, 42, 255), int(16 + 12 * pulse)),
    ]
    for box, color, alpha in positions:
        layer = Image.alpha_composite(layer, glow_ellipse(layer, box, color, alpha, 18))
    return layer


def selective_bloom(frame: Image.Image) -> Image.Image:
    """Bloom only existing bright pixels so black space and facet edges survive."""
    rgb = frame.convert("RGB")
    luminance = ImageOps.grayscale(rgb)
    mask = luminance.point(lambda value: 0 if value < 118 else min(255, int((value - 118) * 1.86)))
    highlights = Image.composite(rgb, Image.new("RGB", rgb.size, (0, 0, 0)), mask)
    highlights = highlights.filter(ImageFilter.GaussianBlur(7.2))
    screened = ImageChops.screen(rgb, highlights)
    return Image.blend(rgb, screened, 0.30).convert("RGBA")


def render_frame(base: Image.Image, phase: float, particles) -> Image.Image:
    pulse = 0.5 + 0.5 * math.sin(math.tau * phase)
    frame = base.convert("RGBA")
    frame = ImageEnhance.Color(frame).enhance(1.105 + 0.040 * pulse)
    frame = ImageEnhance.Contrast(frame).enhance(1.018)
    frame = selective_bloom(frame)

    frame = Image.alpha_composite(frame, aurora_layer(phase))
    frame = Image.alpha_composite(frame, internal_glow_layer(phase))
    frame = Image.alpha_composite(frame, crystal_wave(phase))
    frame = Image.alpha_composite(frame, floor_layer(phase))
    frame = Image.alpha_composite(frame, particle_layer(phase, particles))
    frame = Image.alpha_composite(frame, glint_layer(phase))
    return frame.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frames", type=int, default=270)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    particles = build_particles()

    for frame_number in range(args.frames):
        source = input_dir / f"{frame_number:04d}.png"
        if not source.exists():
            raise SystemExit(f"Missing source frame: {source}")
        phase = frame_number / args.frames
        with Image.open(source) as image:
            rendered = render_frame(image, phase, particles)
            rendered.save(output_dir / f"{frame_number:04d}.png", compress_level=3)


if __name__ == "__main__":
    main()
