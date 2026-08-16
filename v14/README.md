# Aven Prismatic Core Cooler — V14 Event Horizon

A seamless nine-second 480×480 VP8/WebM iCUE loop, rendered procedurally rather
than post-processed from a still. A tilted accretion disk orbits a true-black
event horizon, a lensed halo arcs over the top, and a spectral wave travels the
full colour wheel across the loop.

## What changed from V13

- **Real motion instead of a shimmer pass.** V13 animated a fixed image. Every
  frame here is rendered from scratch, so the disk actually orbits — with
  Keplerian shear, the inner filaments overtaking the outer ones.
- **A designed palette rather than every hue at once.** Only a slice of the
  colour wheel is on screen at any instant, and that slice drifts through a
  complete turn over the nine seconds. The whole RGB range still gets shown, it
  just gets shown in sequence, which keeps each individual frame composed.
- **A genuinely black core.** The horizon is protected after the glow passes, so
  the panel's black level is used as the subject instead of being washed out.
- **Rendered in linear light** at twice the delivery resolution, then filmic
  tone-mapped, so saturated highlights roll off in colour instead of clipping to
  flat white.
- **Lens treatment:** HDR bloom, a restrained anamorphic streak, radial chromatic
  aberration, grain and dithering — the last of these matters on large smooth
  gradients at this size.
- **Composed for a round panel.** The subject is centred with a falloff toward
  the corners, so nothing important sits where the bezel crops.

## Physical touches

The brightness asymmetry across the ring is relativistic beaming — the side of
the disk sweeping toward the viewer reads brighter. The thin, intense ring
hugging the horizon is the photon ring, and the inner disk desaturates toward
white where it is hottest.

## Rebuilding

```sh
python3 tools/build_v14_event_horizon.py
```

Needs `numpy` and an `ffmpeg` with `libvpx`. Pass `--preview <dir>` to dump
sample PNG frames instead of encoding the loop.

## Loop integrity

Every animated quantity completes a whole number of cycles across the nine
seconds, so frame 269 hands back to frame 0 with no visible jump — the wrap
differs by the same amount as any other adjacent pair of frames.
