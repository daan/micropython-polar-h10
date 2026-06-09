"""
dashboard_cores3.py - M5Stack CoreS3 heart-rate + breathing dashboard.

Boots straight into a live display of a worn Polar H10:
    - big heart-rate number with a heart that pulses at the measured rate
    - breaths/min (derived from chest accel, see polar_h10.resp)
    - a scrolling breathing waveform

Deploy as the board's /flash/main.py. Drawing uses M5.Display (the proven API on
M5Stack MicroPython - NOT M5.Lcd). Tap the screen within 3s of boot, or any time
while running, to drop to the REPL (so you're never locked out).

Device-only: needs the M5 firmware, aioble, and the polar_h10 package on /flash/libs.

Breathing is an ESTIMATE from chest motion - good sitting still, unreliable when
you move. Not a medical measurement.
"""

import sys
import time
import asyncio

import M5
from M5 import *

from polar_h10.central import find_h10, PolarH10
from polar_h10.resp import RespirationEstimator

# --- colours (RGB888; LovyanGFX converts) ---
BLACK = 0x000000
WHITE = 0xFFFFFF
RED = 0xFF0000
CYAN = 0x00FFFF
GREEN = 0x00FF00
YELLOW = 0xFFFF00
DARKGREY = 0x555555
LIGHTGREY = 0xAAAAAA

ACC_RATE = 50           # Hz; plenty for breathing, light on radio/CPU

W, H = 320, 240
STRIP_TOP = 162
STRIP_H = H - STRIP_TOP - 4
STRIP_MID = STRIP_TOP + STRIP_H // 2
WAVE_X0 = 9
WAVE_N = W - 2 * WAVE_X0

D = None                # M5.Display, set in begin()
FONTS = None

# Heart geometry (fixed cell, cleared each frame for the pulse animation).
HX, HY, HR_MAX = 46, 96, 24
HBOX = (HX - HR_MAX - 2, HY - HR_MAX, 2 * (HR_MAX + 2), 2 * HR_MAX + 8)


