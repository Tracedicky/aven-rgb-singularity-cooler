#!/usr/bin/env python3
"""Build the V14 Event Horizon cooler loop.

The frame is rendered procedurally in linear light at twice the delivery
resolution.  A tilted accretion disk orbits a black event horizon while a
lensed halo arcs over the top, and a spectral colour wave travels around the
ring so the whole RGB range is shown against a genuinely dark field.

Every animated quantity completes a whole number of cycles across the nine
second loop, so the last frame hands back to the first with no seam.
"""

from __future__ import annotations

import argparse
import base64
import math
import subprocess
from pathlib import Path

import numpy as np

SIZE = 480
SUPERSAMPLE = 2
RENDER = SIZE * SUPERSAMPLE
FPS = 30
DURATION = 9.0
FRAMES = int(round(FPS * DURATION))

SHADOW_RADIUS = 0.268
PHOTON_RADIUS = 0.281
DISK_INNER = 0.347
DISK_OUTER = 0.895
DISK_SQUASH = 0.320

# Only a slice of the colour wheel is on screen at any instant, and that slice
# drifts through a full turn across the loop.  Showing every hue at once is what
# makes RGB work look cheap; showing them in sequence keeps each frame designed.
HUE_SPREAD = 0.18

TEXTURE_RINGS = 320
TEXTURE_ANGLES = 1080
SHELL_CYCLES = (5, 3, 2, 1)
SHELL_CENTRES = (0.00, 0.34, 0.67, 1.00)
SHELL_WIDTH = 0.30

TAU = math.tau


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def _along(array: np.ndarray, axis: int, window: slice) -> np.ndarray:
    index: list[slice] = [slice(None)] * array.ndim
    index[axis] = window
    return array[tuple(index)]


