"""Item 29 on the rig: one boot, real scsynth, then quit.

Headless suites can only prove the drone reacts to messages we invented.
This proves it against a real server: that POWER actually opens a real
envelope, that a drone and a poly on one source both sound, and — the one
thing only the rig can show — that drone spawn/dispose cycles do not leave
synths running (the 07-26 satellite leak, drone-shaped).

    python tests/rig_item29_drone.py

MAC-ONLY, MANUAL — like diag_*/hear_check, not something CI can run: it
boots a real scsynth and makes real sound. Deliberately NOT named test_*.

Boots output-only per the p39 recipe: the output-only device named for BOTH
-H slots plus -i 0, which sidesteps the microphone authorization that
stalls CoreAudio from an agent shell. Master volume is kept at 0.10 —
assume someone is at the machine. Set DEV to an output-only device; do not
point it at whatever the human is listening through.
"""

import socket
import struct
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import functools                                       # noqa: E402
import synthbase.app as appmod                          # noqa: E402
from synthbase.app import SynthApp                       # noqa: E402
from synthbase.allocation import midi_to_freq            # noqa: E402
from synthbase.engine import Engine as _RealEngine       # noqa: E402

DEV = "MacBook Pro Speakers"       # output-only at 48 kHz; NOT Cole's AirPods

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra else ""))
    if not cond:
        FAILURES.append(name)