def log(msg):
    try:
        with open("/flash/_dash.txt", "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


class State:
    def __init__(self):
        self.bpm = None
        self.br = None
        self.wave = 0.0
        self.amp = 1.0
        self.status = ""


def boot_escape():
    """Creature-style: tap screen within 3s of boot to drop to the REPL."""
    Widgets.fillScreen(BLACK)
    Widgets.Label("H10 dashboard - tap for REPL", 6, 10, 1.0, WHITE, BLACK, Widgets.FONTS.DejaVu18)
    for _ in range(30):
        M5.update()
        if M5.Touch.getCount() > 0:
            Widgets.fillScreen(BLACK)
            Widgets.Label("REPL", 8, 10, 1.0, YELLOW, BLACK, Widgets.FONTS.DejaVu24)
            sys.exit()
        time.sleep(0.1)


def centered(text, font, y, color):
    D.setFont(font)
    D.setTextColor(color, BLACK)
    D.drawString(text, (W - D.textWidth(text)) // 2, y)


def draw_static():
    D.fillScreen(BLACK)
    D.setFont(FONTS.DejaVu18)
    D.setTextColor(LIGHTGREY, BLACK)
    D.drawString("POLAR H10", 8, 6)
    D.setTextColor(RED, BLACK)
    D.drawString("bpm", 250, 70)
    D.setTextColor(CYAN, BLACK)
    D.drawString("breaths/min", 92, 132)
    D.drawRect(WAVE_X0 - 1, STRIP_TOP - 1, WAVE_N + 2, STRIP_H + 2, DARKGREY)


def draw_heart(cx, cy, r, color):
    half = r // 2
    D.fillCircle(cx - half, cy - half // 2, half, color)
    D.fillCircle(cx + half, cy - half // 2, half, color)
    D.fillTriangle(cx - r, cy - half // 4, cx + r, cy - half // 4, cx, cy + r, color)


async def acc_task(h10, state):
    est = RespirationEstimator(fs=ACC_RATE)
    while True:
        out = await h10.read_frame(fs=ACC_RATE)
        for (x, y, z) in out["samples"]:
            est.feed(x, y, z)
        state.br = est.rate_bpm
        state.wave = est.wave
        state.amp = est.amp


async def hr_task(h10, state):
    while True:
        hr = await h10.read_hr()
        state.bpm = hr["bpm"]


async def ui_task(state):
    wave = [STRIP_MID] * WAVE_N
    last_bpm = last_br = None
    beat_phase = 0.0
    pulse = 0.0
    last_ms = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        dt = time.ticks_diff(now, last_ms) / 1000.0
        last_ms = now

        # tap anytime -> REPL
        if M5.Touch.getCount() > 0:
            sys.exit()

        bpm_str = str(state.bpm) if state.bpm else "--"
        if bpm_str != last_bpm:
            D.fillRect(92, 44, 150, 70, BLACK)
            D.setFont(FONTS.DejaVu72)
            D.setTextColor(RED, BLACK)
            D.drawString(bpm_str, 92, 44)
            last_bpm = bpm_str

        br_str = ("%.0f" % state.br) if state.br else "--"
        if br_str != last_br:
            D.fillRect(8, 120, 80, 40, BLACK)
            D.setFont(FONTS.DejaVu40)
            D.setTextColor(CYAN, BLACK)
            D.drawString(br_str, 8, 120)
            last_br = br_str

        # heart pulses at the measured rate
        if state.bpm:
            beat_phase += dt * state.bpm / 60.0
            if beat_phase >= 1.0:
                beat_phase -= 1.0
                pulse = 1.0
        pulse *= 0.72
        D.fillRect(*HBOX, BLACK)
        draw_heart(HX, HY, int(16 + (HR_MAX - 16) * pulse), RED if state.bpm else DARKGREY)

        # scrolling breathing waveform
        scale = state.amp * 3.0
        if scale < 20.0:
            scale = 20.0
        y = STRIP_MID - int(state.wave / scale * (STRIP_H // 2 - 2))
        if y < STRIP_TOP + 1:
            y = STRIP_TOP + 1
        elif y > STRIP_TOP + STRIP_H - 1:
            y = STRIP_TOP + STRIP_H - 1
        wave.append(y)
        del wave[0]
        D.fillRect(WAVE_X0, STRIP_TOP, WAVE_N, STRIP_H, BLACK)
        for i in range(1, WAVE_N):
            D.drawLine(WAVE_X0 + i - 1, wave[i - 1], WAVE_X0 + i, wave[i], GREEN)

        M5.update()
        await asyncio.sleep_ms(66)


async def app():
    state = State()
    while True:
        draw_static()
        centered("scanning for strap...", FONTS.DejaVu18, STRIP_MID - 9, YELLOW)
        log("scanning")
        dev = await find_h10(timeout_ms=10000)
        if dev is None:
            centered("no strap - retrying", FONTS.DejaVu18, STRIP_MID - 9, RED)
            log("no strap")
            await asyncio.sleep(2)
            continue
        try:
            async with PolarH10(dev, acc=True, hr=True) as h10:
                await h10.start_acc(sample_rate=ACC_RATE, range_g=8)
                draw_static()
                log("connected, streaming")
                await asyncio.gather(acc_task(h10, state), hr_task(h10, state), ui_task(state))
        except SystemExit:
            raise
        except Exception as e:
            log("disconnect/err: " + repr(e))
            state.bpm = None
            state.br = None
            await asyncio.sleep(1)


def run():
    global D, FONTS
    M5.begin()
    D = M5.Display
    FONTS = D.FONTS
    boot_escape()
    with open("/flash/_dash.txt", "w") as f:
        f.write("dashboard boot\n")
    asyncio.run(app())


run()
