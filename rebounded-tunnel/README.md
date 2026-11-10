# AVENGIS Rebounded: The Endless Run

A seamless thirty-second 480×480 VP8/WebM iCUE loop, built from AVENGIS Rebounded's own art: all six worlds'
animated bricks, their scenes, the crew's decks and balls, and the Press Start font.

You fly down an endless tunnel walled with the six worlds' bricks. It turns once a loop and bends as it goes,
and the far end glows in each world's colour.

Living water runs along the floor, rising and falling as it does in the game, with the tunnel mirrored in it. The
crew's deck rides the water and keeps a ball in play. Every hit lights a brick and breaks it into shards toward you,
and the hole shows that world's scene outside.

Two more balls play further down the tunnel and splash off the water. The holes they knock come rushing past.

When a world's stretch of wall reaches the deck, that world's hero takes over and its name rises in: Coppercloud,
Glassroot, Emberline, Tideglass, Stormglass, Clockwork.

## Live iCUE page

https://tracedicky.github.io/aven-rgb-singularity-cooler/rebounded-tunnel/

`AVENGIS_Rebounded_Endless_Run_480x480.mp4` is a full-quality H.264 copy made from the same frames.

## Loop integrity

Everything is a function of time modulo the loop:

- The tunnel travels exactly its own length in the thirty seconds: six worlds of twelve rows.
- It turns exactly once, and its sway and bend are whole waves of the loop.
- The water rises and falls twice.
- Every hit sits on a fixed beat.
- A brick is broken once its row has passed the depth it was hit at, so holes travel with the wall.
- Every animation, scene drift and particle is read from the loop's clock.

Frame 900 matches frame 0 apart from a few pixels of floating-point rounding.

## Rebuilding

```sh
python tools/build_rebounded_tunnel.py <path to an AVENGIS-Rebounded checkout> --ffmpeg <ffmpeg>
```

Needs numpy, Pillow and an `ffmpeg` with `libvpx` and `libx264`. It renders in two passes across six worker
processes, about a minute each.

Pass `--preview <dir>` to dump sample PNG frames instead of encoding the loop.