def synth_count(port, timeout=2.0):
    """Ask the live scsynth how many synth nodes it has (/status over OSC)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"/status\x00,\x00\x00\x00", ("127.0.0.1", port))
        data, _ = sock.recvfrom(8192)
    finally:
        sock.close()
    i = data.index(b",")
    tags = data[i:data.index(b"\x00", i)].decode()[1:]
    off = i + ((len(tags) + 1 + 4) & ~3)
    vals = []
    for t in tags:
        if t == "i":
            vals.append(struct.unpack(">i", data[off:off + 4])[0]); off += 4
        elif t == "f":
            vals.append(struct.unpack(">f", data[off:off + 4])[0]); off += 4
        elif t == "d":
            vals.append(struct.unpack(">d", data[off:off + 8])[0]); off += 8
    return vals[2]          # (unused, ugens, SYNTHS, groups, defs, ...)


def live_controls(app, vid, *names):
    """Read the drone's OWN slot off the running scsynth (/s_get).

    The default patch wires keys->arp->voice, so the primary mono voice
    already holds slot 0 and a drone card lands on a SATELLITE — a synth
    with no Instance and no entry in rack.instances. Reading
    `inst.settings` therefore reports the MONO VOICE, not the drone, which
    is the trap the first run of this probe fell into. Go to the server.
    """
    slot = app.voices[vid]._slots[0]
    if slot.index == 0:                       # primary: the instance IS it
        inst = app.rack.find(app.voices[vid].target_key)
        return {n: inst.settings.get(n) for n in names}
    got = slot.node.get(*names)
    return {n: got.get(n) for n in names}


def main():
    # the p39 boot recipe, probe-local: the output-only device in BOTH -H
    # slots and `-i 0`. app.start() doesn't thread input_channels through, and
    # this is a probe, not a reason to change production.
    appmod.Engine = functools.partial(
        _RealEngine, input_device=DEV, output_device=DEV, input_channels=0)
    app = SynthApp(use_midi=False, use_reload=False)
    app.start("bells")
    port = app.engine.options.port
    try:
        app.set_volume(0.10)                  # Cole is at the machine
        app.set_transport(playing=True)
        time.sleep(0.4)
        base = synth_count(port)
        check("scsynth reachable", base > 0, f"{base} synths")

        # ---- 1. POWER opens a real envelope -------------------------------
        vid = app.spawn_drone_voice()
        app.set_ctl_wire("add", "keys", vid)
        check("drone voice spawned", vid == "hold", f"target={app.voices[vid].target_key}")
        app.note_on(45)                        # A2
        app.set_drone_power(vid, True)
        time.sleep(1.2)                        # <-- audible: a held A2 drone
        inst = app.rack.find(app.voices[vid].target_key)
        slot = app.voices[vid]._slots[0]
        print(f"[rig] drone rides slot {slot.index} "
              f"({'the target instance' if slot.index == 0 else 'a satellite'})")
        c = live_controls(app, vid, "gate", "freq")
        check("POWER opened the drone's gate on the live server",
              c["gate"] == 1, f"gate={c['gate']}")
        check("the root is the note we played",
              abs(c["freq"] - midi_to_freq(45)) < 0.01, f"{c['freq']:.2f} Hz")

        # ---- 2. note-off HOLDS, transport stop silences -------------------
        app.note_off(45)
        time.sleep(0.5)
        c = live_controls(app, vid, "gate", "freq")
        check("note-off HOLDS the root (a drone has no release)",
              abs(c["freq"] - midi_to_freq(45)) < 0.01, f"{c['freq']:.2f} Hz")
        check("...and never closed the gate", c["gate"] == 1)
        app.set_transport(playing=False)
        time.sleep(0.5)
        check("transport stop silences the drone (item 32)",
              live_controls(app, vid, "gate")["gate"] == 0)
        app.set_transport(playing=True)
        time.sleep(0.5)
        check("play brings it back", live_controls(app, vid, "gate")["gate"] == 1)

        # ---- 3. POWER from a binary wire (logic input) --------------------
        bid = app.spawn_button()
        taps = []
        app.on_midi_event = taps.append
        # the button sits lo, so the wire's FIRST SIGHT is itself a level-in
        # application: adding it must power the drone down
        app.set_ctl_wire("add", bid, f"{vid}:pwr")
        time.sleep(0.5)
        g = live_controls(app, vid, "gate")["gate"]
        check("a binary wire powers the drone DOWN (first sight)",
              g == 0 and app._drone_powers[vid] is False, f"gate={g}")
        check("...and emits the falling level tap the card needs",
              any(t.get("kind") == "level" and t.get("ep") == f"{vid}:pwr"
                  and t.get("on") is False for t in taps))
        taps.clear()
        app.buttons[bid].press()               # momentary: level hi
        time.sleep(0.5)
        g = live_controls(app, vid, "gate")["gate"]
        check("a binary wire powers it back UP",
              g == 1 and app._drone_powers[vid] is True, f"gate={g}")
        check("...with a rising level tap too",
              any(t.get("kind") == "level" and t.get("ep") == f"{vid}:pwr"
                  and t.get("on") is True for t in taps))
        check("POWER never bypassed the target module", inst.enabled)
        taps.clear()
        app.buttons[bid].release()
        time.sleep(0.5)
        check("release closes it again (a falling edge, not just a rising one)",
              live_controls(app, vid, "gate")["gate"] == 0)
        app.set_ctl_wire("remove", bid, f"{vid}:pwr")
        app.set_drone_power(vid, True)         # back on for the sharing test
        time.sleep(0.3)

        # ---- 4. a drone and a poly SHARING one source ---------------------
        # Cut keys->drone first. Both were wired to `keys`, so the chord fed
        # the DRONE too and last-note priority correctly walked its root up
        # to the top note — right behaviour, wrong patch for this question.
        # This is how you'd really patch it: a deriver or a held root feeds
        # the drone, the keys feed the poly. Cutting the wire also exercises
        # hold-on-empty — the drone keeps the root it was left on.
        app.set_ctl_wire("remove", "keys", vid)
        with_drone = synth_count(port)
        pid = app.spawn_poly(4)
        app.set_ctl_wire("add", "keys", pid)
        for n in (52, 57, 61):                 # a chord over the drone
            app.note_on(n)
        time.sleep(1.5)                        # <-- audible: chord + drone
        check("poly and drone target the same source",
              app.voices[pid].target_key == app.voices[vid].target_key,
              app.voices[pid].target_key)
        check("poly is sounding all three notes", len(app.voices[pid]._sounding) == 3)
        c = live_controls(app, vid, "gate", "freq")
        check("the drone still holds its own root under the chord",
              abs(c["freq"] - midi_to_freq(45)) < 0.01, f"{c['freq']:.2f} Hz")
        check("...and its gate is still open", c["gate"] == 1)
        app.set_drone_power(vid, False)
        time.sleep(0.6)                        # <-- audible: chord alone
        check("powering the drone down closes only the DRONE's gate",
              live_controls(app, vid, "gate")["gate"] == 0)
        check("...and leaves the poly sounding",
              len(app.voices[pid]._sounding) == 3 and inst.enabled)
        for n in (52, 57, 61):
            app.note_off(n)
        app.set_drone_power(vid, True)
        time.sleep(0.4)

        # ---- 5. THE LEAK, drone-shaped ------------------------------------
        # 4a: settle, then measure across five spawn/dispose cycles.
        app.remove_voice(pid)
        app.set_drone_power(vid, False)
        app.remove_voice(vid)
        time.sleep(0.6)
        settled = synth_count(port)
        counts = []
        for _ in range(5):
            v = app.spawn_drone_voice()
            app.set_ctl_wire("add", "keys", v)
            app.note_on(45)
            app.set_drone_power(v, True)
            time.sleep(0.25)
            app.set_drone_power(v, False)
            app.remove_voice(v)
            time.sleep(0.25)
            counts.append(synth_count(port))
        check("five drone spawn/dispose cycles leave the node count flat",
              counts == [settled] * 5, f"settled={settled} cycles={counts}")

        # 5b: the same, with a mono voice holding slot 0 so the drone is a
        # SATELLITE — the case an unforced free() silently leaves running.
        app.spawn_voice()
        time.sleep(0.3)
        settled2 = synth_count(port)
        counts2 = []
        for _ in range(5):
            v = app.spawn_drone_voice()
            app.set_ctl_wire("add", "keys", v)
            app.note_on(45)
            app.set_drone_power(v, True)
            time.sleep(0.25)
            app.set_drone_power(v, False)
            app.remove_voice(v)
            time.sleep(0.25)
            counts2.append(synth_count(port))
        check("...and flat again when the drone rides a SATELLITE slot",
              counts2 == [settled2] * 5, f"settled={settled2} cycles={counts2}")

        # ---- 6. the drone MODULE path (the _DroneSink swap) ---------------
        app.spawn_unconnected("drone")
        did = [i.key for i in app.rack.instances if i.type == "drone"][0]
        app.set_transpose(2)
        app.set_ctl_wire("add", "keys", did)
        app.note_on(36)
        time.sleep(0.8)                        # <-- audible: a low drone
        dinst = app.rack.find(did)
        check("a drone MODULE still retargets from the note stream",
              dinst.settings.get("freq", 0) > 0, f"{dinst.settings.get('freq'):.2f} Hz")
        check("...and now follows global transpose (+2)",
              abs(dinst.settings.get("freq", 0) - midi_to_freq(38)) < 0.01,
              f"want {midi_to_freq(38):.2f}")
        check("no phantom gate param on the gateless drone module",
              "gate" not in dinst.settings)
        app.set_transpose(0)
        app.note_off(36)
    finally:
        print("\n[rig] tearing down")
        app.stop()
        time.sleep(0.5)

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}: " + ", ".join(FAILURES))
        return 1
    print("PASS — item 29 live on the rig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
