# Supercon badge: filesystem dump and hacking notes

A copy of the MicroPython filesystem from a Hackaday Supercon badge ("Billboard Badge": ESP32-S3,
960x672 red/black e-paper, 8 WS2812 LEDs, 3 SAO ports), plus notes for hacking on it.
The badge is the source of truth. This repo is a snapshot you edit and copy back.

See `CLAUDE.md` for the detailed hardware pin map, how the launcher works, known oddities and open questions.

## Prerequisites (macOS)

```sh
uv tool install esptool pyserial mpremote   # or: pipx install ...
```

Tools land in `~/.local/bin`. The badge shows up as a USB serial port, `/dev/cu.usbmodem1101` on the machine
this was dumped from. Yours may differ: run `ls /dev/cu.usb*`.
Only one program can hold the port at a time (close `screen`/miniterm before running `mpremote`).

## Watch the console

```sh
pyserial-miniterm /dev/cu.usbmodem1101 115200   # exit with Ctrl-]
```

The badge prints app discovery, button selections and a 60 s heap report. Opening the port may print nothing
until the next reset or button press.

## Look around (read-only)

```sh
mpremote connect /dev/cu.usbmodem1101 fs ls :apps
mpremote connect /dev/cu.usbmodem1101 repl      # Ctrl-C interrupts the running app, Ctrl-X exits
mpremote connect /dev/cu.usbmodem1101 reset     # restart the badge
```

Interrupting the app pauses the badge UI until you reset.

## Write your own app

Apps are `BaseApp` subclasses in `apps/`. They are discovered automatically at boot; there is no registration step.

1. `cp apps/template.py apps/myapp.py`, rename the class and set `self.name`.
   (`template.py` itself is never loaded by the launcher.)
2. Copy it to the badge:
   `mpremote connect /dev/cu.usbmodem1101 fs cp apps/myapp.py :apps/myapp.py`
3. Reset and check the console for `Added app: <your name>`.
4. Use the menu +/- buttons to select it and a User button to launch.
   (The code notes that plus/minus are mislabeled on the silkscreen.)

This workflow has not been run end-to-end on the badge yet.

## Sync to the badge

`tools/sync.py` makes the badge match this directory (create, update, delete), transferring only files whose SHA-256 differs.

```sh
tools/sync.py                  # dry run: show what would change
tools/sync.py --apply          # make the changes
tools/sync.py --apply --reset  # ...and reset the badge afterwards
tools/sync.py --no-delete      # never delete files that exist only on the badge
tools/sync.py --port /dev/cu.usbmodemXXXX
```

- Syncs `main.py`, `boot.py`, `apps/`, `lib/`, `data/`. Skips `data/panel_config.json`, `data/user_apps.py` and `data/app_list`, which the badge rewrites on every boot.
- Reading the badge interrupts the running app (no reset). Use `--reset` or press the reset button afterwards.
- Not yet run against real hardware.

## Re-dump (badge -> this repo)

```sh
mpremote connect /dev/cu.usbmodem1101 fs cp -r : dump/
```

This took a long time over serial (the PNG assets are large). Expect `data/panel_config.json` to differ
between dumps, because `main.py` increments `load_counter` on every boot.

## Before you experiment further

A full flash backup (bootloader, partition table, firmware, filesystem) has **not** been taken. This repo only
has the Python filesystem. To take one: put the badge in download mode if needed, then
`esptool read-flash 0 ALL badge_full.bin`. Keep that file outside this repo (it is large).

## Layout

| Path | What |
|---|---|
| `main.py`, `boot.py` | Entry points. `main.py` starts button, display, LED and app-switcher tasks. |
| `apps/` | Built-in apps: schedule (Workshops, Sat/Sun Talks), Gallery, Nametag. |
| `lib/` | Drivers and launcher (EPD, LEDs, buttons, SAO, app switcher, PNG decoding). |
| `data/` | Images, `schedule.csv`, EPD waveform tables, config. `user_apps.py` and `app_list` are regenerated at boot. |
