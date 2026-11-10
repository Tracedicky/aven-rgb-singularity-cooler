"""AVENGIS Rebounded: THE ENDLESS RUN, a seamless 30-second 480x480 VP8/WebM for the iCUE LCD.

Down an endless tunnel walled with all six worlds' living bricks, turning as it goes. Water rides along its floor,
rising and falling as it does in the game, and on it the crew's deck keeps a ball in play: every return breaks a brick
out of the wall and lets the world outside show through. Two more balls work further down the tunnel, so the holes
they knock come rushing past. Each ball lights the bricks around it, everything mirrors in the water, and as each
world's stretch of wall reaches the deck its hero takes over and its name shows.

Everything is a function of time modulo the loop: the tunnel travels exactly its own length (six worlds of twelve
rows) and turns exactly once in the thirty seconds, every hit sits on a fixed beat, a brick is broken once its row has
passed the depth it was hit at, and every animation runs a whole number of cycles. Frame 900 is frame 0.

    py tools/build_rebounded_tunnel.py <Rebounded checkout> --ffmpeg <ffmpeg>                encode into rebounded-tunnel/
    py tools/build_rebounded_tunnel.py <Rebounded checkout> --preview DIR [--preview-frames]  sample PNG frames
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rebounded_cooler import write_payload  # noqa: E402  (the same payload as the first loop)

SIZE = 480
MID = SIZE / 2
FPS = 30
DURATION = 30.0
FRAMES = int(FPS * DURATION)
TAU = math.tau

COLS = 32  # bricks round the tunnel
ROWS = 12  # rows a world
WORLDS_N = 6
N = ROWS * WORLDS_N  # the tunnel's length, in rows: travelled once a loop
SPEED = N / DURATION  # rows a second
R_PLAY = 150.0  # the screen radius of the tunnel where the deck and its ball play
K = COLS * R_PLAY / math.pi  # depth (rows) = K / screen radius: bricks are 2:1 at R_PLAY
V_PLAY = K / R_PLAY
F_OVER_K = 240.0 / K  # a row's depth in tunnel radii (a 90 degree view)
INK = (5, 10, 18)
FLASH = 0.1  # a hit brick lights up this long before it goes
BITRATE = '2400k'  # the page's VP8: as sharp as keeps the page a sensible size

HEROES = [  # (world, scene, dusk, brick sheet, deck sheet + column, ball sheet + column, colour)
    ('COPPERCLOUD', 'coppercloud-harbour', 0, 'coppercloud/coppercloud-bricks-032', ('decks-012', 0), ('aether-ball', 0), (82, 200, 190)),
    ('GLASSROOT', 'glassroot-canopy', 0, 'glassroot/glassroot-bricks-032', ('decks-012', 1), ('dewdrop', 0), (138, 219, 137)),
    ('EMBERLINE', 'emberline-smokestacks', 1, 'emberline/emberline-bricks-032', ('decks-012', 2), ('spark', 0), (235, 169, 96)),
    ('TIDEGLASS', 'tideglass-bay', 0, 'tideglass/tideglass-bricks-032', ('decks-012', 3), ('pearl', 0), (124, 188, 235)),
    ('STORMGLASS', 'stormglass-peaks', 1, 'stormglass/storm-bricks-032', ('decks-012', 4), ('glint', 0), (188, 147, 219)),
    ('CLOCKWORK', 'clockwork-heart', 0, 'clockwork/clockwork-bricks-032', ('world-decks-021', 16), ('world-balls-021', 8), (244, 208, 111)),
]
WATER = ((14, 60, 120), (30, 104, 176), (64, 156, 220), (140, 210, 246), (230, 250, 255))  # deep .. foam (deckkit)

MAIN_HITS = 36  # the deck's ball: a brick a beat
FAR = [(96.0, 26, 11), (64.0, 22, 23)]  # (screen radius, hits a loop, seed): the balls further down


def whole(seconds_per_cycle: float) -> int:
    return max(1, round(DURATION / seconds_per_cycle))


def anim(t: float, frames: int, seconds_per_frame: float) -> int:
    return int((t / DURATION) * whole(frames * seconds_per_frame) * frames) % frames


def wave(t: float, k: int, phase: float = 0.0) -> float:
    return math.sin(TAU * k * t / DURATION + phase)


# --- the tunnel's motion, all periodic ---


def twist(t: float) -> float:
    """The tunnel's turn, in columns: once round a loop, with a slow sway on top."""
    return COLS * t / DURATION + 0.9 * wave(t, 2)


def water_h(t: float) -> float:
    """The water's surface, in tunnel radii below its axis: rising and falling twice a loop."""
    return 0.56 - 0.16 * wave(t, 2, 0.6)


