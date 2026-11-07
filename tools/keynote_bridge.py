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
import struct

from bleak import BleakClient, BleakScanner

SVC_UUID = "9f1c0001-4b1d-4e6f-8a52-4b6579524d31"
BTN_UUID = "9f1c0002-4b1d-4e6f-8a52-4b6579524d31"
STATE_UUID = "9f1c0003-4b1d-4e6f-8a52-4b6579524d31"
NAME_UUID = "9f1c0004-4b1d-4e6f-8a52-4b6579524d31"
NAME_MAX = 20  # bytes; fits one ATT write at the default MTU
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

CMD_SCRIPTS = {
    b"N": """
if application "Keynote" is running then
  tell application "Keynote"
    if playing then
      show next
    else if (count of documents) > 0 then
      start front document
    end if
  end tell
end if""",
    b"P": """
if application "Keynote" is running then
  tell application "Keynote"
    if playing then show previous
  end tell
end if""",
}


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
            print("badge:", "next" if cmd == b"N" else "prev")
            await osa(CMD_SCRIPTS[cmd])
        state_changed.set()  # push the new slide right away instead of waiting for the poll

    async with BleakClient(device) as client:
        print("connected to", device.name or device.address)
        await client.start_notify(BTN_UUID, on_button)
        last = last_name = None
        while client.is_connected:
            state, name = await read_state()
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
