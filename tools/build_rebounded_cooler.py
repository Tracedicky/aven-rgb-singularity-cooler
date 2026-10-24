"""AVENGIS Rebounded cooler loop: a seamless 30-second 480x480 VP8/WebM for the iCUE LCD.

A tower of bricks, one row a world, sinks steadily into the panel while the crew's deck and ball break it one brick
at a time. Each world's row brings its own scene behind the play, its own hero's deck and ball, and its name; after
the fifth world the tower has sunk exactly five rows and the picture is frame for frame where it began.

Everything is scheduled rather than simulated, so it closes on itself: the 35 hits a loop sit on fixed beats, each
brick breaks at its beat wherever the sinking tower has carried it, the ball flies straight lines between the hits
and the deck, and every particle, trail and animation is read from time modulo the loop.

Drawn from the game's own art (scenes, bricks, decks, balls, font) at 240 x 240 and scaled 2x nearest-neighbour.

    py tools/build_rebounded_cooler.py <Rebounded checkout>                 encode the loop into rebounded/
    py tools/build_rebounded_cooler.py <Rebounded checkout> --preview DIR   write sample PNG frames instead
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 480
ART = 240  # the art's own resolution, scaled 2x
FPS = 30
DURATION = 30.0
FRAMES = int(FPS * DURATION)

COLUMNS = 7
CELL_W, CELL_H = 16, 8
FIELD_X = (ART - COLUMNS * CELL_W) // 2  # 64
FRONT = 112  # where a row's underside sits when it's broken, on average
DECK_Y = 196  # the deck's top
HITS = 35  # a loop: one row a world, a brick a beat
BEAT = DURATION / HITS
WINDOW = DURATION / 5  # each world's six seconds
SINK = 5 * CELL_H / DURATION  # px/s: five rows a loop
FADE = 1.0  # the scenes' crossfade
INK = (5, 10, 18)

WORLDS = [  # (name, scene, dusk, brick sheet, hero's deck column, orb, colour)
    ('COPPERCLOUD', 'coppercloud-harbour', 0.0, 'coppercloud/coppercloud-bricks-032', 0, 'aether-ball', (82, 200, 190)),
    ('GLASSROOT', 'glassroot-canopy', 0.0, 'glassroot/glassroot-bricks-032', 1, 'dewdrop', (138, 219, 137)),
    ('EMBERLINE', 'emberline-smokestacks', 1.0, 'emberline/emberline-bricks-032', 2, 'spark', (235, 169, 96)),
    ('TIDEGLASS', 'tideglass-bay', 0.0, 'tideglass/tideglass-bricks-032', 3, 'pearl', (124, 188, 235)),
    ('STORMGLASS', 'stormglass-peaks', 1.0, 'stormglass/storm-bricks-032', 4, 'glint', (188, 147, 219)),
]


def cycles(seconds_per_cycle: float) -> int:
    """A whole number of an animation's cycles in the loop, as near its own speed as can be (so it closes)."""
    return max(1, round(DURATION / seconds_per_cycle))


def anim(t: float, frames: int, seconds_per_frame: float) -> int:
    n = cycles(frames * seconds_per_frame)
    return int((t / DURATION) * n * frames) % frames


# ---------------------------------------------------------------------------
# the art
# ---------------------------------------------------------------------------


