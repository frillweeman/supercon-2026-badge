# Keynote Remote

Use the badge as a Bluetooth clicker for Apple Keynote on a Mac.

- **Right Button** = next slide. If Keynote isn't presenting, it starts the show.
- **Left Button** = previous slide.
- The **LED bar** shows where you are in the deck.
- The e-paper shows the name of the open Keynote file so you can check you're driving the right deck.

There are two parts:

| Part | Where it runs | File |
|---|---|---|
| Badge app | on the badge | `apps/keynote.py` (plus a small `on_exit` hook in `lib/app_switcher.py`) |
| Mac bridge | on the Mac running Keynote | `tools/keynote_bridge.py` |

The bridge is a plain Python script. It needs macOS (it drives Keynote with AppleScript).

## Install on a Mac

1. **Install `uv`** (one time). It runs the script and fetches its one dependency (`bleak`) on its own:

   ```sh
   brew install uv        # or: curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Get the script.** Clone this repo, or copy just `tools/keynote_bridge.py` to the Mac:

   ```sh
   git clone https://github.com/frillweeman/supercon-2026-badge.git
   cd supercon-2026-badge
   ```

3. **Run it:**

   ```sh
   uv run --script tools/keynote_bridge.py
   ```

   The first run downloads `bleak` (a few seconds). It prints `looking for the badge...` and keeps retrying, so you
   can start it before or after the badge app.

4. **Approve the macOS prompts** the first time (for whichever app is running the script, e.g. Terminal or iTerm):
   - **Bluetooth**: needed to find and talk to the badge.
   - **Automation, "control Keynote"**: needed to move slides and read the slide number.

   If you missed a prompt, enable it under System Settings > Privacy & Security > Bluetooth / Automation.

Leave the script running in a terminal while you present. `Ctrl-C` stops it.
The bridge never launches Keynote by itself.

## Install on the badge

The badge needs the app file and the one-line launcher hook. From the repo root, with the badge plugged in over USB:

```sh
tools/sync.py                  # dry run: shows what will change
tools/sync.py --apply --reset  # push to the badge and reboot it
```

(Or by hand: `mpremote fs cp apps/keynote.py :apps/keynote.py` and `mpremote fs cp lib/app_switcher.py :lib/app_switcher.py`, then reset.)
See the main `README.md` for `mpremote` setup. Close any serial monitor first, since only one program can hold the badge's USB port.

## Using it

1. Open the deck in Keynote (any document; the bridge follows the front one).
2. Start the bridge on the Mac.
3. On the badge, press the menu buttons until **Keynote Remote** is selected (it comes right after Gallery), then press a User button.
4. Wait for solid green LEDs. The screen draws once and shows the deck name.
5. Press **Right Button** to start the show, then click through.

To leave, press a menu button. Bluetooth is turned off when you leave the app.

### What the LEDs mean

| LEDs | Meaning |
|---|---|
| Orange chase | Loading: no Mac connected yet, or connected but no Keynote document open |
| Solid green | Ready (connected, document open, not presenting). Green is used for nothing else |
| One bright LED on a dim row | Presenting. LED *i* is slide `lap*8 + i + 1`; the bright one is the current slide |
| Bright LED colour | Which lap of 8 slides: blue (1-8), magenta (9-16), white (17-24), then it repeats |
| Dim LED | A slide exists at that spot in this lap |
| Off LED | No slide there (past the end of the deck) |

### What the screen does

The e-paper refreshes slowly and flickers, so the app barely touches it:

- Nothing is drawn when the app opens.
- It draws once, when a Mac is connected **and** has a Keynote document open.
- It redraws only if the front document changes. Slide changes and disconnects never redraw.

The deck name is cut to 20 bytes (with a trailing `..`).

## Troubleshooting

- **Bridge never finds the badge.** Make sure the badge is in the Keynote Remote app (orange chase = advertising), Mac Bluetooth is on, and the terminal app has Bluetooth permission.
- **Buttons do nothing.** Check the bridge terminal: each press prints `badge: next` / `badge: prev`. If nothing prints, the connection dropped; leave and re-enter the badge app. If it prints but Keynote doesn't move, check the Automation permission.
- **Orange never turns green.** The Mac is connected but the bridge sees no open Keynote document.
- **Screen still shows the previous app.** That's intended until the first connected-with-a-deck draw.
- **Several badges nearby.** The bridge connects to the first badge it sees running this app. Run the app on one badge at a time.

## Limitations

- macOS and Keynote only.
- No pairing or authentication. Any computer running the bridge near a badge running the app can connect to it.
- Presenter notes are not supported yet.
- Slide numbers are limited to 65535 and deck names to 20 bytes.

## How it works

The badge is a BLE peripheral advertising one GATT service. The bridge is the BLE central.

| Characteristic | UUID | Direction | Payload |
|---|---|---|---|
| Buttons | `9f1c0002-4b1d-4e6f-8a52-4b6579524d31` | badge -> Mac (notify) | `b"N"` next, `b"P"` previous |
| State | `9f1c0003-4b1d-4e6f-8a52-4b6579524d31` | Mac -> badge (write) | `struct "<BHH"`: presenting (0/1), current slide, slide count |
| Deck name | `9f1c0004-4b1d-4e6f-8a52-4b6579524d31` | Mac -> badge (write) | UTF-8 name of the front document, max 20 bytes |

The service UUID is `9f1c0001-4b1d-4e6f-8a52-4b6579524d31`, the same base as the characteristics above.

The bridge polls Keynote about twice a second (and immediately after each command) with `osascript`, and writes state/name to the badge only when they change.