def centres(t: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The tunnel's mouth and its far end on screen: the far end wanders, so the tunnel bends."""
    near = (MID + 5 * wave(t, 1), MID + 4 * wave(t, 2, 1.3))
    far = (MID + 30 * wave(t, 2, 0.5), MID + 20 * wave(t, 3, 2.0))
    return near, far


def bend(r):
    return np.clip((300.0 - r) / 300.0, 0.0, 1.0) ** 2


def centre_at(t: float, r: float) -> tuple[float, float]:
    near, far = centres(t)
    w = float(bend(np.float64(r)))
    return near[0] + (far[0] - near[0]) * w, near[1] + (far[1] - near[1]) * w


# ---------------------------------------------------------------------------
# the art
# ---------------------------------------------------------------------------


class Art:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.sheets = [Image.open(repo / 'assets/worlds' / (h[3] + '.png')).convert('RGBA') for h in HEROES]
        self.decks = {}
        self.balls = {}
        for h in HEROES:
            name, col = h[4]
            self.decks.setdefault(name, Image.open(repo / 'assets/crew' / (name + '.png')).convert('RGBA'))
            name, col = h[5]
            self.balls.setdefault(name, Image.open(repo / 'assets/crew' / (name + '.png')).convert('RGBA'))
        self.font = ImageFont.truetype(str(repo / 'assets/fonts/PressStart2P.ttf'), 8)
        self.average = [[self._average(s.crop((m * 16, 0, m * 16 + 16, 8))) for m in range(9)] for s in self.sheets]
        self.scenes = [self._scene(h[1]) for h in HEROES]
        # each world's wall: its body brick (nearest its hero's colour), a spiral stripe and a scatter of a third
        self.motifs = []
        for w, h in enumerate(HEROES):
            order = sorted(range(9), key=lambda m: sum((a - b) ** 2 for a, b in zip(self.average[w][m], h[6])))
            self.motifs.append(order[:3])
        self.layout = np.zeros((N, COLS), np.int32)  # (row, column) -> motif
        for row in range(N):
            w = row // ROWS
            m0, m1, m2 = self.motifs[w]
            for c in range(COLS):
                m = m0
                if (c + row) % 8 in (0, 1):
                    m = m1
                elif (c * 5 + row * 3) % 13 == 0:
                    m = m2
                self.layout[row, c] = m
        self.mortar = [tuple(int(v * 0.22) for v in self.average[w][self.motifs[w][0]]) for w in range(WORLDS_N)]

    @staticmethod
    def _average(img: Image.Image) -> tuple[int, int, int]:
        px = [p for p in img.get_flattened_data() if p[3] > 128]
        return tuple(sum(p[i] for p in px) // len(px) for i in range(3))

    def _scene(self, name: str):
        data = json.loads((self.repo / 'assets/backdrops' / (name + '.json')).read_text(encoding='utf-8'))
        sheet = Image.open(self.repo / 'assets/backdrops' / (name + '.png')).convert('RGBA')
        tags = {t['name']: (t['from'], t['to']) for t in data['meta'].get('frameTags', [])}
        day, dusk = tags.get('day', (0, 0)), tags.get('dusk', (1, 1))
        frames = {}
        for f in data['frames']:
            layer, index = f['filename'].split('|')
            r, at = f['frame'], f.get('spriteSourceSize', {'x': 0, 'y': 0})
            img = sheet.crop((r['x'], r['y'], r['x'] + r['w'], r['y'] + r['h'])) if r['w'] and r['h'] else None
            frames.setdefault(layer, {})[int(index)] = (img, at['x'], at['y'], f.get('sourceSize', r)['w'])
        out = []
        for layer in data['meta']['layers']:
            fields = dict(p.split('=') for p in (layer.get('data') or '').split(';') if '=' in p)
            fps = float(fields.get('fps', 0))
            mine = frames[layer['name']]
            width = next(iter(mine.values()))[3]
            wrap = int(fields.get('wrap', 0)) or width
            runs = []
            for first, last in (day, dusk):
                run = range(first, last + 1) if fps else (first,)
                runs.append([mine.get(i, mine[first])[:3] for i in run])
            drift = float(fields.get('drift', 0))
            if drift:  # a whole number of wraps a loop, so it closes
                drift = math.copysign(max(1, round(abs(drift) * DURATION / wrap)) * wrap / DURATION, drift)
            out.append((drift, fps, wrap, runs))
        return out

    def scene(self, w: int, t: float) -> np.ndarray:
        """World w's scene at time t, 384 x 216, as an array."""
        dusk = HEROES[w][2]
        frame = Image.new('RGBA', (384, 216), (0, 0, 0, 255))
        for drift, fps, wrap, runs in self.scenes[w]:
            run = runs[dusk]
            if fps and len(run) > 1:
                cycles = max(1, round(DURATION * fps / len(run)))
                img, ox, oy = run[int(t / DURATION * cycles * len(run)) % len(run)]
            else:
                img, ox, oy = run[0]
            if img is None:
                continue
            start = -(round(t * drift) % wrap)
            while start + ox < 384:
                if start + ox + img.width > 0:
                    frame.alpha_composite(img, (max(0, start + ox), oy), (max(0, -(start + ox)), 0))
                start += wrap
        return np.asarray(frame.convert('RGB'), np.float32)

    def texture(self, t: float) -> list[np.ndarray]:
        """The whole tunnel wall at time t (its bricks animating), 576 x 512, and three smaller copies of it."""
        f = anim(t, 24, 0.1)
        cells = [np.asarray(s.crop((0, f * 8, 144, f * 8 + 8)), np.float32) for s in self.sheets]  # (8, 144, 4)
        tex = np.zeros((N * 8, COLS * 16, 3), np.float32)
        for row in range(N):
            w = row // ROWS
            strip = cells[w]
            mortar = np.array(self.mortar[w], np.float32)
            for c in range(COLS):
                m = self.layout[row, c]
                brick = strip[:, m * 16 : m * 16 + 16]
                a = brick[..., 3:4] / 255.0
                tex[row * 8 : row * 8 + 8, c * 16 : c * 16 + 16] = brick[..., :3] * a + mortar * (1 - a)
        levels = [tex]
        for _ in range(3):
            p = levels[-1]
            levels.append((p[0::2, 0::2] + p[1::2, 0::2] + p[0::2, 1::2] + p[1::2, 1::2]) / 4)
        return levels

    def sprite(self, sheet: str, col: int, width: int, height: int, frames: int, spf: float, t: float) -> Image.Image:
        img = self.decks.get(sheet) or self.balls[sheet]
        f = anim(t, frames, spf)
        return img.crop((col * width, f * height, col * width + width, f * height + height))


# ---------------------------------------------------------------------------
# the schedule: every hit of a loop, on fixed beats
# ---------------------------------------------------------------------------


def row_at(v_local: float, t: float) -> int:
    return math.floor(v_local + SPEED * t)


def column_at(theta: float, t: float) -> int:
    return math.floor(theta / TAU * COLS + twist(t)) % COLS


class Ball:
    """A ball in the tunnel's cross-section at one depth: straight lines between fixed hits, in tunnel radii."""

    def __init__(self, path):
        self.path = path  # [(t, x, y, kind)] sorted, kind 'wall' | 'deck' | 'water'

    def at(self, t: float) -> tuple[float, float]:
        t %= DURATION
        p = self.path
        for i, (ta, xa, ya, _) in enumerate(p):
            tb, xb, yb, _ = p[(i + 1) % len(p)]
            if i + 1 == len(p):
                tb += DURATION
            for shift in (0.0, -DURATION):
                a, b = ta + shift, tb + shift
                if a <= t < b:
                    k = (t - a) / (b - a)
                    return xa + (xb - xa) * k, ya + (yb - ya) * k
        raise AssertionError(t)


class Schedule:
    def __init__(self) -> None:
        self.broken = {}  # (row in the loop, column) -> (absolute row at the hit, time, depth)
        self.hits = []  # (t, x, y, depth, world, kind, column, absolute row)
        self.balls = []  # (depth, Ball, radius in tunnel radii, screen radius)
        for radius, count, seed in FAR:
            self.balls.append(self._far(radius, count, seed))
        self.main = self._main()

    def _claim(self, theta: float, t: float, depth: float) -> tuple[int, int] | None:
        col, row = column_at(theta, t), row_at(depth, t)
        key = (row % N, col)
        if key in self.broken:
            return None
        return row, col

    def _wall_hit(self, rng, t, depth, br, lo, hi, side):
        for _ in range(200):
            theta = rng.uniform(lo, hi) if side is None else (rng.uniform(lo, (lo + hi) / 2) if side else rng.uniform((lo + hi) / 2, hi))
            got = self._claim(theta, t, depth)
            if got:
                row, col = got
                self.broken[(row % N, col)] = (row, t, depth)
                x, y = (1 - br) * math.cos(theta), (1 - br) * math.sin(theta)
                w = (row % N) // ROWS
                self.hits.append((t, x, y, depth, w, 'wall', col, row))
                return x, y
        raise RuntimeError('no brick left to hit')

    def _far(self, radius: float, count: int, seed: int):
        """A ball further down: wall to water to wall, breaking a brick every beat."""
        rng = random.Random(seed)
        depth = K / radius
        br = 4.5 / radius
        beat = DURATION / count
        path = []
        for i in range(count):
            t = (i + 0.5) * beat
            x, y = self._wall_hit(rng, t, depth, br, 0.25, math.pi - 0.25, i % 2 == 0)
            path.append((t, x, y, 'wall'))
        out = []
        for i, (t, x, y, kind) in enumerate(path):
            out.append((t, x, y, kind))
            t2, x2, y2, _ = path[(i + 1) % count]
            if i + 1 == count:
                t2 += DURATION
            tw = t + (t2 - t) * 0.5
            for _ in range(2):
                surface = -water_h(tw) + br
                d1, d2 = y - surface, y2 - surface
                tw = t + (t2 - t) * d1 / (d1 + d2)
            xw = x + (x2 - x) * d1 / (d1 + d2)
            out.append((tw % DURATION, xw, -water_h(tw) + br, 'water'))
            self.hits.append((tw % DURATION, xw, -water_h(tw), depth, None, 'water', None, None))
        out.sort()
        return depth, Ball(out), br, radius

    def _main(self):
        """The deck's ball: wall, deck, wall, the deck riding the water and meeting each fall."""
        rng = random.Random(20261110)
        br = 13.5 / R_PLAY
        beat = DURATION / MAIN_HITS
        walls = []
        for i in range(MAIN_HITS):
            t = (i + 0.5) * beat
            x, y = self._wall_hit(rng, t, V_PLAY, br, 0.2, math.pi - 0.2, None if i % 3 else i % 2 == 0)
            walls.append((t, x, y))
        path, self.landings = [], []
        deck_top = 21.0 / R_PLAY
        for i, (t, x, y) in enumerate(walls):
            path.append((t, x, y, 'wall'))
            t2, x2, y2 = walls[(i + 1) % MAIN_HITS]
            if i + 1 == MAIN_HITS:
                t2 += DURATION
            f = 0.5
            for _ in range(3):
                tl = t + (t2 - t) * f
                floor = -water_h(tl) + deck_top + br
                d1, d2 = y - floor, y2 - floor
                f = d1 / (d1 + d2)
            tl = t + (t2 - t) * f
            span = math.sqrt(max(0.0, 1 - water_h(tl) ** 2)) - 0.3
            xl = max(-span, min(span, x + (x2 - x) * f + rng.uniform(-0.12, 0.12)))
            floor = -water_h(tl) + deck_top + br
            path.append((tl % DURATION, xl, floor, 'deck'))
            self.landings.append((tl % DURATION, xl, (x2 - xl) / max(0.05, t2 - tl)))
            self.hits.append((tl % DURATION, xl, -water_h(tl), V_PLAY, None, 'deck', None, None))
        path.sort()
        self.landings.sort()
        return V_PLAY, Ball(path), br, R_PLAY

    def deck(self, t: float) -> float:
        t %= DURATION
        spots = [(lt, lx - max(-0.08, min(0.08, vx * 0.04))) for lt, lx, vx in self.landings]
        for i, (ta, xa) in enumerate(spots):
            tb, xb = spots[(i + 1) % len(spots)]
            if i + 1 == len(spots):
                tb += DURATION
            for shift in (0.0, -DURATION):
                a, b = ta + shift, tb + shift
                if a <= t < b:
                    k = (t - a) / (b - a)
                    k = k * k * (3 - 2 * k)
                    return xa + (xb - xa) * k
        raise AssertionError(t)

    def hero(self, t: float) -> int:
        """Whose deck it is: the world whose wall is at the deck, as of the last time the ball met it."""
        t %= DURATION
        last = max((lt for lt, _, _ in self.landings if lt <= t), default=self.landings[-1][0] - DURATION)
        return (row_at(V_PLAY, last) % N) // ROWS


# ---------------------------------------------------------------------------
# a frame
# ---------------------------------------------------------------------------


class Tunnel:
    def __init__(self, repo: Path) -> None:
        self.art = Art(repo)
        self.plan = Schedule()
        ys, xs = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
        self.xs, self.ys = xs + 0.5, ys + 0.5
        n = len(self.plan.broken)
        self.hit_row = np.full((N, COLS), 0, np.int64)
        self.hit_t = np.full((N, COLS), np.inf, np.float32)
        for (row, col), (absolute, t, _) in self.plan.broken.items():
            self.hit_row[row, col] = absolute
            self.hit_t[row, col] = t
        rng = random.Random(5)
        self.shards = [
            [(rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.4), rng.uniform(2.0, 6.5), rng.uniform(0.6, 1.0), rng.choice((2, 2, 3, 3, 4))) for _ in range(14)]
            for _ in self.plan.hits
        ]
        mid = (SIZE - 1) / 2
        d = np.hypot(self.xs - 0.5 - mid, self.ys - 0.5 - mid) / (SIZE / 2)
        self.vignette = np.where(d < 0.86, 1.0, np.clip(1 - (d - 0.86) / 0.14, 0, 1) ** 1.5).astype(np.float32)[..., None]
        self.n_hits = n
        self.glow = np.array([[m * 0.6 + c * 0.42 for m, c in zip(self.art.mortar[w], HEROES[w][6])] for w in range(WORLDS_N)], np.float32)

    # --- the wall, the water and what shows through ---

    def geometry(self, t: float):
        near, far = centres(t)
        cx, cy = near
        for _ in range(2):
            r = np.hypot(self.xs - cx, self.ys - cy)
            w = bend(r)
            cx = near[0] + (far[0] - near[0]) * w
            cy = near[1] + (far[1] - near[1]) * w
        dx, dy = self.xs - cx, self.ys - cy
        r = np.maximum(np.hypot(dx, dy), 0.75)
        return dx, dy, r

    def sample_wall(self, levels, theta, v_local, t, footprint):
        """The wall's colour where a ray meets it (theta, depth), its cell's world and whether that cell is broken."""
        u = theta / TAU * COLS + twist(t)
        a = v_local + SPEED * t
        col = np.floor(u).astype(np.int64) % COLS
        absolute = np.floor(a).astype(np.int64)
        row = absolute % N
        tu = (u % COLS) * 16
        tv = (a % N) * 8
        level = np.clip(np.floor(np.log2(np.maximum(footprint, 1e-6))), 0, 3).astype(np.int8)
        out = np.zeros(theta.shape + (3,), np.float32)
        for k, tex in enumerate(levels):
            sel = level == k
            if not sel.any():
                continue
            h, w = tex.shape[:2]
            iy = np.clip((tv[sel] / (2**k)).astype(np.int64), 0, h - 1)
            ix = np.clip((tu[sel] / (2**k)).astype(np.int64), 0, w - 1)
            out[sel] = tex[iy, ix]
        # broken: hit in this pass (or an earlier pass of the loop's run of that row)
        hit_row = self.hit_row[row, col]
        hit_t = self.hit_t[row, col]
        laps = np.floor_divide(absolute - hit_row, N)
        when = hit_t + laps * DURATION
        broken = np.isfinite(hit_t) & (t >= when + FLASH)
        age = np.where(np.isfinite(hit_t), t - when, 99.0)
        flash = np.where((age >= 0) & (age < FLASH), 1.0 - 0.5 * age / FLASH, 0.0)
        world = row // ROWS
        return out, broken, flash, world

    def render(self, frame: int) -> Image.Image:
        t = frame / FPS
        art, plan = self.art, self.plan
        levels = art.texture(t)
        dx, dy, r = self.geometry(t)
        theta = np.arctan2(-dy, dx)
        v_local = K / r
        footprint = np.maximum(COLS * 16 / (TAU * r), 8 * K / (r * r))

        colour, broken, flash, world = self.sample_wall(levels, theta, v_local, t, footprint)

        # the ball's light on the bricks round it
        mx, my = plan.main[1].at(t)
        hero = plan.hero(t)
        hc = np.array(HEROES[hero][6], np.float32)
        wx, wy = np.cos(theta), np.sin(theta)
        dist2 = (wx - mx) ** 2 + (wy - my) ** 2 + ((v_local - V_PLAY) * F_OVER_K) ** 2
        light = (1.1 / (1 + dist2 / 0.06))[..., None]
        for depth, ball, _, _ in plan.balls:
            fx, fy = ball.at(t)
            d2 = (wx - fx) ** 2 + (wy - fy) ** 2 + ((v_local - depth) * F_OVER_K) ** 2
            light = light + (0.6 / (1 + d2 / 0.03))[..., None]

        # depth: the wall fades into the far end's glow
        fog = np.clip(np.exp(-(v_local - 9.0) / 30.0), 0, 1)[..., None]
        tint = self.glow[world]

        # what shows through a broken brick: that world's scene, outside
        through = np.zeros_like(colour)
        if broken.any():
            sx = np.clip((192 + (self.xs - MID) * 0.45).astype(np.int64), 0, 383)
            sy = np.clip((108 + (self.ys - MID) * 0.45).astype(np.int64), 0, 215)
            for w in np.unique(world[broken]):
                sel = broken & (world == w)
                scene = art.scene(int(w), t)
                through[sel] = scene[sy[sel], sx[sel]] * 0.85
        lit = colour * (0.55 + 0.45 * fog) + hc * light * 0.35 * fog
        lit = np.where(flash[..., None] > 0, lit + (255 - lit) * flash[..., None] * 0.85, lit)
        wall = np.where(broken[..., None], through * np.maximum(fog, 0.35), lit)
        wall = wall * fog + tint * (1 - fog)

        # the water along the floor: where a ray meets the surface before the wall
        h = water_h(t)
        on_water = (dy / r) > h
        img = wall
        if on_water.any():
            sel = on_water
            ddx, ddy = dx[sel], dy[sel]
            dw = K * h / ddy  # its depth, in rows
            xw = ddx / ddy * h  # across, in tunnel radii
            aw = dw + SPEED * t
            ripple = (
                np.sin(TAU * (aw * 1.0) + 2.2 * np.sin(xw * 5 + TAU * 3 * t / DURATION))
                + 0.6 * np.sin(TAU * (aw * 0.5 - xw * 0.8) - TAU * 5 * t / DURATION)
            )
            # the reflection: the ray turned up off the surface, meeting the wall above
            px = ddx * dw / K
            a2 = (ddx * ddx + ddy * ddy) / (K * K)
            b2 = 2 * (px * ddx - h * ddy) / K
            c2 = px * px + h * h - 1
            s = (-b2 + np.sqrt(np.maximum(b2 * b2 - 4 * a2 * c2, 0))) / (2 * a2)
            hx = px + s * ddx / K + 0.02 * ripple
            hy = -h + s * ddy / K
            rv = dw + s
            rtheta = np.arctan2(hy, hx)
            rr = K / np.maximum(rv, 0.5)
            rfoot = np.maximum(COLS * 16 / (TAU * rr), 8 * K / (rr * rr)) * 1.5
            rcol, rbroken, _, rworld = self.sample_wall(levels, rtheta, rv, t, rfoot)
            rfog = np.clip(np.exp(-(rv - 9.0) / 30.0), 0, 1)[..., None]
            rtint = self.glow[rworld]
            rcol = np.where(rbroken[..., None], rtint, rcol)
            rcol = rcol * rfog + rtint * (1 - rfog)
            # the surface's own colour: the game's water, banded, brighter where the ripples crest
            depth_band = np.clip(1.6 + ripple * 0.75 + np.clip((8 - dw) / 8, 0, 1) * 0.6, 0, 4)
            band = np.array(WATER, np.float32)[np.floor(depth_band).astype(np.int64)]
            mix = np.clip(0.26 + 0.22 * np.exp(-dw / 14), 0, 0.5)[..., None]
            surface = rcol * mix + band * (1 - mix)
            crest = (ripple > 1.35)[..., None]
            surface = np.where(crest, surface * 0.5 + np.array(WATER[3], np.float32) * 0.5, surface)
            wfog = np.clip(np.exp(-(dw - 9.0) / 30.0), 0, 1)[..., None]
            wt = tint[sel]
            img = wall.copy()
            img[sel] = surface * wfog + wt * (1 - wfog)
            # the surface's edge against the wall: a line of foam
            edge = sel & ((dy / r) < h + 0.012)
            img[edge] = img[edge] * 0.4 + np.array(WATER[4], np.float32) * 0.6 * np.clip(np.exp(-(K * h / np.maximum(dy[edge], 0.1) - 9) / 30), 0.25, 1)[..., None]

        canvas = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')
        self.draw_play(canvas, t, hero)
        out = np.asarray(canvas.convert('RGB'), np.float32) * self.vignette
        return Image.fromarray(out.astype(np.uint8), 'RGB')

    # --- the balls, the deck, the bits ---

    def to_screen(self, t: float, x: float, y: float, depth: float) -> tuple[float, float]:
        radius = K / depth
        cx, cy = centre_at(t, radius)
        return cx + x * radius, cy - y * radius

    def draw_play(self, canvas: Image.Image, t: float, hero: int) -> None:
        art, plan = self.art, self.plan
        overlay = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        h = water_h(t)

        # the far balls first (further away), each with its reflection and its trail
        for i, (depth, ball, br, radius) in enumerate(plan.balls):
            who = (hero + 2 + i * 2) % WORLDS_N
            sheet, col = HEROES[who][5]
            orb = art.sprite(sheet, col, 9, 9, 8, 0.12, t)
            colour = HEROES[who][6]
            for j in range(5, 0, -1):
                bx, by = ball.at(t - j * 0.02)
                sx, sy = self.to_screen(t, bx, by, depth)
                a = int(90 * (1 - j / 6))
                draw.rectangle((round(sx) - 1, round(sy) - 1, round(sx) + 1, round(sy) + 1), (*colour, a))
            bx, by = ball.at(t)
            sx, sy = self.to_screen(t, bx, by, depth)
            rx, ry = self.to_screen(t, bx, -2 * h - by, depth)
            mirror = orb.transpose(Image.FLIP_TOP_BOTTOM)
            mirror.putalpha(mirror.getchannel('A').point(lambda v: v * 2 // 5))
            overlay.alpha_composite(mirror, (round(rx) - 4, round(ry) - 4))
            overlay.alpha_composite(orb, (round(sx) - 4, round(sy) - 4))

        # every hit's bits: shards off a broken brick toward us, a splash off the water, sparks off the deck
        for k, (ht, hx, hy, depth, w, kind, _, _) in enumerate(plan.hits):
            age = (t - ht) % DURATION
            if kind == 'wall' and FLASH <= age + FLASH < 1.4 + FLASH:
                age = age - FLASH
                if age < 0:
                    continue
                colour = art.average[w][art.motifs[w][0]]
                for ox, oy, toward, life, size in self.shards[k]:
                    if age >= life * 1.4:
                        continue
                    x = hx * (1 - 0.35 * age) + ox * age * 0.6
                    y = hy * (1 - 0.35 * age) + oy * age * 0.6 - 0.5 * age * age
                    d = depth - toward * age
                    if d < 1.2:
                        continue
                    sx, sy = self.to_screen(t, x, y, d)
                    s = max(1, round(size * V_PLAY / d))
                    fade = int(255 * (1 - age / (life * 1.4)))
                    draw.rectangle((round(sx), round(sy), round(sx) + s - 1, round(sy) + s - 1), (*colour, fade))
            elif kind == 'water' and age < 0.6:
                for j in range(6):
                    ang = math.pi * (0.15 + 0.7 * j / 5)
                    x = hx + math.cos(ang) * age * 0.35
                    y = hy + math.sin(ang) * age * 0.9 - 2.6 * age * age
                    if y < -water_h(t) - 0.01:
                        continue
                    sx, sy = self.to_screen(t, x, y, depth)
                    draw.point((round(sx), round(sy)), (*WATER[4], int(230 * (1 - age / 0.6))))
            elif kind == 'deck' and age < 0.55:
                colour = HEROES[hero][6]
                rng = random.Random(k)
                for j in range(8):
                    vx, vy = rng.uniform(-0.6, 0.6), rng.uniform(0.4, 1.2)
                    x = hx + vx * age
                    y = hy + 21 / R_PLAY + vy * age - 2.4 * age * age
                    if y < -water_h(t):
                        continue
                    sx, sy = self.to_screen(t, x, y, depth)
                    a = int(255 * (1 - age / 0.55))
                    draw.rectangle((round(sx), round(sy), round(sx) + 1, round(sy) + 1), (*(colour if j % 2 else WATER[4]), a))
        canvas.alpha_composite(overlay)

        # the deck, riding the water, and its reflection
        sheet, col = HEROES[hero][4]
        deck = art.sprite(sheet, col, 40, 10, 24, 0.1, t).resize((120, 30), Image.NEAREST)
        dx = plan.deck(t)
        sx, sy = self.to_screen(t, dx, -h, V_PLAY)
        bob = round(1.5 * math.sin(TAU * 15 * t / DURATION))
        mirror = deck.transpose(Image.FLIP_TOP_BOTTOM)
        mirror.putalpha(mirror.getchannel('A').point(lambda v: v * 2 // 5))
        canvas.alpha_composite(mirror, (round(sx) - 60, round(sy) + 9 + bob))
        canvas.alpha_composite(deck, (round(sx) - 60, round(sy) - 21 + bob))

        # the deck's ball: its trail, its reflection, the ball
        sheet, col = HEROES[hero][5]
        orb = art.sprite(sheet, col, 9, 9, 8, 0.12, t).resize((27, 27), Image.NEAREST)
        colour = HEROES[hero][6]
        trail = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        td = ImageDraw.Draw(trail)
        for j in range(7, 0, -1):
            bx, by = plan.main[1].at(t - j * 0.016)
            px, py = self.to_screen(t, bx, by, V_PLAY)
            rad = 9 - j * 0.9
            td.ellipse((px - rad, py - rad, px + rad, py + rad), (*colour, int(120 * (1 - j / 8))))
        canvas.alpha_composite(trail)
        bx, by = plan.main[1].at(t)
        px, py = self.to_screen(t, bx, by, V_PLAY)
        rx, ry = self.to_screen(t, bx, -2 * h - by, V_PLAY)
        mirror = orb.transpose(Image.FLIP_TOP_BOTTOM)
        mirror.putalpha(mirror.getchannel('A').point(lambda v: v * 2 // 5))
        canvas.alpha_composite(mirror, (round(rx) - 13, round(ry) - 13))
        canvas.alpha_composite(orb, (round(px) - 13, round(py) - 13))

        # the world's name as its wall reaches the deck
        self.draw_name(canvas, t)

    def draw_name(self, canvas: Image.Image, t: float) -> None:
        a = (V_PLAY + SPEED * t) % N
        w = int(a // ROWS)
        into = (a % ROWS) / SPEED  # seconds since this world's wall reached the deck
        if into > 3.2:
            return
        alpha = min(1.0, into / 0.4) * min(1.0, (3.2 - into) / 0.6)
        name = HEROES[w][0]
        small = Image.new('RGBA', (120, 10), (0, 0, 0, 0))
        d = ImageDraw.Draw(small)
        d.fontmode = '1'
        width = round(d.textlength(name, font=self.art.font))
        x = (120 - width) // 2
        d.text((x + 1, 1), name, font=self.art.font, fill=(*INK, 255))
        d.text((x, 0), name, font=self.art.font, fill=(*HEROES[w][6], 255))
        big = small.resize((240, 20), Image.NEAREST)
        big.putalpha(big.getchannel('A').point(lambda v: int(v * alpha)))
        rise = round(6 * (1 - min(1.0, into / 0.4)))
        canvas.alpha_composite(big, (120, 62 + rise))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_TUNNEL = None


def _init(repo: str) -> None:
    global _TUNNEL
    _TUNNEL = Tunnel(Path(repo))


def _frame(frame: int) -> bytes:
    return _TUNNEL.render(frame).tobytes()


def _render_into(repo: Path, commands: list[list[str]], workers: int) -> None:
    processes = [subprocess.Popen(c, stdin=subprocess.PIPE) for c in commands]
    with multiprocessing.Pool(workers, initializer=_init, initargs=(str(repo),)) as pool:
        for i, data in enumerate(pool.imap(_frame, range(FRAMES), chunksize=4)):
            if i % 90 == 0:
                print(f'  frame {i}/{FRAMES}', flush=True)
            for process in processes:
                process.stdin.write(data)
    for process in processes:
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError('ffmpeg failed')


def encode(repo: Path, destination: Path, ffmpeg: str, workers: int) -> None:
    """Two passes of VP8 for the page (the frames rendered twice: it's quicker than keeping them), and in the second
    pass an H.264 copy from the same frames for iCUE's own media picker."""
    raw = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', '-', '-an']
    vp8 = ['-c:v', 'libvpx', '-pix_fmt', 'yuv420p', '-b:v', BITRATE, '-qmin', '2', '-qmax', '40',
           '-g', str(FRAMES), '-auto-alt-ref', '0', '-lag-in-frames', '0', '-deadline', 'good', '-cpu-used', '0',
           '-passlogfile', str(destination.with_suffix(''))]
    print('pass 1')
    _render_into(repo, [raw + vp8 + ['-pass', '1', '-f', 'webm', 'NUL' if sys.platform == 'win32' else '/dev/null']], workers)
    print('pass 2')
    local = raw + ['-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                   str(destination.with_suffix('.mp4'))]
    _render_into(repo, [raw + vp8 + ['-pass', '2', str(destination)], local], workers)
    for log in destination.parent.glob(destination.stem + '*.log'):
        log.unlink()


def write_page(root: Path, part_count: int) -> None:
    scripts = '\n'.join(f'  <script src="tunnel-parts/video-part-{n:02d}.js"></script>' for n in range(1, part_count + 1))
    root.joinpath('index.html').write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
  <meta name="theme-color" content="#000000">
  <title>AVENGIS Rebounded Endless Run</title>
  <style>
    html,body{{width:100%;height:100%;margin:0;overflow:hidden;background:#000}}
    body{{display:grid;place-items:center}}
    video{{display:block;width:100vw;height:100vh;object-fit:cover;background:#000;image-rendering:pixelated}}
  </style>
  <script>window.__avenPrismaticParts=[];</script>
{scripts}
</head>
<body>
  <video id="tunnel" autoplay muted loop playsinline preload="auto" disablepictureinpicture></video>
  <script>
    (function(){{
      var video=document.getElementById("tunnel"), payload=window.__avenPrismaticParts.join("");
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
        encoding='utf-8',
    )


def main() -> None:
    parser = argparse.ArgumentParser(description='Render the Rebounded tunnel loop.')
    parser.add_argument('repo', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent / 'rebounded-tunnel')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--preview', type=Path)
    parser.add_argument('--preview-frames', type=int, nargs='*', default=[0, 120, 300, 450, 700, 899])
    args = parser.parse_args()

    if args.preview:
        tunnel = Tunnel(args.repo)
        print(f'{len(tunnel.plan.broken)} bricks broken a loop')
        args.preview.mkdir(parents=True, exist_ok=True)
        for frame in args.preview_frames:
            tunnel.render(frame).save(args.preview / f'tunnel_{frame:03d}.png')
            print(f'preview frame {frame}', flush=True)
        return

    args.root.mkdir(parents=True, exist_ok=True)
    video = args.root / 'AVENGIS_Rebounded_Endless_Run_480x480.webm'
    encode(args.repo, video, args.ffmpeg, args.workers)
    part_count = write_payload(video, args.root / 'tunnel-parts')
    write_page(args.root, part_count)
    print(f'encoded {FRAMES} frames, {video.stat().st_size / 1_048_576:.2f} MB across {part_count} payload parts')


if __name__ == '__main__':
    main()
