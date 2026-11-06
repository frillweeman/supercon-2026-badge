# CLAUDE.md

Filesystem dump of a Hackaday Supercon badge ("Billboard Badge" in the code, "supercon-x" splash image).
This directory is a **read-only copy of the badge's MicroPython filesystem**, pulled over USB serial with
`mpremote fs cp -r : dump/`. The badge itself is the source of truth; edit files here, then copy them back.

Legend: **[code]** = read directly from files in this dump. **[inferred]** = my reasoning, not verified on hardware.
See "Open questions" at the bottom before relying on any [inferred] item.

## Hardware (from lib/*.py)

- MCU: ESP32 family, running MicroPython (`sys.platform == "esp32"`, `btree`, `asyncio`, `machine.I2CTarget` referenced in docs link to MicroPython v1.27). Exact chip/PSRAM **not confirmed** — see open questions. [inferred: ESP32-S3, because GPIO numbers up to 48 are used and boot log shows a ~6.4 MB "max new split" heap]
- Display: 960x672 e-paper, black + red framebuffers, two controllers (CS1/CS2) each driving half the width. Driver in `lib/epd.py` is a port of Pervasive Displays `EPD_Driver_GU_large`. Uses **SoftSPI** at 8 MHz. LVGL draws into it.
- LEDs: 8x WS2812 on GPIO 38 (`lib/led_bar.py`, `NUM_LEDS = 8`).
- Buttons (active low, pull-up) in `lib/button.py`. Note the code says **plus/minus are mislabeled on the silkscreen**:
  - `MenuMinus` GPIO40, `MenuPlus` GPIO15, `UserA` GPIO39, `UserB` GPIO16, `Boot` GPIO0
- SAO connectors (`lib/sao.py`): I2C bus 0, SCL=18, SDA=17, 400 kHz. GPIO pairs: SAO1=(2,42), SAO2=(4,5), SAO3=(7,6).
- Misc (`lib/board.py`): status LED GPIO1 (active low), battery/USB voltage sense ADC on GPIO14 (1/2 divider), test points TP8=GPIO9 and TP9=GPIO41, UART on back test pads (code has a TODO: UART(0) invalid, UART(1) conflicts with the EPD).
- EPD pins (`lib/epd.py`): CS1=13, CS2=3, BUSY1=12, BUSY2=8, DC=10, RST=11, SCL=21, SDA=47, MISO=45 (unused), power enable=48.
- Free-looking pins are scarce: anything above is already in use. Do not reuse them for add-ons without checking.

## Software layout

| Path | Role |
|---|---|
| `boot.py` | Empty (comments only; `webrepl` lines are commented out). |
| `main.py` | Starts 4 asyncio tasks: button scan, display, LED update, app switcher. Prints a 60 s heartbeat with `micropython.mem_info()` (this is what you see on the serial console). |
| `apps/` | One file per app. Auto-discovered. |
| `lib/` | Drivers + launcher: `epd.py`, `led_bar.py`, `button.py`, `sao.py`, `board.py`, `app_switcher.py`, `app_list.py`, PNG helpers (`PNGdecoder.py`, `large_png.py`), `zlib.py`, `json_configs.py`. |
| `data/` | Assets and state: `gallery/` (44 PNGs), `graphics/` (10 PNGs), `schedule.csv`, `rendered_luts.py` (EPD waveform tables), `panel_config.json`, `user_apps.py`, `app_list`. |

Built-in apps: Workshops, Sat Talks, Sun Talks (all from `apps/schedule.py`), Gallery, Nametag.

## How apps work [code]

- An app is a subclass of `apps.base_app.BaseApp`. The launcher instantiates it with no args at boot and calls `run()` repeatedly, sleeping `sleep_ms` between calls.
- `self.launched` is True for the first `run()` after the app is selected; use it for one-time drawing. It is reset to False after that cycle.
- `BaseApp.arun()` wraps `run()` in try/except and prints the traceback to serial, so an app exception should not kill the launcher.
- **Registration is automatic.** `lib/app_switcher.py` lists `/apps/*.py` at every boot, **rewrites `data/user_apps.py`** from that list, imports it, and instantiates every `BaseApp` subclass it finds in each module. So: drop a `.py` file in `apps/`, reset, done. Do not hand-edit `data/user_apps.py`; it is overwritten each boot.
- Skipped modules: names starting with `_`, and `__init__`, `base_app`, `template` (denylist). **`template.py` is never loaded**, so copy it to a new filename to make a real app.
- Class attribute `_abstract` on a class excludes it from instantiation.
- Menu: MenuPlus/MenuMinus cycle through apps; UserA/UserB selects. Pressing a menu button while in an app returns to the menu. `data/app_list` is a MicroPython `btree` database (key = app index byte, value = app name) rewritten by the switcher on boot — it is not a mystery blob.
- Button API: `Button.pressed()` returns True **once per press**; `pressed_ms()` gives hold time. Buttons are scanned every 1 ms.
- Display API used by apps: `display.clear()`, `display.draw_png(path)`, `display.update()`, plus LVGL (`import lvgl`) for text/layout. `display.lv_display.inv_p = 0` is used in apps to restrict refreshes to a dirty region. E-paper refresh is slow; avoid per-frame redraws.
- Fonts available via LVGL in this build include `font_montserrat_14/16/24/48` (seen in use). Others unverified.

