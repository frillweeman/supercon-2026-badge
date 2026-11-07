#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=0.22"]
# ///
"""Mac side of the badge's Keynote Remote app (apps/keynote.py).

Finds the badge over BLE, turns its Prev/Next button notifications into Keynote
AppleScript commands, and writes the slide state (playing, current, total) back so the
badge can show it. Host-side only; never synced to the badge.

    tools/keynote_bridge.py

First run: macOS asks for Bluetooth permission (for the terminal app) and for
Automation permission to control Keynote. Never launches Keynote by itself.
"""

import asyncio
import re
import struct

from bleak import BleakClient, BleakScanner

SVC_UUID = "9f1c0001-4b1d-4e6f-8a52-4b6579524d31"
BTN_UUID = "9f1c0002-4b1d-4e6f-8a52-4b6579524d31"
STATE_UUID = "9f1c0003-4b1d-4e6f-8a52-4b6579524d31"
NAME_UUID = "9f1c0004-4b1d-4e6f-8a52-4b6579524d31"
ROOT_UUID = "9f1c0005-4b1d-4e6f-8a52-4b6579524d31"
NAME_MAX = 20 # bytes; fits one ATT write at the default MTU
POLL_S = 0.5

STATE_SCRIPT = """
if application "Keynote" is running then
  tell application "Keynote"
    if (count of documents) is 0 then return "0,0,0|"
    set docname to name of front document
    set n to count of slides of front document
    if playing then
      set cur to slide number of current slide of front document
      return "1," & (cur as text) & "," & (n as text) & "|" & docname
    end if
    return "0,0," & (n as text) & "|" & docname
  end tell
end if
return "0,0,0|"
"""

# Keynote's `show next` / `show previous` move whole slides and skip builds. The arrow keys
# are what a real clicker sends: they step builds first, then slides. So while presenting we
# press Right/Left (key codes 124/123) via System Events, which needs Accessibility
# permission for the terminal app and a frontmost Keynote.
def _key_script(code: int, start_if_idle: bool) -> str:
    idle = (
        "tell application \"Keynote\" to if (count of documents) > 0 then start front document"
        if start_if_idle else "return"
    )
    return f"""
if application "Keynote" is running then
  tell application "Keynote"
    set isPlaying to playing
  end tell
  if isPlaying then
    tell application "System Events"
      if not (frontmost of process "Keynote") then set frontmost of process "Keynote" to true
      key code {code}
    end tell
  else
    {idle}
  end if
end if"""


NOTES_SCRIPT = """
if application "Keynote" is running then
  tell application "Keynote"
    try
      if playing then return (presenter notes of current slide of front document) as text
    end try
  end tell
end if
return ""
"""

ROOT_RE = re.compile(r"\broot\s*:\s*(\d+)", re.IGNORECASE)


def parse_root(notes: str) -> int | None:
    """Value of an optional `root:N` field in a slide's presenter notes (0-49 = SAO animation,
    50 = all off), or None if the slide has no valid field. `pic:` is reserved for later."""
    m = ROOT_RE.search(notes)
    if m and int(m.group(1)) <= 50:
        return int(m.group(1))
    return None


CMD_SCRIPTS = {
    b"N": _key_script(124, True),  # not presenting: start the show instead of stepping
    b"P": _key_script(123, False),
    # Custom commands from the badge: b"1" = B1, b"2" = B2.
    b"1": 'do shell script "open https://cornhub.website"',
    b"2": 'do shell script "open https://willsploit.com"',
}
CMD_NAMES = {b"N": "next", b"P": "prev", b"1": "B1 (cornhub)", b"2": "B2 (web)"}


async def osa(script: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode:
        print("osascript:", err.decode().strip())
    return out.decode().strip()


def clip(name: str) -> bytes:
    """UTF-8, at most NAME_MAX bytes, cut on a character boundary (with .. if shortened)."""
    raw = name.encode()
    if len(raw) <= NAME_MAX:
        return raw
    return raw[: NAME_MAX - 2].decode(errors="ignore").encode() + b".."


async def read_state() -> tuple[tuple[int, int, int], bytes]:
    try:
        nums, _, name = (await osa(STATE_SCRIPT)).partition("|")
        playing, cur, total = (int(x) for x in nums.split(","))
        return (playing, cur, total), clip(name)
    except ValueError:
        return (0, 0, 0), b""


async def session(device) -> None:
    lock = asyncio.Lock()  # one AppleScript command at a time, in press order
    state_changed = asyncio.Event()

    def on_button(_char, data: bytearray) -> None:
        cmd = bytes(data[:1])
        if cmd in CMD_SCRIPTS:
            asyncio.get_running_loop().create_task(run_cmd(cmd))

    async def run_cmd(cmd: bytes) -> None:
        async with lock:
            print("badge:", CMD_NAMES[cmd])
            await osa(CMD_SCRIPTS[cmd])
        state_changed.set()  # push the new slide right away instead of waiting for the poll

    async with BleakClient(device) as client:
        print("connected to", device.name or device.address)
        await client.start_notify(BTN_UUID, on_button)
        last = last_name = None
        last_slide = None  # slide whose notes we last read; None whenever not presenting
        while client.is_connected:
            state, name = await read_state()
            slide = state[1] if state[0] else None
            if slide != last_slide:
                last_slide = slide
                if slide is not None:
                    root = parse_root(await osa(NOTES_SCRIPT))
                    if root is not None:  # no field on this slide: leave the lights alone
                        print("slide", slide, "root:", root)
                        await client.write_gatt_char(ROOT_UUID, bytes((root,)), response=False)
            if name != last_name:  # name first so the badge never shows a stale deck
                await client.write_gatt_char(NAME_UUID, name or b"\0", response=False)
                last_name = name
            if state != last:
                await client.write_gatt_char(STATE_UUID, struct.pack("<BHH", *state), response=False)
                last = state
            try:
                await asyncio.wait_for(state_changed.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass
            state_changed.clear()
    print("disconnected")


async def main() -> None:
    print("looking for the badge (open Keynote Remote on it)...")
    while True:
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: SVC_UUID in [u.lower() for u in ad.service_uuids], timeout=10
        )
        if device is None:
            continue
        try:
            await session(device)
        except Exception as ex:  # dropped link, badge left the app, etc.
            print("session ended:", ex)
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
