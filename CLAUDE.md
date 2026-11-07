# CLAUDE.md

Filesystem dump of a Hackaday Supercon badge ("Billboard Badge" in the code, "supercon-x" splash image).
This directory is a **read-only copy of the badge's MicroPython filesystem**, pulled over USB serial with
`mpremote fs cp -r : dump/`. The badge itself is the source of truth; edit files here, then copy them back.

Legend: **[code]** = read directly from files in this dump. **[inferred]** = my reasoning, not verified on hardware.
See "Open questions" at the bottom before relying on any [inferred] item.

## Hardware (from lib/*.py)

- MCU: **ESP32-S3 with octal SPIRAM**, confirmed by REPL probe: `_machine='Billboard Badge Generic ESP32S3 module with Octal-SPIRAM with ESP32S3'`. (Not yet cross-checked with `esptool chip-id`.)
- Firmware: MicroPython, `sys.implementation.version` = 1.24.1, custom ("dirty") build dated 2026-11-04. `sys.version` says `v1.20.0-2527.g44aa2b153.dirty`, which conflicts with 1.24.1; unresolved, trust 1.24.1 for now.
- Radios (REPL probe): `network` (WLAN STA and AP interfaces exist, both inactive at idle), `bluetooth`, `espnow`, `socket`, `webrepl` all import. The radio has **not** been switched on yet, so actual WiFi/BLE function and RAM headroom are untested.
- Not installed: `aioble`, `microdot`.
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
| `apps/` | One file per app. Auto-discovered. Menu order is alphabetical by module filename (`dir()` order); there is no explicit ordering. Filenames must be valid identifiers and not start with `_`, so a `0_` prefix would break the import for every app. |
| `lib/` | Drivers + launcher: `epd.py`, `led_bar.py`, `button.py`, `sao.py`, `board.py`, `app_switcher.py`, `app_list.py`, PNG helpers (`PNGdecoder.py`, `large_png.py`), `zlib.py`, `json_configs.py`. |
| `tools/` | Host-side scripts, **not synced to the badge**. `sync.py` pushes this dir to the badge (see below). |
| `data/` | Assets and state: `gallery/` (44 PNGs), `graphics/` (10 PNGs), `schedule.csv`, `rendered_luts.py` (EPD waveform tables), `panel_config.json`, `user_apps.py`, `app_list`. |

Built-in apps, in menu order: Gallery (`gallery.py`), Nametag (`nametag.py`), Workshops / Sat Talks / Sun Talks (all from `schedule.py`), Web Nametag (`webnametag.py`, last because it sorts last; set from a web page over Bluetooth LE). Default (index 0) is Gallery.

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
2. `tools/sync.py` (dry run), then `tools/sync.py --apply --reset`. Or copy one file by hand:
   `mpremote connect /dev/cu.usbmodem1101 fs cp apps/myapp.py :apps/myapp.py` then `... reset`.
3. Watch the console for `Added app: <name>`.

### `tools/sync.py` (local dir -> badge)

Creates, updates and deletes files on the badge so it matches this directory. Compares SHA-256 per file and only transfers differences. **Dry run by default**; `--apply` writes, `--no-delete` keeps remote-only files, `--reset` resets the badge afterwards, `--port` overrides `/dev/cu.usbmodem1101`.

- Scope: `main.py`, `boot.py`, `apps/`, `lib/`, `data/` only. Other badge files and local `CLAUDE.md`/`tools/`/`.git` are untouched.
- Never copied or deleted (the badge rewrites them): `data/panel_config.json` (boot counter), `data/user_apps.py`, `data/app_list`.
- Listing the badge uses `mpremote exec`, which interrupts `main.py`'s tasks without resetting (boot counter is not bumped). Reset afterwards.
- Writing to the badge still needs the owner's OK (see working rules): run the dry run, show the plan, then `--apply`.
- Status: written and syntax-checked, **not yet run against the badge**.
- `tools/preview.py [port]` serves `web/webnametag/` locally (default 8080) with a mock BLE badge, for browser/mobile styling checks. Host-side only; never synced.
- The tool is one-way. To pull the badge's state into this dir, use `mpremote fs cp -r : dump/` as before.

Tooling lives in `~/.local/bin` (`mpremote`, `esptool`, `espefuse`, `espsecure`, `pyserial-miniterm`; installed with `uv tool`).
The serial port is exclusive: only one program can hold `/dev/cu.usbmodem1101` at a time (a running `mpremote fs cp` blocks everything else).

## Web Nametag app (`apps/webnametag.py`) [code; protocol tested on host with mocks, **not yet on hardware**]

