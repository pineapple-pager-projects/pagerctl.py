# pagerctl.py

A **pure-Python graphics, input, and hardware library for the WiFi Pineapple Pager** — a drop-in replacement for `libpagerctl.so` with the **same `Pager` API** and **no native library or bundled binaries**.

It lets a payload draw a full-color animated UI, read the buttons, and drive the
buzzer/vibrator/LEDs/backlight — using only Python plus the firmware's own
commands and sysfs. Everything heavy (TrueType rasterization, image decoding and
scaling) is done **ahead of time on your computer** by `tools/build_assets.py`, so
the device only ever does fast `memcpy`-style blits.

```python
from pagerctl import Pager

with Pager() as p:                 # init() on enter, cleanup() on exit
    p.set_rotation(270)            # landscape (480x222)
    p.clear(p.rgb(0, 0, 32))
    p.draw_ttf(10, 10, "Hello!", p.WHITE, "fonts/Arial.ttf", 24)
    p.fill_rect(10, 50, 120, 30, p.ORANGE)
    p.flip()
    if p.wait_button() & Pager.BTN_A:
        p.beep(880, 120)
```

---

## Why

The original Pager graphics library shipped as a cross-compiled `libpagerctl.so`
(plus bundled `nmap`/crypto binaries in bigger payloads). `pagerctl.py` removes all
of that: it's one Python file you can drop into any payload, and the only
device-side dependencies come from `opkg` or are produced by the build tool as
plain data. That makes payloads smaller, easier to audit, and portable.

## Features

- **Framebuffer drawing** to `/dev/fb0` (222×480 RGB565) via the firmware's
  `UI_TAKEOVER` — no need to stop the pager service.
- **Text** with real TrueType fonts, pre-rendered into bitmap **glyph atlases**
  (`draw_ttf`, `ttf_width`, centered/right helpers) + a built-in-font shim
  (`draw_text`).
- **Images / sprites** with transparency, pre-rendered to native RGB565 blobs
  (`draw_image_file_scaled`, `get_image_info`, …).
- **Primitives**: `fill_rect`, `rect`, `hline`, `vline`, `pixel`, `line`,
  `circle`, `fill_circle`.
- **Input**: reads `/dev/input/event0` directly in a background thread with full
  press / release / held state and an event queue (`wait_button`, `poll_input`,
  `get_input_event`, `peek_buttons`).
- **Sound & haptics** via the firmware `RINGTONE` / `VIBRATE` commands
  (`beep`, `play_rtttl`, `vibrate`).
- **LEDs** (per-direction D-pad RGB + A/B button LEDs) via sysfs, **backlight**
  via sysfs, **display power** via `ENABLE_DISPLAY` / `DISABLE_DISPLAY`.
- **Rotation** 0 (portrait) and 270 (landscape); 90/180 are best-effort.
- **Host testing** with a fake in-memory framebuffer (`PAGERCTL_FAKE=1`).

## Requirements

**On the Pager** (install once, e.g. from `opkg`):
- `python3` (`python3-light` + any stdlib splits your app uses, e.g.
  `python3-ctypes`, `python3-multiprocessing`).
- Firmware with the `UI_TAKEOVER` / `UI_RELEASE` / `RINGTONE` / `VIBRATE` /
  `ENABLE_DISPLAY` / `DISABLE_DISPLAY` commands (standard on current Pager OS).

