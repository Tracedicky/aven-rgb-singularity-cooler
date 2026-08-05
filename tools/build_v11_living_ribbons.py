#!/usr/bin/env python3
"""Build V11 Living Ribbons while preserving the V10 Crystal Focus treatment.

The background ribbons receive a phase-locked liquid displacement and subtle
travelling colour/caustic waves.  The crystal and pedestal are explicitly
protected so their V10 fidelity is unchanged.
"""

from __future__ import annotations

import argparse
import colorsys
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from build_v10_crystal_focus import HEIGHT, WIDTH, build_particles, render_frame


TAU = math.tau


def _soft_ribbon_mask(reference: Image.Image) -> Image.Image:
    """Find the saturated ribbon texture and protect the foreground crystal."""
    hsv = np.asarray(reference.convert("HSV"), dtype=np.float32)
    saturation = hsv[..., 1]
    value = hsv[..., 2]

    chroma = np.clip((saturation - 42.0) / 108.0, 0.0, 1.0)
    light = np.clip((value - 18.0) / 92.0, 0.0, 1.0)
    alpha = np.power(chroma * light, 0.72)

    # Keep the treatment in the rear energy field, away from the pedestal.
    geometry = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(geometry)
    draw.rectangle((0, 420, WIDTH, HEIGHT), fill=0)
    draw.polygon(
        [(240, 33), (307, 124), (329, 270), (322, 388),
         (240, 431), (154, 385), (157, 174)],
        fill=0,
    )
    geometry = geometry.filter(ImageFilter.GaussianBlur(7.0))
    alpha *= np.asarray(geometry, dtype=np.float32) / 255.0

    # A soft blur prevents cut-out edges while retaining bright filament detail.
    mask = Image.fromarray(np.uint8(np.clip(alpha * 255.0, 0, 255)), "L")
    return mask.filter(ImageFilter.GaussianBlur(3.2))


def _bilinear_sample(rgb: np.ndarray, source_x: np.ndarray, source_y: np.ndarray) -> np.ndarray:
    """Sample an RGB frame through a small smooth displacement field."""
    source_x = np.clip(source_x, 0.0, WIDTH - 1.001)
    source_y = np.clip(source_y, 0.0, HEIGHT - 1.001)

    x0 = np.floor(source_x).astype(np.int32)
    y0 = np.floor(source_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, WIDTH - 1)
    y1 = np.minimum(y0 + 1, HEIGHT - 1)
    wx = (source_x - x0)[..., None]
    wy = (source_y - y0)[..., None]

    top = rgb[y0, x0] * (1.0 - wx) + rgb[y0, x1] * wx
    bottom = rgb[y1, x0] * (1.0 - wx) + rgb[y1, x1] * wx
    return top * (1.0 - wy) + bottom * wy


def _liquid_warp(frame: Image.Image, phase: float) -> Image.Image:
    """Gently undulate the rear ribbon texture around the crystal."""
    rgb = np.asarray(frame.convert("RGB"), dtype=np.float32)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    centre_x, centre_y = 240.0, 245.0
    radial_x = xx - centre_x
    radial_y = yy - centre_y
    radius = np.sqrt(radial_x * radial_x + radial_y * radial_y) + 1.0
    unit_x = radial_x / radius
    unit_y = radial_y / radius
    tangent_x = -unit_y
    tangent_y = unit_x
    angle = np.arctan2(radial_y, radial_x)

    # Two counterweighted eddies prevent the field from looking mechanically
    # rotated.  Every term has an integer temporal frequency for a clean loop.
    tangent_amount = (
        4.25 * np.sin(TAU * phase + radius * 0.020 + 0.60 * np.sin(angle * 2.0))
        + 1.45 * np.sin(TAU * 2.0 * phase - angle * 2.0 + radius * 0.008)
    )
    radial_amount = (
        1.45 * np.sin(TAU * phase - angle * 3.0 + radius * 0.017)
        + 0.55 * np.sin(TAU * 2.0 * phase + angle + radius * 0.031)
    )

    source_x = xx - tangent_x * tangent_amount - unit_x * radial_amount
    source_y = yy - tangent_y * tangent_amount - unit_y * radial_amount
    warped = _bilinear_sample(rgb, source_x, source_y)
    return Image.fromarray(np.uint8(np.clip(warped, 0, 255)), "RGB")


