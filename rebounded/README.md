# AVENGIS Rebounded Cooler

A seamless thirty-second 480×480 VP8/WebM iCUE loop, drawn from AVENGIS
Rebounded's own pixel art: its parallax scenes, world bricks, the crew's decks
and balls, and the Press Start font, at 240×240 scaled 2× nearest-neighbour.

A tower of bricks, one row a world, sinks steadily into the panel while a deck
and ball break it one brick at a time. As each world's row reaches the deck, its
scene fades in behind the play, its hero's deck and ball take over, and its name
shows beneath: Coppercloud, Glassroot, Emberline, Tideglass, Stormglass.

## Live iCUE page

https://tracedicky.github.io/aven-rgb-singularity-cooler/rebounded/

`AVENGIS_Rebounded_Cooler_480x480.mp4` is an H.264 copy for picking as a local
file in iCUE instead.

## Loop integrity

Nothing is simulated. The 35 hits sit on fixed beats, each brick breaks on its
beat wherever the sinking tower has carried it, the ball flies straight lines
between the hits and the deck, and every shard, trail and sprite animation is
read from time modulo the loop. The tower sinks exactly five rows in the thirty
seconds, so frame 899 hands back to frame 0 like any other world change.

## Rebuilding

```sh
python tools/build_rebounded_cooler.py <path to an AVENGIS-Rebounded checkout>
```

Needs Pillow and an `ffmpeg` with `libvpx` and `libx264`. Pass
`--preview <dir>` to dump sample PNG frames instead of encoding the loop.