## Writing and deploying an app

Untested workflow (the dump was read-only so far):

1. `cp apps/template.py apps/myapp.py`, rename the class, set `self.name`.
2. `mpremote connect /dev/cu.usbmodem1101 fs cp apps/myapp.py :apps/myapp.py`
3. `mpremote connect /dev/cu.usbmodem1101 reset`, then watch the console for `Added app: <name>`.

Tooling lives in `~/.local/bin` (`mpremote`, `esptool`, `espefuse`, `espsecure`, `pyserial-miniterm`; installed with `uv tool`).
The serial port is exclusive: only one program can hold `/dev/cu.usbmodem1101` at a time (a running `mpremote fs cp` blocks everything else).

## Gotchas and oddities noticed [code]

- `main.py` increments `load_counter` in `data/panel_config.json` on **every boot** (it is 5 in this dump, and our own resets contributed). At counter == 1 it shows the splash screen; at counter == 42 it tries to draw `/data/graphics/never-epd.png`. The file on disk is `never_epd.png` (underscore), so that path will likely fail at boot 42. Not verified on hardware. Don't reset 37 more times by accident unless you want to find out.
- `lib/base_app.py` is a different, older copy of `apps/base_app.py` (uses `first_launch` instead of `launched`, no `gc.collect()`). Apps import `apps.base_app`. I have not confirmed whether anything imports the `lib/` one.
- `apps/nametag.py` sets both name labels to `""`, so the nametag shows no name as shipped. Where the name is meant to come from is unknown.
- `data/graphics/SayItWithMe_blank.png` is not referenced by any `.py` file in this dump.
- `lib/sao.py` `Sao.set_address()` sets `self.address = 0` regardless of its `addr` argument. Looks like a bug; unverified.
- `apps/gallery.py` hardcodes `maximage = 44`; matches the 44 files present.
- Heap is nearly full at idle (about 1.52 MB of 1.58 MB used in the boot log), so large allocations in a new app may fail. Call `gc.collect()` and keep buffers small.
- No code in this dump uses `bluetooth`, `network`, or WiFi. `boot.py` has `webrepl` commented out only.

## Not in this dump

This is the Python filesystem only. It does **not** include the MicroPython firmware image, bootloader, partition table, or NVS. A full `esptool read-flash` backup has **not been taken yet**. Do that before any experiment that rewrites flash.

## Safety / working rules

- Treat the badge as the owner's own hardware at a conference that expects hacking. Stay on the badge and its SAO/test-pad interfaces.
- Prefer read-only commands. Ask before: writing to the badge's filesystem, running `esptool` write/erase commands, or anything that touches eFuses (`espefuse` is irreversible).
- Keep temporary files out of this directory.

## Open questions (answer these and I'll fold them into the file)

1. **Chip identity.** Is it an ESP32-S3 (with PSRAM)? `esptool chip-id` / `flash-id` has not been run. Everything above says "ESP32 family" for now.
2. **MicroPython version and build.** Check `import sys; print(sys.implementation, sys.version)` over the REPL. Needed to know which modules exist.
3. **Is BLE available?** `import bluetooth` has not been tried. Decides the Bluetooth ideas we discussed.
4. **Flash layout.** Partition table not read; filesystem type (littlefs vs FAT) and free space unknown.
5. **Nametag name.** Was the name supposed to be set at the conference (by a kiosk, by phone, by editing a file)? Do you know?
6. **Official docs.** Is there a Hackaday/Supercon badge repo or guide for this year's badge? I have only inferred behavior from this dump.
7. **Version control.** Should I `git init` this directory so changes are tracked? I have not.