class Art:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.scenes = {w[1]: self._scene(w[1]) for w in WORLDS}
        self.bricks = [Image.open(repo / 'assets/worlds' / (w[3] + '.png')).convert('RGBA') for w in WORLDS]
        self.decks = Image.open(repo / 'assets/crew/decks-012.png').convert('RGBA')
        self.orbs = [Image.open(repo / 'assets/crew' / (w[5] + '.png')).convert('RGBA') for w in WORLDS]
        self.font = ImageFont.truetype(str(repo / 'assets/fonts/PressStart2P.ttf'), 8)
        # each brick motif's average colour, for its shards
        self.shard = [[self._average(sheet.crop((m * 16, 0, m * 16 + 16, 8))) for m in range(9)] for sheet in self.bricks]
        self.white = [self._whiten(sheet) for sheet in self.bricks]
        # each world's row is one brick from its sheet: the one nearest its hero's colour, so the tower reads in bands
        self.motif = [
            min(range(9), key=lambda m: sum((a - b) ** 2 for a, b in zip(self.shard[w][m], WORLDS[w][6])))
            for w in range(5)
        ]

    @staticmethod
    def _average(img: Image.Image) -> tuple[int, int, int]:
        px = [p for p in img.get_flattened_data() if p[3] > 128]
        return tuple(sum(p[i] for p in px) // len(px) for i in range(3))

    @staticmethod
    def _whiten(sheet: Image.Image) -> Image.Image:
        white = Image.new('RGBA', sheet.size, (255, 255, 255, 0))
        white.putalpha(sheet.getchannel('A'))
        return white

    def _scene(self, name: str):
        """[(drift, fps, wrap, (day frames, dusk frames))] back to front, each frame an image and its offset."""
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
            runs = []
            for first, last in (day, dusk):
                run = range(first, last + 1) if fps else (first,)
                runs.append([mine[i][:3] for i in run])
            out.append((float(fields.get('drift', 0)), fps, int(fields.get('wrap', 0)) or width, runs))
        return out


# ---------------------------------------------------------------------------
# the schedule
# ---------------------------------------------------------------------------


class Schedule:
    """Every hit of a loop: (time, column, world), and the ball's and deck's paths between them."""

    def __init__(self) -> None:
        rng = random.Random(20261024)
        self.hits = []  # (t, column, world, x, y): y the ball's centre as it strikes the brick's underside
        for w in range(5):
            order = list(range(COLUMNS))
            while True:  # an order that wanders across the row without jumping wall to wall every time
                rng.shuffle(order)
                if max(abs(a - b) for a, b in zip(order, order[1:])) <= 4:
                    break
            for i, c in enumerate(order):
                t = (w * COLUMNS + i + 0.5) * BEAT
                x = FIELD_X + c * CELL_W + CELL_W / 2
                y = self.underside(w, t) + 4.5
                self.hits.append((t, c, w, x, y))
        self.broken = {(h[1], h[2]): h[0] for h in self.hits}  # (column, row in the loop) -> when
        # the bounces off the deck between hits: the straight lines meet the deck where they mirror
        self.landings = []  # (t, x, outgoing vx)
        for k, (t0, _, _, x0, y0) in enumerate(self.hits):
            t1, _, _, x1, y1 = self.hits[(k + 1) % HITS]
            if k + 1 == HITS:
                t1 += DURATION
            ball_floor = DECK_Y - 4.5
            down, up = ball_floor - y0, ball_floor - y1
            f = down / (down + up)
            self.landings.append((t0 + f * (t1 - t0), x0 + f * (x1 - x0), (x1 - x0) / (t1 - t0), (t0, x0, y0, t1, x1, y1, f)))

    @staticmethod
    def underside(row: int, t: float) -> float:
        """Row `row`'s underside at time t: rows climb upward from the loop's first; the tower sinks at SINK."""
        return FRONT + SINK * (t - WINDOW / 2) - CELL_H * row  # (row w's underside is at FRONT mid-window)

    def ball(self, t: float) -> tuple[float, float]:
        t %= DURATION
        for lt, lx, _, (t0, x0, y0, t1, x1, y1, f) in self.landings:
            span = (t0, t1)
            for shift in (0, -DURATION):
                a, b = span[0] + shift, span[1] + shift
                if a <= t < b:
                    k = (t - a) / (b - a)
                    x = x0 + (x1 - x0) * k
                    floor = DECK_Y - 4.5
                    y = y0 + (floor - y0) * (k / f) if k < f else floor + (y1 - floor) * ((k - f) / (1 - f))
                    return x, y
        raise AssertionError(t)

    def deck(self, t: float) -> float:
        """The deck: under each landing, set a little to the side it means to send the ball, easing between."""
        t %= DURATION
        spots = [(lt, self._aim(lx, vx)) for lt, lx, vx, _ in self.landings]
        for i, (ta, xa) in enumerate(spots):
            tb, xb = spots[(i + 1) % len(spots)]
            if i + 1 == len(spots):
                tb += DURATION
            for shift in (0, -DURATION):
                a, b = ta + shift, tb + shift
                if a <= t < b:
                    k = (t - a) / (b - a)
                    k = k * k * (3 - 2 * k)
                    return xa + (xb - xa) * k
        raise AssertionError(t)

    @staticmethod
    def _aim(x: float, vx: float) -> float:
        return x - max(-12.0, min(12.0, vx * 0.09))


# ---------------------------------------------------------------------------
# a frame
# ---------------------------------------------------------------------------


class Cooler:
    def __init__(self, art: Art) -> None:
        self.art = art
        self.plan = Schedule()
        rng = random.Random(7)
        self.shards = [  # each hit's shards: (vx, vy, life, size)
            [(rng.uniform(-60, 60), rng.uniform(-70, 10), rng.uniform(0.45, 0.9), rng.choice((1, 1, 2))) for _ in range(10)]
            for _ in range(HITS)
        ]
        self.sparks = [
            [(rng.uniform(-40, 40), rng.uniform(-60, -20), rng.uniform(0.25, 0.5)) for _ in range(5)] for _ in range(HITS)
        ]
        self.vignette = self._vignette()

    # --- the scenes ---

    def scene(self, w: int, t: float) -> Image.Image:
        """World w's scene: 240 wide from the middle of its 384, the sky and ground stretched to fill the square.
        Its clock starts as it fades in, so whatever drifts is only ever seen moving forward."""
        name, scene, mix = WORLDS[w][1], WORLDS[w][1], WORLDS[w][2]
        local = (t - (w * WINDOW - FADE)) % DURATION
        frame = Image.new('RGBA', (384, 216), (0, 0, 0, 255))
        for drift, fps, wrap, (day, dusk) in self.art.scenes[scene]:
            run = dusk if mix >= 0.5 else day
            img, ox, oy = run[int(local * fps) % len(run)] if fps else run[0]
            if img is None:
                continue
            x = round(local * drift)
            start = -(x % wrap)
            while start + ox < 384:
                frame.alpha_composite(img, (max(0, start + ox), oy), (max(0, -(start + ox)), 0))
                start += wrap
        crop = frame.crop((72, 0, 312, 216)).convert('RGB')
        out = Image.new('RGB', (ART, ART))
        out.paste(crop.crop((0, 0, ART, 1)).resize((ART, 12)), (0, 0))
        out.paste(crop, (0, 12))
        out.paste(crop.crop((0, 215, ART, 216)).resize((ART, 12)), (0, 228))
        return Image.eval(out, lambda v: int(v * 0.72))  # pulled back, so the play reads over it

    def world_at(self, t: float) -> tuple[int, int, float]:
        """(this world, the next, how far into their crossfade 0..1)."""
        w = int(t // WINDOW) % 5
        into = t - w * WINDOW - (WINDOW - FADE / 2)
        if into > 0:
            return w, (w + 1) % 5, min(1.0, into / FADE)
        into = t - w * WINDOW + FADE / 2
        if into < FADE:  # (still finishing the fade from the world before)
            return (w - 1) % 5, w, into / FADE
        return w, w, 0.0

    # --- the play ---

    def brick(self, canvas: Image.Image, w: int, c: int, x: int, y: int, t: float, flash: float = 0.0) -> None:
        motif = self.art.motif[w]
        f = anim(t, 24, 0.1)
        box = (motif * 16, f * 8, motif * 16 + 16, f * 8 + 8)
        img = self.art.bricks[w].crop(box)
        canvas.paste(img, (x, y), img)
        if flash > 0:
            white = self.art.white[w].crop(box)
            white.putalpha(white.getchannel('A').point(lambda a: int(a * flash)))
            canvas.paste(white, (x, y), white)

    def render(self, frame: int) -> Image.Image:
        t = frame / FPS
        plan = self.plan
        w, nxt, k = self.world_at(t)
        canvas = self.scene(w, t)
        if k > 0 and nxt != w:
            canvas = Image.blend(canvas, self.scene(nxt, t), k)
        hero = w if k < 0.5 else nxt

        # the tower: every row on screen, from below the deck's line to above the panel's top
        for n in range(-2, 5):  # loops back and on
            for row in range(5):
                bottom = Schedule.underside(row + 5 * n, t)
                if bottom < -2 or bottom - CELL_H > DECK_Y:
                    continue
                for c in range(COLUMNS):
                    when = plan.broken[(c, row)] + DURATION * n
                    if when <= t:
                        continue
                    x = FIELD_X + c * CELL_W
                    self.brick(canvas, row, c, x, round(bottom) - CELL_H, t)

        overlay = Image.new('RGBA', (ART, ART), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # each hit's flash and shards, wherever they are in their short lives (across the loop's seam too)
        for i, (ht, c, row, hx, hy) in enumerate(plan.hits):
            age = (t - ht) % DURATION
            if age < 0.12:
                x = FIELD_X + c * CELL_W
                self.brick(canvas, row, c, x, round(Schedule.underside(row, ht)) - CELL_H, ht, 1 - age / 0.12)
            if age < 0.9:
                colour = self.art.shard[row][self.art.motif[row]]
                for vx, vy, life, size in self.shards[i]:
                    if age >= life:
                        continue
                    px = hx + vx * age
                    py = hy - 6 + vy * age + 160 * age * age
                    a = int(255 * (1 - age / life))
                    draw.rectangle((round(px), round(py), round(px) + size - 1, round(py) + size - 1), (*colour, a))

        # the deck's sparks as the ball meets it
        for i, (lt, lx, _, _) in enumerate(plan.landings):
            age = (t - lt) % DURATION
            if age < 0.5:
                colour = WORLDS[hero][6]
                for vx, vy, life in self.sparks[i]:
                    if age < life:
                        a = int(255 * (1 - age / life))
                        draw.point((round(lx + vx * age), round(DECK_Y + vy * age + 120 * age * age)), (*colour, a))

        # the ball's trail
        colour = WORLDS[hero][6]
        for j in range(6, 0, -1):
            bx, by = plan.ball(t - j * 0.018)
            a = int(110 * (1 - j / 7))
            r = 3 - j // 3
            draw.ellipse((bx - r, by - r, bx + r, by + r), (*colour, a))

        canvas = canvas.convert('RGBA')
        canvas.alpha_composite(overlay)

        # the deck and the ball
        dx = round(plan.deck(t)) - 20
        f = anim(t, 24, 0.1)
        deck = self.art.decks.crop((WORLDS[hero][4] * 40, f * 10, WORLDS[hero][4] * 40 + 40, f * 10 + 10))
        canvas.alpha_composite(deck, (dx, DECK_Y))
        bx, by = plan.ball(t)
        orb = self.art.orbs[hero]
        o = anim(t, 8, 0.12)
        canvas.alpha_composite(orb.crop((0, o * 9, 9, o * 9 + 9)), (round(bx) - 4, round(by) - 4))

        # the world's name, under the deck
        label = Image.new('RGBA', (ART, ART), (0, 0, 0, 0))
        ld = ImageDraw.Draw(label)
        ld.fontmode = '1'
        for world, alpha in ((w, 1 - k), (nxt, k)) if nxt != w else ((w, 1.0),):
            if alpha <= 0:
                continue
            text = WORLDS[world][0]
            width = round(ld.textlength(text, font=self.art.font))
            x = (ART - width) // 2
            a = int(255 * alpha)
            ld.text((x + 1, 216 + 1), text, font=self.art.font, fill=(*INK, a))
            ld.text((x, 216), text, font=self.art.font, fill=(*WORLDS[world][6], a))
        canvas.alpha_composite(label)

        canvas.alpha_composite(self.vignette)
        return canvas.convert('RGB').resize((SIZE, SIZE), Image.NEAREST)

    @staticmethod
    def _vignette() -> Image.Image:
        """The round panel: the corners to black, a soft dark rim just inside the bezel."""
        v = Image.new('RGBA', (ART, ART))
        px = v.load()
        mid = (ART - 1) / 2
        for y in range(ART):
            for x in range(ART):
                d = math.hypot(x - mid, y - mid) / (ART / 2)
                a = 0 if d < 0.82 else min(1.0, (d - 0.82) / 0.18) ** 1.6
                px[x, y] = (0, 0, 0, int(255 * a))
        return v


# ---------------------------------------------------------------------------
# encoding and the page (the same as the Prismatic Core loops)
# ---------------------------------------------------------------------------


def encode(frames_iter, destination: Path, ffmpeg: str) -> None:
    command = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{SIZE}x{SIZE}', '-r', str(FPS),
        '-i', '-',
        '-an',
        '-c:v', 'libvpx', '-pix_fmt', 'yuv420p',
        '-b:v', '2600k', '-crf', '8', '-qmin', '0', '-qmax', '26',
        '-g', str(FRAMES), '-auto-alt-ref', '0', '-lag-in-frames', '0',
        '-deadline', 'good', '-cpu-used', '0',
        str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames_iter:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError('ffmpeg failed to encode the loop')


def mp4(webm: Path, destination: Path, ffmpeg: str) -> None:
    """An H.264 copy for iCUE's own media picker (a local file rather than the web page)."""
    subprocess.run(
        [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(webm), '-c:v', 'libx264', '-preset', 'slow',
         '-crf', '14', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(destination)],
        check=True,
    )


def write_payload(video: Path, folder: Path, chunk: int = 1_400_000) -> int:
    payload = base64.b64encode(video.read_bytes()).decode('ascii')
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob('video-part-*.js'):
        existing.unlink()
    parts = [payload[index : index + chunk] for index in range(0, len(payload), chunk)]
    for number, part in enumerate(parts, start=1):
        target = folder / f'video-part-{number:02d}.js'
        target.write_text(f'window.__avenPrismaticParts.push("{part}");\n', encoding='utf-8')
    return len(parts)


def write_page(root: Path, part_count: int) -> None:
    scripts = '\n'.join(f'  <script src="rebounded-parts/video-part-{n:02d}.js"></script>' for n in range(1, part_count + 1))
    root.joinpath('index.html').write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
  <meta name="theme-color" content="#000000">
  <title>AVENGIS Rebounded Cooler</title>
  <style>
    html,body{{width:100%;height:100%;margin:0;overflow:hidden;background:#000}}
    body{{display:grid;place-items:center}}
    video{{display:block;width:100vw;height:100vh;object-fit:cover;background:#000;image-rendering:pixelated}}
  </style>
  <script>window.__avenPrismaticParts=[];</script>
{scripts}
</head>
<body>
  <video id="rebounded" autoplay muted loop playsinline preload="auto" disablepictureinpicture></video>
  <script>
    (function(){{
      var video=document.getElementById("rebounded"), payload=window.__avenPrismaticParts.join("");
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
    parser = argparse.ArgumentParser(description='Render the Rebounded cooler loop.')
    parser.add_argument('repo', type=Path, help='an AVENGIS Rebounded checkout (for its art)')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent / 'rebounded')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--preview', type=Path, help='write sample PNG frames here instead of encoding')
    parser.add_argument('--preview-frames', type=int, nargs='*', default=[0, 90, 165, 180, 400, 899])
    args = parser.parse_args()

    cooler = Cooler(Art(args.repo))

    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        for frame in args.preview_frames:
            cooler.render(frame).save(args.preview / f'rebounded_{frame:03d}.png')
            print(f'preview frame {frame}')
        return

    def frames():
        for frame in range(FRAMES):
            if frame % 90 == 0:
                print(f'  frame {frame}/{FRAMES}', flush=True)
            yield cooler.render(frame)

    args.root.mkdir(parents=True, exist_ok=True)
    video = args.root / 'AVENGIS_Rebounded_Cooler_480x480.webm'
    encode(frames(), video, args.ffmpeg)
    mp4(video, args.root / 'AVENGIS_Rebounded_Cooler_480x480.mp4', args.ffmpeg)
    part_count = write_payload(video, args.root / 'rebounded-parts')
    write_page(args.root, part_count)
    print(f'encoded {FRAMES} frames, {video.stat().st_size / 1_048_576:.2f} MB across {part_count} payload parts')


if __name__ == '__main__':
    main()