def box_blur(array: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Average `array` over a sliding window using a cumulative sum."""
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
    upper = _along(running, axis, slice(span, span + length))
    lower = _along(running, axis, slice(0, length))
    return (upper - lower) / np.float32(span)


def blur(array: np.ndarray, sigma: float) -> np.ndarray:
    """Approximate a Gaussian blur with three box passes per axis."""
    radius = max(1, int(round(sigma / 1.4)))
    result = array
    for axis in (0, 1):
        for _ in range(3):
            result = box_blur(result, radius, axis)
    return result


def downsample(array: np.ndarray) -> np.ndarray:
    height, width = array.shape[:2]
    reshaped = array.reshape(height // 2, 2, width // 2, 2, -1)
    return reshaped.mean(axis=(1, 3)).reshape(height // 2, width // 2, -1)


def upsample(array: np.ndarray, factor: int) -> np.ndarray:
    return np.repeat(np.repeat(array, factor, axis=0), factor, axis=1)


def smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def hsv_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    """Vectorised HSV conversion; `hue` may lie outside [0, 1)."""
    hue = np.mod(hue, 1.0) * 6.0
    sector = np.floor(hue).astype(np.int32)
    fraction = hue - sector

    p = value * (1.0 - saturation)
    q = value * (1.0 - saturation * fraction)
    t = value * (1.0 - saturation * (1.0 - fraction))

    sector = sector % 6
    red = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
        [value, q, p, p, t],
        default=value,
    )
    green = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
        [t, value, value, q, p],
        default=p,
    )
    blue = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4],
        [p, p, t, value, value],
        default=q,
    )
    return np.stack([red, green, blue], axis=-1).astype(np.float32)


def aces_tonemap(colour: np.ndarray) -> np.ndarray:
    """Narkowicz's ACES fit — keeps saturated highlights from clipping flat."""
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip((colour * (a * colour + b)) / (colour * (c * colour + d) + e), 0.0, 1.0)


def linear_to_srgb(colour: np.ndarray) -> np.ndarray:
    low = colour * 12.92
    high = 1.055 * np.power(np.maximum(colour, 1e-6), 1.0 / 2.4) - 0.055
    return np.where(colour <= 0.0031308, low, high)


# ---------------------------------------------------------------------------
# procedural textures
# ---------------------------------------------------------------------------


def filament_texture(seed: int, angular_stretch: float, detail: float) -> np.ndarray:
    """A periodic polar noise field whose structure is drawn out along phi."""
    rng = np.random.default_rng(seed)
    white = rng.normal(size=(TEXTURE_RINGS, TEXTURE_ANGLES))

    ring_freq = np.fft.fftfreq(TEXTURE_RINGS)[:, None] * TEXTURE_RINGS
    angle_freq = np.fft.fftfreq(TEXTURE_ANGLES)[None, :] * TEXTURE_ANGLES

    # Angular frequency is penalised so structure is drawn out into long streaks
    # that follow the orbit, while radial detail stays crisp as banded lanes.
    radial = ring_freq / detail
    angular = angle_freq * angular_stretch / detail
    magnitude = np.sqrt(radial**2 + angular**2)
    envelope = 1.0 / (1.0 + magnitude**2.4)

    field = np.fft.ifft2(np.fft.fft2(white) * envelope).real
    field -= field.mean()
    field /= field.std() + 1e-6
    return field.astype(np.float32)


def shell_weights(normalised_radius: np.ndarray) -> list[np.ndarray]:
    """Blend windows that give inner shells a faster orbit than outer ones."""
    raw = [
        np.exp(-(((normalised_radius - centre) / SHELL_WIDTH) ** 2)).astype(np.float32)
        for centre in SHELL_CENTRES
    ]
    total = np.add.reduce(raw) + 1e-6
    return [layer / total for layer in raw]


# ---------------------------------------------------------------------------
# scene geometry, computed once
# ---------------------------------------------------------------------------


class Geometry:
    def __init__(self) -> None:
        axis = (np.arange(RENDER, dtype=np.float32) + 0.5) / RENDER * 2.0 - 1.0
        self.u = np.tile(axis[None, :], (RENDER, 1))
        self.v = np.tile(-axis[:, None], (1, RENDER))

        self.screen_radius = np.hypot(self.u, self.v)
        self.screen_angle = np.arctan2(self.v, self.u)

        # The disk lies in a plane tilted away from the viewer, so its circular
        # orbits project to ellipses squashed along screen y.
        self.disk_radius = np.hypot(self.u, self.v / DISK_SQUASH)
        self.disk_angle = np.arctan2(self.v / DISK_SQUASH, self.u)

        self.disk_span = np.clip(
            (self.disk_radius - DISK_INNER) / (DISK_OUTER - DISK_INNER), 0.0, 1.0
        )
        self.disk_body = smoothstep(DISK_INNER, DISK_INNER + 0.040, self.disk_radius) * (
            1.0 - smoothstep(DISK_OUTER - 0.24, DISK_OUTER, self.disk_radius)
        )

        self.halo_span = np.clip(
            (self.screen_radius - PHOTON_RADIUS) / (0.492 - PHOTON_RADIUS), 0.0, 1.0
        )
        self.halo_body = smoothstep(PHOTON_RADIUS, PHOTON_RADIUS + 0.012, self.screen_radius) * (
            1.0 - smoothstep(0.324, 0.492, self.screen_radius)
        )

        pixel = 2.0 / RENDER
        self.shadow = 1.0 - smoothstep(
            SHADOW_RADIUS - 1.5 * pixel, SHADOW_RADIUS + 1.5 * pixel, self.screen_radius
        )
        self.visible = (1.0 - self.shadow).astype(np.float32)

        # A thin, intense ring of light hugging the horizon.
        self.photon_ring = np.exp(
            -(((self.screen_radius - PHOTON_RADIUS) / 0.0045) ** 2)
        ).astype(np.float32)

        self.far_side = smoothstep(-0.12, 0.12, np.sin(self.disk_angle)).astype(np.float32)
        self.near_side = (1.0 - self.far_side).astype(np.float32)

        self.disk_shells = shell_weights(self.disk_span)
        self.halo_shells = shell_weights(self.halo_span)

        ring_index = np.clip((self.disk_span * (TEXTURE_RINGS - 1)).astype(np.int32), 0, TEXTURE_RINGS - 1)
        angle_index = np.clip(
            ((self.disk_angle / TAU) % 1.0 * TEXTURE_ANGLES).astype(np.int32), 0, TEXTURE_ANGLES - 1
        )
        self.disk_lookup = (ring_index * TEXTURE_ANGLES + angle_index).ravel()

        halo_ring = np.clip((self.halo_span * (TEXTURE_RINGS - 1)).astype(np.int32), 0, TEXTURE_RINGS - 1)
        halo_angle = np.clip(
            ((self.screen_angle / TAU) % 1.0 * TEXTURE_ANGLES).astype(np.int32), 0, TEXTURE_ANGLES - 1
        )
        self.halo_lookup = (halo_ring * TEXTURE_ANGLES + halo_angle).ravel()

        # Corners fall away so the frame sits comfortably inside a round LCD.
        self.vignette = (
            (1.0 - 0.86 * smoothstep(0.66, 1.36, self.screen_radius)).astype(np.float32)
        )

        self.chromatic = [self._radial_sampler(scale) for scale in (1.0035, 1.0, 0.9965)]

    def _radial_sampler(self, scale: float):
        """Bilinear gather that resamples the frame about its centre."""
        centre = (RENDER - 1) / 2.0
        rows = np.arange(RENDER, dtype=np.float32)[:, None]
        cols = np.arange(RENDER, dtype=np.float32)[None, :]

        source_y = np.clip(centre + (rows - centre) / scale, 0, RENDER - 1.001)
        source_x = np.clip(centre + (cols - centre) / scale, 0, RENDER - 1.001)

        y0 = source_y.astype(np.int32)
        x0 = source_x.astype(np.int32)
        fy = (source_y - y0).astype(np.float32)
        fx = (source_x - x0).astype(np.float32)

        y0 = np.broadcast_to(y0, (RENDER, RENDER))
        x0 = np.broadcast_to(x0, (RENDER, RENDER))
        fy = np.broadcast_to(fy, (RENDER, RENDER))
        fx = np.broadcast_to(fx, (RENDER, RENDER))

        flat = (y0 * RENDER + x0).ravel()
        return (
            flat,
            flat + 1,
            flat + RENDER,
            flat + RENDER + 1,
            ((1 - fy) * (1 - fx)).ravel(),
            ((1 - fy) * fx).ravel(),
            (fy * (1 - fx)).ravel(),
            (fy * fx).ravel(),
        )


# ---------------------------------------------------------------------------
# starfield and nebula
# ---------------------------------------------------------------------------


class Backdrop:
    def __init__(self, geometry: Geometry) -> None:
        rng = np.random.default_rng(4471)
        count = 760

        self.x = rng.integers(2, RENDER - 3, size=count)
        self.y = rng.integers(2, RENDER - 3, size=count)
        brightness = rng.random(count) ** 3.2
        self.brightness = (0.04 + 1.45 * brightness).astype(np.float32)
        self.cycles = rng.integers(1, 5, size=count).astype(np.float32)
        self.phase = rng.random(count).astype(np.float32)

        warmth = rng.random(count).astype(np.float32)
        self.tint = np.stack(
            [0.94 + 0.07 * warmth, 0.96 + 0.03 * warmth, 1.02 - 0.04 * warmth], axis=-1
        ).astype(np.float32)

        # Stars sit far enough out that the disk glow does not swallow them.
        keep = geometry.screen_radius[self.y, self.x] > 0.34
        self.x, self.y = self.x[keep], self.y[keep]
        self.brightness = self.brightness[keep]
        self.cycles, self.phase = self.cycles[keep], self.phase[keep]
        self.tint = self.tint[keep]

        self.orbit_r = rng.uniform(0.48, 0.84, size=260).astype(np.float32)
        self.orbit_theta = rng.random(260).astype(np.float32)
        self.orbit_spin = rng.uniform(0.8, 2.4, size=260).astype(np.float32)
        self.particle_hue = rng.random(260).astype(np.float32)
        self.particle_size = rng.uniform(1.4, 4.4, size=260).astype(np.float32)

        coarse = RENDER // 16
        cloud = rng.normal(size=(coarse, coarse, 3)).astype(np.float32)
        cloud = blur(cloud, 5.0)
        cloud -= cloud.min()
        cloud /= cloud.max() + 1e-6
        self.cloud = cloud * np.array([0.28, 0.56, 1.0], dtype=np.float32)

        self.falloff = (
            0.16 + 0.84 * np.clip(1.0 - geometry.screen_radius / 1.5, 0.0, 1.0) ** 2
        ).astype(np.float32)

    def render(self, phase: float) -> np.ndarray:
        drift = int(round(phase * self.cloud.shape[0])) % self.cloud.shape[0]
        cloud = np.roll(self.cloud, drift, axis=1)
        nebula = upsample(cloud, 16) * 0.040 * self.falloff[..., None]

        twinkle = 0.72 + 0.28 * np.sin(TAU * (self.cycles * phase + self.phase))
        flare = (self.brightness * twinkle)[:, None] * self.tint

        stars = np.zeros((RENDER, RENDER, 3), dtype=np.float32)
        np.add.at(stars, (self.y, self.x), flare)
        np.add.at(stars, (self.y + 1, self.x), flare * 0.34)
        np.add.at(stars, (self.y - 1, self.x), flare * 0.34)
        np.add.at(stars, (self.y, self.x + 1), flare * 0.34)
        np.add.at(stars, (self.y, self.x - 1), flare * 0.34)

        dust = np.zeros((RENDER, RENDER, 3), dtype=np.float32)
        centre = (RENDER - 1) / 2.0
        for radius, theta, spin, hue, size in zip(
            self.orbit_r, self.orbit_theta, self.orbit_spin, self.particle_hue, self.particle_size
        ):
            angle = TAU * (phase * spin + theta)
            px = np.clip((centre + np.cos(angle) * radius * centre * 1.08).astype(np.int32), 1, RENDER - 2)
            py = np.clip((centre - np.sin(angle) * radius * centre * DISK_SQUASH * 1.08).astype(np.int32), 1, RENDER - 2)
            colour = hsv_to_rgb(hue, np.array(0.68, dtype=np.float32), np.array(0.85, dtype=np.float32))
            dust[py, px] += colour * (0.40 + 0.70 * size / self.particle_size.max())
            dust[py + 1, px] += colour * 0.32
            dust[py - 1, px] += colour * 0.32
            dust[py, px + 1] += colour * 0.32
            dust[py, px - 1] += colour * 0.32

        return nebula + stars + dust


# ---------------------------------------------------------------------------
# orbiting sparks
# ---------------------------------------------------------------------------


class Sparks:
    def __init__(self) -> None:
        rng = np.random.default_rng(9130)
        count = 420
        self.radius = (DISK_INNER + 0.03 + rng.random(count) ** 1.5 * (DISK_OUTER - DISK_INNER - 0.06)).astype(np.float32)
        self.orbits = rng.choice([1, 2, 3, 4, 5, 6, 7], size=count, p=[0.06, 0.16, 0.22, 0.20, 0.18, 0.12, 0.06]).astype(np.float32)
        self.phase = rng.random(count).astype(np.float32)
        self.lift = (rng.normal(scale=0.014, size=count)).astype(np.float32)
        self.energy = (0.62 + rng.random(count) ** 2 * 2.0).astype(np.float32)
        self.hue_offset = rng.normal(scale=0.06, size=count).astype(np.float32)
        self.size = (rng.uniform(1.2, 3.8, size=count)).astype(np.float32)

    def render(self, phase: float, base_hue: float) -> np.ndarray:
        layer = np.zeros((RENDER, RENDER, 3), dtype=np.float32)
        centre = (RENDER - 1) / 2.0

        for step, weight in ((0.0, 1.0), (-0.006, 0.48), (-0.012, 0.20), (0.010, 0.26)):
            angle = TAU * (self.orbits * (phase + step) + self.phase)
            u = self.radius * np.cos(angle)
            v = self.radius * np.sin(angle) * DISK_SQUASH + self.lift

            visible = ~((np.hypot(u, v) < SHADOW_RADIUS) & (np.sin(angle) > 0))

            hue = base_hue + HUE_SPREAD * np.sin(angle) + self.hue_offset
            depth = 0.60 + 0.40 * (1.0 - np.sin(angle))
            colour = hsv_to_rgb(hue, np.full_like(hue, 0.55), self.energy * depth * weight)

            px = np.clip((centre + u * centre).astype(np.int32), 1, RENDER - 2)
            py = np.clip((centre - v * centre).astype(np.int32), 1, RENDER - 2)
            px, py, colour = px[visible], py[visible], colour[visible]

            size_scale = (1.0 + 0.5 * self.size[visible])[:, None]
            np.add.at(layer, (py, px), colour * size_scale)
            np.add.at(layer, (py + 1, px), colour * 0.32)
            np.add.at(layer, (py - 1, px), colour * 0.32)
            np.add.at(layer, (py, px + 1), colour * 0.32)
            np.add.at(layer, (py, px - 1), colour * 0.32)
            np.add.at(layer, (py + 2, px), colour * 0.15)
            np.add.at(layer, (py - 2, px), colour * 0.15)
            np.add.at(layer, (py, px + 2), colour * 0.15)
            np.add.at(layer, (py, px - 2), colour * 0.15)

        return layer


# ---------------------------------------------------------------------------
# the frame itself
# ---------------------------------------------------------------------------


class Scene:
    def __init__(self) -> None:
        self.geometry = Geometry()
        self.backdrop = Backdrop(self.geometry)
        self.sparks = Sparks()

        self.disk_textures = [
            filament_texture(seed=1200 + index * 37, angular_stretch=6.0, detail=14.0).ravel()
            for index in range(len(SHELL_CYCLES))
        ]
        self.halo_textures = [
            filament_texture(seed=8800 + index * 53, angular_stretch=5.0, detail=10.0).ravel()
            for index in range(len(SHELL_CYCLES))
        ]
        self.shimmer = filament_texture(seed=555, angular_stretch=3.0, detail=30.0).ravel()

        self.step = TEXTURE_ANGLES // FRAMES

    def _turbulence(self, frame: int, textures: list[np.ndarray], weights: list[np.ndarray], lookup: np.ndarray) -> np.ndarray:
        total = np.zeros(lookup.shape, dtype=np.float32)
        for cycles, texture, weight in zip(SHELL_CYCLES, textures, weights):
            offset = (cycles * self.step * frame) % TEXTURE_ANGLES
            rolled = np.roll(texture.reshape(TEXTURE_RINGS, TEXTURE_ANGLES), offset, axis=1).ravel()
            total += weight.ravel() * rolled[lookup]
        return total.reshape(RENDER, RENDER)

    def render(self, frame: int) -> np.ndarray:
        geometry = self.geometry
        phase = frame / FRAMES
        base_hue = phase

        image = self.backdrop.render(phase) * geometry.visible[..., None]

        # --- accretion disk -------------------------------------------------
        turbulence = self._turbulence(frame, self.disk_textures, geometry.disk_shells, geometry.disk_lookup)
        sparkle_offset = (7 * self.step * frame) % TEXTURE_ANGLES
        sparkle = np.roll(
            self.shimmer.reshape(TEXTURE_RINGS, TEXTURE_ANGLES), sparkle_offset, axis=1
        ).ravel()[geometry.disk_lookup].reshape(RENDER, RENDER)

        density = np.exp(1.05 * turbulence - 0.55) * (1.0 + 0.12 * sparkle)
        radial_falloff = np.power(DISK_INNER / np.maximum(geometry.disk_radius, DISK_INNER), 1.5)

        # Relativistic beaming: the side sweeping toward the viewer is brighter.
        beaming = np.power(1.0 + 0.42 * np.cos(geometry.disk_angle), 1.5)

        disk_intensity = geometry.disk_body * radial_falloff * density * beaming * 0.85

        hue = base_hue + HUE_SPREAD * np.sin(geometry.disk_angle) + 0.05 * geometry.disk_span
        saturation = 0.70 + 0.25 * smoothstep(0.0, 0.42, geometry.disk_span)
        disk_colour = hsv_to_rgb(hue, saturation, disk_intensity)

        image += disk_colour * geometry.near_side[..., None]
        image += disk_colour * (geometry.far_side * geometry.visible)[..., None]

        # --- lensed halo arcing over the horizon -----------------------------
        halo_turbulence = self._turbulence(frame, self.halo_textures, geometry.halo_shells, geometry.halo_lookup)
        halo_density = np.exp(0.95 * halo_turbulence - 0.45)
        vertical_bias = 0.34 + 0.66 * np.abs(np.sin(geometry.screen_angle))
        halo_intensity = geometry.halo_body * halo_density * vertical_bias * 1.00

        halo_hue = base_hue + HUE_SPREAD * np.sin(geometry.screen_angle) + 0.04
        image += hsv_to_rgb(halo_hue, np.full_like(halo_hue, 0.72), halo_intensity)

        # --- photon ring ------------------------------------------------------
        ring_hue = base_hue + HUE_SPREAD * np.sin(geometry.screen_angle)
        ring = geometry.photon_ring * (1.0 + 0.55 * np.sin(TAU * (2 * phase + geometry.screen_angle / TAU)))
        image += hsv_to_rgb(ring_hue, np.full_like(ring_hue, 0.28), ring * 3.0)

        # Extra energy surge to give the event horizon more life and a stronger
        # "live cooler" feel without washing out the black core.
        surge = np.exp(-(((geometry.screen_radius - 0.43) / 0.05) ** 2)) * (
            0.58 + 0.42 * np.sin(TAU * (phase * 2.4 + geometry.screen_angle * 2.0))
        )
        image += hsv_to_rgb(base_hue + 0.12, np.full_like(surge, 0.70), surge * 1.75)
        image *= (1.0 - geometry.shadow * 0.985)[..., None]

        # --- sparks -----------------------------------------------------------
        image += self.sparks.render(phase, base_hue)

        return image


# ---------------------------------------------------------------------------
# post processing
# ---------------------------------------------------------------------------


def bloom(image: np.ndarray) -> np.ndarray:
    source = np.maximum(image - 0.80, 0.0)
    accumulated = np.zeros_like(image)
    current = source
    for index, weight in enumerate((0.20, 0.16, 0.12, 0.085, 0.055)):
        current = blur(downsample(current), 1.7)
        accumulated += weight * upsample(current, 2 ** (index + 1))
    return accumulated


def anamorphic(image: np.ndarray) -> np.ndarray:
    source = np.maximum(image - 2.4, 0.0)
    streak = box_blur(source, 70, axis=1)
    streak = box_blur(streak, 70, axis=1)
    streak = box_blur(streak, 2, axis=0)
    return streak * np.array([0.42, 0.62, 1.15], dtype=np.float32) * 0.55


def chromatic_aberration(image: np.ndarray, geometry: Geometry) -> np.ndarray:
    result = np.empty_like(image)
    for channel, sampler in enumerate(geometry.chromatic):
        i00, i01, i10, i11, w00, w01, w10, w11 = sampler
        flat = image[..., channel].ravel()
        blended = flat[i00] * w00 + flat[i01] * w01 + flat[i10] * w10 + flat[i11] * w11
        result[..., channel] = blended.reshape(RENDER, RENDER)
    return result


def finish(image: np.ndarray, geometry: Geometry, frame: int) -> np.ndarray:
    image = image + bloom(image) + anamorphic(image)
    # Glow must not creep back over the horizon — the void stays void.
    image *= (1.0 - geometry.shadow * 0.94)[..., None]
    image = chromatic_aberration(image, geometry)
    image *= geometry.vignette[..., None]

    image = aces_tonemap(image * 0.80)
    image = linear_to_srgb(image)

    # Keep the loop seamless: grain must be phase-locked, not seeded by the
    # absolute frame index. Using a fixed RNG seed makes the final frame match
    # the first frame when the animation wraps around.
    rng = np.random.default_rng(20_000)
    phase = (frame / FRAMES) * TAU
    luminance = image.mean(axis=-1, keepdims=True)
    grain_weight = 0.016 * (1.0 - np.abs(luminance * 2.0 - 1.0))

    # A deterministic, low-frequency noise field tied to the current phase keeps
    # the wrap continuous while still giving the image some fine texture.
    y = np.arange(RENDER, dtype=np.float32)[:, None]
    x = np.arange(RENDER, dtype=np.float32)[None, :]
    noise = (
        np.sin((x + 1.73 * y + phase) * 0.27)
        + np.cos((x * 0.63 - y * 0.91 + phase * 2.1) * 0.41)
        + np.sin((x * 0.19 + y * 0.73 - phase * 1.7) * 0.94)
    )
    noise = np.broadcast_to(noise[..., None], image.shape).astype(np.float32)
    image += (rng.normal(size=image.shape).astype(np.float32) * 0.75 + noise * 0.25) * grain_weight
    image += rng.uniform(-0.5 / 255.0, 0.5 / 255.0, size=image.shape).astype(np.float32)

    image = np.clip(image, 0.0, 1.0)
    small = image.reshape(SIZE, SUPERSAMPLE, SIZE, SUPERSAMPLE, 3).mean(axis=(1, 3))
    return (small * 255.0 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------


def encode(frames_iter, destination: Path, ffmpeg: str) -> None:
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SIZE}x{SIZE}", "-r", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libvpx", "-pix_fmt", "yuv420p",
        "-b:v", "2600k", "-crf", "8", "-qmin", "0", "-qmax", "26",
        "-g", str(FRAMES), "-auto-alt-ref", "0", "-lag-in-frames", "0",
        "-deadline", "good", "-cpu-used", "0",
        str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames_iter:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the loop")


def write_payload(video: Path, folder: Path, chunk: int = 1_400_000) -> int:
    payload = base64.b64encode(video.read_bytes()).decode("ascii")
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob("video-v14-part-*.js"):
        existing.unlink()

    parts = [payload[index : index + chunk] for index in range(0, len(payload), chunk)]
    for number, part in enumerate(parts, start=1):
        target = folder / f"video-v14-part-{number:02d}.js"
        target.write_text(f'window.__avenPrismaticParts.push("{part}");\n', encoding="utf-8")
    return len(parts)


def write_page(root: Path, part_count: int) -> None:
    scripts = "\n".join(
        f'  <script src="singularity-v14/video-v14-part-{number:02d}.js"></script>'
        for number in range(1, part_count + 1)
    )
    root.joinpath("index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
  <meta name="theme-color" content="#000000">
  <title>Aven Prismatic Core V14 — Event Horizon</title>
  <style>
    html,body{{width:100%;height:100%;margin:0;overflow:hidden;background:#000}}
    body{{display:grid;place-items:center}}
    video{{display:block;width:100vw;height:100vh;object-fit:cover;background:#000}}
  </style>
  <script>window.__avenPrismaticParts=[];</script>
{scripts}
</head>
<body>
  <video id="prismatic" autoplay muted loop playsinline preload="auto" disablepictureinpicture></video>
  <script>
    (function(){{
      var video=document.getElementById("prismatic"), payload=window.__avenPrismaticParts.join("");
      window.__avenPrismaticParts.length=0; video.muted=true; video.defaultMuted=true; video.setAttribute("muted","");
      function start(){{if(!video.paused)return;var p=video.play();if(p&&p.catch)p.catch(function(){{}});}}
      video.addEventListener("loadeddata",start); video.addEventListener("canplay",start);
      video.addEventListener("stalled",start); window.addEventListener("pageshow",start);
      document.addEventListener("visibilitychange",function(){{if(!document.hidden)start();}});
      video.src="data:video/webm;base64,"+payload; video.load(); start(); window.setInterval(start,3000);
    }}());
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the V14 Event Horizon loop.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent / "v14")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--preview", type=Path, help="write sample PNG frames here instead of encoding")
    parser.add_argument("--preview-frames", type=int, nargs="*", default=[0, 45, 90, 180])
    args = parser.parse_args()

    scene = Scene()

    if args.preview:
        from PIL import Image

        args.preview.mkdir(parents=True, exist_ok=True)
        for frame in args.preview_frames:
            pixels = finish(scene.render(frame), scene.geometry, frame)
            Image.fromarray(pixels).save(args.preview / f"v14_{frame:03d}.png")
            print(f"preview frame {frame}")
        return

    def frames():
        for frame in range(FRAMES):
            if frame % 30 == 0:
                print(f"  frame {frame}/{FRAMES}", flush=True)
            yield finish(scene.render(frame), scene.geometry, frame)

    args.root.mkdir(parents=True, exist_ok=True)
    video = args.root / "aven-v14-event-horizon.webm"
    encode(frames(), video, args.ffmpeg)

    part_count = write_payload(video, args.root / "singularity-v14")
    write_page(args.root, part_count)
    size_mb = video.stat().st_size / 1_048_576
    video.unlink()
    print(f"encoded {FRAMES} frames, {size_mb:.2f} MB across {part_count} payload parts")


if __name__ == "__main__":
    main()