def _colour_current(warped: Image.Image, phase: float) -> Image.Image:
    """Send a restrained travelling hue and luminance wave through the ribbons."""
    hsv = np.asarray(warped.convert("HSV"), dtype=np.float32).copy()
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    dx = xx - 240.0
    dy = yy - 245.0
    radius = np.sqrt(dx * dx + dy * dy)
    angle = np.arctan2(dy, dx)

    travel = np.sin(TAU * phase - angle * 1.35 + radius * 0.028)
    secondary = np.sin(TAU * 2.0 * phase + angle * 2.10 + radius * 0.013)
    hsv[..., 0] = np.mod(hsv[..., 0] + 4.8 * travel + 1.8 * secondary, 256.0)
    hsv[..., 1] = np.clip(hsv[..., 1] + 5.0 + 3.2 * travel, 0.0, 255.0)
    hsv[..., 2] = np.clip(hsv[..., 2] + 3.8 * travel + 1.4 * secondary, 0.0, 255.0)
    return Image.fromarray(np.uint8(hsv), "HSV").convert("RGB")


def _bezier_point(points, t: float):
    p0, p1, p2, p3 = points
    omt = 1.0 - t
    x = omt ** 3 * p0[0] + 3 * omt * omt * t * p1[0] + 3 * omt * t * t * p2[0] + t ** 3 * p3[0]
    y = omt ** 3 * p0[1] + 3 * omt * omt * t * p1[1] + 3 * omt * t * t * p2[1] + t ** 3 * p3[1]
    return x, y


def _flowing_filaments(phase: float, ribbon_mask: Image.Image) -> Image.Image:
    """Add low-opacity caustic strands that travel along the existing curves."""
    paths = [
        (((376, -28), (286, 32), (333, 132), (252, 215)), 0.035, 3.7),
        (((494, 62), (420, 80), (384, 207), (278, 300)), 0.52, 3.1),
        (((532, 153), (426, 172), (377, 285), (282, 350)), 0.57, 3.5),
        (((535, 295), (427, 274), (366, 334), (278, 387)), 0.76, 3.0),
        (((-42, 255), (63, 224), (131, 297), (190, 359)), 0.50, 3.3),
        (((-38, 344), (66, 300), (132, 319), (193, 383)), 0.66, 3.8),
        (((-25, 441), (66, 340), (133, 347), (205, 401)), 0.84, 3.2),
    ]

    broad = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    sharp = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    broad_draw = ImageDraw.Draw(broad)
    sharp_draw = ImageDraw.Draw(sharp)

    samples = 180
    for path_index, (control, base_hue, cycles) in enumerate(paths):
        previous = _bezier_point(control, 0.0)
        for index in range(1, samples):
            t = index / (samples - 1)
            point = _bezier_point(control, t)
            # A travelling envelope creates visible direction while overlapping
            # cycles keep the filament from blinking on or off as a whole.
            wave = 0.5 + 0.5 * math.sin(TAU * (t * cycles - phase + path_index * 0.137))
            envelope = max(0.0, (wave - 0.42) / 0.58) ** 1.65
            if envelope > 0.015:
                hue = (base_hue + 0.10 * math.sin(TAU * (phase + t * 0.72))) % 1.0
                red, green, blue = (int(channel * 255) for channel in colorsys.hsv_to_rgb(hue, 0.72, 1.0))
                broad_draw.line((previous, point), fill=(red, green, blue, int(34 * envelope)), width=7)
                sharp_draw.line((previous, point), fill=(235, 249, 255, int(31 * envelope)), width=1)
            previous = point

    broad = broad.filter(ImageFilter.GaussianBlur(5.2))
    filaments = Image.alpha_composite(broad, sharp)
    alpha = ImageChops.multiply(filaments.getchannel("A"), ribbon_mask)
    filaments.putalpha(alpha)
    return filaments


def animate_ribbons(base: Image.Image, phase: float, ribbon_mask: Image.Image) -> Image.Image:
    warped = _liquid_warp(base, phase)
    shifted = _colour_current(warped, phase)
    # Preserve part of the original texture even inside the mask.  The movement
    # remains gentle and the wallpaper's fine strands do not turn mushy.
    moved = Image.blend(base.convert("RGB"), shifted, 0.78)
    frame = Image.composite(moved, base.convert("RGB"), ribbon_mask).convert("RGBA")
    return Image.alpha_composite(frame, _flowing_filaments(phase, ribbon_mask)).convert("RGB")


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

    with Image.open(input_dir / "0000.png") as first:
        ribbon_mask = _soft_ribbon_mask(first.convert("RGB"))
    ribbon_mask.save(output_dir / "_ribbon_mask.png")

    for frame_number in range(args.frames):
        source = input_dir / f"{frame_number:04d}.png"
        if not source.exists():
            raise SystemExit(f"Missing source frame: {source}")
        phase = frame_number / args.frames
        with Image.open(source) as image:
            animated_base = animate_ribbons(image.convert("RGB"), phase, ribbon_mask)
            rendered = render_frame(animated_base, phase, particles)
            rendered.save(output_dir / f"{frame_number:04d}.png", compress_level=3)


if __name__ == "__main__":
    main()