Bluetooth LE only (no WiFi). A web page hosted on **GitHub Pages** (`web/webnametag/`, deployed by `.github/workflows/pages.yml` on push to master; URL `https://frillweeman.github.io/supercon-2026-badge/`) talks to the badge via **Web Bluetooth**, which needs HTTPS and a supporting browser (Bluefy on iPhone; Safari/iOS has none; Chrome on Android/desktop works). `web/webnametag/bt-test.html` just prints whether `navigator.bluetooth` exists.
- **Identity:** advertises as `Badge-<BLE_NAME>` (`BLE_NAME` in untracked `data/webnametag_config.py`, see the `.example.py`), or `Badge-<last 2 MAC bytes>` if unset. The page's chooser lists devices by that name.
- **Pairing code:** random 4 digits, shown on the home screen, regenerated on BLE enable and on the right button. The page sends it first (`A`+code); everything else is refused until then; 3 wrong codes disconnect + 10 s lockout. A nuisance filter, not real security. The page remembers the code per badge name in `localStorage`.
- **Home screen:** QR + text of the page URL, the advertised name, the code. (An iPhone camera opens Safari, so open the link in Bluefy.)
- **GATT:** service `7b1f0001-5c0e-4b6e-9a3d-5e11c0de0001`; CTRL `...0002` (commands `A`,`N`,`P`,`S`,`E`), DATA `...0003` (photo bytes, gatts buffer in append mode), STATUS `...0004` (read+notify). Details in the module docstring; `web/webnametag/app.js` mirrors them. Photos go in windows of <=3840 bytes, each followed by `S` which the badge acks with its byte count (the 4096-byte DATA buffer can't overflow), then `E`.
- **Untested/assumed:** `gatts_set_buffer(..., append=True)` behaviour on this firmware, MTU negotiation under Bluefy (page uses the `chunk` size the badge reports, `mtu-3` capped at 244), real throughput, and RAM headroom with BLE active.
- `tools/preview.py` serves the page locally with `tools/mock_ble.js` (a fake `navigator.bluetooth`, code 1234) for UI work in desktop Chrome.
- Old WiFi/SoftAP version: git history (`ef208c9`).

## Gotchas and oddities noticed [code]

- `main.py` increments `load_counter` in `data/panel_config.json` on **every boot** (it is 5 in this dump, and our own resets contributed). At counter == 1 it shows the splash screen; at counter == 42 it tries to draw `/data/graphics/never-epd.png`. The file on disk is `never_epd.png` (underscore), so that path will likely fail at boot 42. Not verified on hardware. Don't reset 37 more times by accident unless you want to find out.
- `lib/base_app.py` is a different, older copy of `apps/base_app.py` (uses `first_launch` instead of `launched`, no `gc.collect()`). Apps import `apps.base_app`. I have not confirmed whether anything imports the `lib/` one.
- `apps/nametag.py` sets both name labels to `""`, so the nametag shows no name as shipped. Where the name is meant to come from is unknown.
- `data/graphics/SayItWithMe_blank.png` is not referenced by any `.py` file in this dump.
- `lib/sao.py` `Sao.set_address()` sets `self.address = 0` regardless of its `addr` argument. Looks like a bug; unverified.
- `apps/gallery.py` hardcodes `maximage = 44`; matches the 44 files present.
- Heap is nearly full at idle (about 1.52 MB of 1.58 MB used in the boot log), so large allocations in a new app may fail. Call `gc.collect()` and keep buffers small.
- No code in this dump uses `bluetooth`, `network`, or WiFi, even though the firmware includes them. `boot.py` has `webrepl` commented out only.

## Not in this dump

This is the Python filesystem only. It does **not** include the MicroPython firmware image, bootloader, partition table, or NVS. A full `esptool read-flash` backup has **not been taken yet**. Do that before any experiment that rewrites flash.

## Safety / working rules

- Treat the badge as the owner's own hardware at a conference that expects hacking. Stay on the badge and its SAO/test-pad interfaces.
- Prefer read-only commands. Ask before: writing to the badge's filesystem, running `esptool` write/erase commands, or anything that touches eFuses (`espefuse` is irreversible).
- Keep temporary files out of this directory.

## Answered since first draft

- Chip: ESP32-S3 + octal PSRAM (REPL probe). MicroPython 1.24.1 (see Hardware for the version-string mismatch).
- WiFi and BLE modules are present in the firmware (import only; radio untested).
- Repo: `git init` done, initial commit made.

## Idea under investigation (not implemented): set the nametag name from a web page

Feasible on paper. Preferred design: badge hosts its own WiFi AP (phone joins, opens `http://192.168.4.1`), tiny `asyncio.start_server` HTTP form, name saved with `lib.json_configs.append()` into `data/panel_config.json`, `apps/nametag.py` reads it on launch and redraws. Gate the AP behind a button-held "pairing mode" with a timeout rather than leaving it always on.
Risks: heap is ~96% full at idle and the WiFi stack needs RAM (biggest unknown, test by starting the AP briefly and comparing `gc.mem_free()`); SoftSPI e-paper redraws block the event loop; font may only cover ASCII; Web Bluetooth alternative works on Android Chrome only (no `aioble` installed).

## Open questions (answer these and I'll fold them into the file)

1. **Flash layout.** Partition table not read; filesystem type (littlefs vs FAT) and free space unknown.
2. **Nametag name.** Was the name supposed to be set at the conference (kiosk, phone, editing a file)? Unknown.
3. **Official docs.** Is there a Hackaday/Supercon repo or guide for this year's badge? Everything here is inferred from the dump.
4. **Does WiFi actually run?** Radio on, AP up, RAM left? Needs a short non-persistent test.
5. **Version string mismatch** (1.24.1 vs v1.20.0 in `sys.version`).
6. **Full-flash backup** (`esptool read-flash`) still not taken.