**On your computer** (to build assets — not needed on the device):
- Python 3, [Pillow](https://pypi.org/project/Pillow/), and
  [NumPy](https://pypi.org/project/numpy/).

The device needs **no** freetype, PIL, or compiler — that's the whole point of the
build step.

## Install

Copy `pagerctl.py` next to your payload code (or into a `lib/` on `PYTHONPATH`):

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from pagerctl import Pager
```

## Assets: the build step

The device can't rasterize fonts or decode/scale images at runtime (no freetype /
PIL, and per-pixel Python is far too slow). So you pre-render on your computer:

```sh
# Fonts -> <font>.fatlas  (glyph atlases, all sizes, rot 0 + 270)
python3 tools/build_assets.py --fonts-only

# Images -> <image>.r565  (native-resolution RGB565, used by get_image_info)
python3 tools/build_assets.py --images-only

# Pre-rendered, scaled + rotated native blobs for a theme's layout sizes
python3 tools/build_assets.py --prewarm --theme <name>
```

This produces data files **next to your source assets**:

| File | What it is | Consumed by |
|---|---|---|
| `<font>.fatlas` | Per-size bitmap glyph atlas (RLE spans), for rotations 0 & 270 | `draw_ttf`, `ttf_width`, `draw_text` |
| `<image>.r565` | Opaque native-res RGB565 + tiny header | `get_image_info`, lazy fallback |
| `prewarm/<img>.<w>x<h>.r<rot>.nbg` | Opaque image scaled+rotated for one draw size | `draw_image_file_scaled` (opaque) |
| `prewarm/<img>.<w>x<h>.r<rot>.nspr` | Transparent sprite (per-row opaque runs) | `draw_image_file_scaled` (with alpha) |

`draw_ttf` works for **any** text at the sizes you built. `draw_image_file_scaled`
works for any `(image, width, height, rotation)` you prewarmed; if a size wasn't
prewarmed it returns `-1` (draws nothing) rather than stalling the device.

Ship the generated `.fatlas` / `.r565` / `prewarm/` files with your payload (they
are data, not binaries), or regenerate them as a packaging step.

## API overview

Everything is on the `Pager` class. It mirrors `libpagerctl.so`, so existing
payloads work unchanged.

**Lifecycle** — `init()`, `cleanup()`, context manager (`with Pager() as p:`),
`set_rotation(r)`, `width`, `height`.

**Frame** — `clear(color)`, `flip()`, `get_ticks()`, `delay(ms)`, `frame_sync()`.

**Color** — `Pager.rgb(r, g, b)`, `Pager.hex_color(0xRRGGBB)`, constants
`BLACK WHITE RED GREEN BLUE YELLOW CYAN MAGENTA ORANGE PURPLE GRAY`.

**Primitives** — `pixel`, `hline`, `vline`, `line`, `rect`, `fill_rect`,
`circle`, `fill_circle`.

**Text** — `draw_ttf(x, y, text, color, font_path, size)`,
`draw_ttf_centered(y, …)`, `draw_ttf_right(y, …, padding)`,
`ttf_width(text, font_path, size)`, `ttf_height(font_path, size)`;
built-in-font shim `draw_text`, `draw_text_centered`, `draw_char`,
`draw_number`, `text_width` (route to an `Arial.fatlas` if found — see
`fonts/`).

**Images** — `draw_image_file_scaled(x, y, w, h, path)`,
`draw_image_file(x, y, path)`, `draw_image_file_scaled_rotated(…)`,
`get_image_info(path) -> (w, h)`, plus the handle-based
`load_image`/`draw_image`/`draw_image_scaled` for API compatibility.

**Input** — `wait_button() -> bit`, `poll_input() -> (held, pressed, released)`,
`get_input_event() -> (button, type, ms) | None`, `has_input_events()`,
`peek_buttons()`, `clear_input_events()`. Button bits: `BTN_UP BTN_DOWN BTN_LEFT
BTN_RIGHT BTN_A BTN_B BTN_POWER`; event types `EVENT_PRESS` / `EVENT_RELEASE`.

**Sound / haptics** — `beep(freq, duration_ms)`,
`play_rtttl(melody, mode=None)`, `play_rtttl_sync(…)`, `vibrate(duration_ms)`,
`vibrate_pattern(rtttl)`. RTTTL modes: `RTTTL_SOUND_ONLY`,
`RTTTL_SOUND_VIBRATE`, `RTTTL_VIBRATE_ONLY`.

**LEDs** — `led_rgb(dir, r, g, b)` / `led_dpad(dir, 0xRRGGBB)` for the D-pad,
`led_set(name, 0..255)` for `a-button-led` / `b-button-led`, `led_all_off()`,
and `led_status(state[, pattern])` for the firmware status LED.

**Backlight / power** — `set_brightness(0..100)`, `get_brightness()`,
`get_max_brightness()`, `screen_off()`, `screen_on()`.

## How it works (hardware notes)

- **Display:** after `UI_TAKEOVER`, the panel (an ST7796 over SPI) only refreshes
  on the **`write()` syscall path** — `mmap` writes update framebuffer memory but
  never reach the panel. `pagerctl.py` draws into an in-memory native buffer and
  `flip()` does one `os.pwrite(fd, buf, 0)`. The buffer is also reversed at flip
  (a 180° rotation) to match the panel's physical orientation.
- **Takeover scope:** `UI_TAKEOVER` grabs **display + input** only; the firmware
  still owns the buzzer, vibrator, and backlight. That's why sound/haptics go
  through the `RINGTONE`/`VIBRATE` commands (driving the vibrator gpio directly
  can latch the motor on), while per-LED control is safe over sysfs as long as
  you don't also issue `DPADLED`/`LED` commands.
- **Input:** buttons are a standard Linux evdev device (`/dev/input/event0`,
  `EV_KEY`), read directly for low latency and full press/release/held state —
  including the POWER button, which the blocking `WAIT_FOR_INPUT` command does not
  report.
- **Brightness** is the one system `/sys/class/backlight` node, shared with the
  OS — setting it is exactly what the OS does, so there's no divergence.

## Environment variables

- `PAGERCTL_FAKE=1` — run off-device against an in-memory framebuffer. Takeover,
  input, sound, and LEDs become no-ops; `flip()` writes to `self._surface` (native
  RGB565) so you can snapshot a render on your computer.
- `PAGERCTL_EXTERNAL_TAKEOVER=1` — the launcher already holds `UI_TAKEOVER` for the
  whole session, so `init()`/`cleanup()` skip their own takeover/release. Set this
  (and run `UI_TAKEOVER` once in your `payload.sh`) when one program launches
  another, to avoid the screen flickering as processes hand off.

## Limitations

- No runtime font rasterization or image decode on the device — build assets
  ahead of time. `draw_image_file_scaled` returns `-1` for a size you didn't
  prewarm.
- Rotations **0** and **270** are tested; **90/180** are best-effort and need
  assets generated for them.
- `screenshot()` is a no-op on-device (read `/dev/fb0` externally to capture).

## Try it on the Pager (demo payload)

This repo **is** a loadable payload. Copy it to the Pager and run it from the
payload menu to see every feature — display, text, images, LEDs, sound,
vibration, brightness, and button input:

```sh
scp -r pagerctl.py root@<pager-ip>:/root/payloads/user/utilities/pagerctl_demo
# then on the Pager: Utilities -> PagerCtl Demo
```

`payload.sh` installs `python3` from opkg on first run if needed (one time,
internet required), then runs `demo.py`. Press **GREEN (A)** to advance through
the sections; the last section is a button test — press every button.

## Repository layout

```
payload.sh             payload entry point (bootstraps python3, runs the demo)
demo.py                the feature demo
pagerctl.py            the library — drop this into your own payload
tools/build_assets.py  host-side asset builder (needs Pillow + NumPy)
fonts/                 Arial (built-in-font shim) + Roboto/PressStart2P for the demo
                       (.ttf sources + prebuilt .fatlas atlases)
images/                demo test image (+ prebuilt .r565 and prewarm/ blobs)
```

To use the library in your **own** payload, you only need `pagerctl.py` (plus
`tools/build_assets.py` on your computer to build your fonts/images).

## Credits

Created by **brAinphreAk**. A pure-Python successor to the Pager's
`libpagerctl.so`, built for the no-binaries payload workflow.
