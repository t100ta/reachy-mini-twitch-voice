from __future__ import annotations

import math
from array import array

# Official conversation-app inspired tuning.
FRAME_MS = 20
HOP_MS = 50

SWAY_MASTER = 1.5
SENS_DB_OFFSET = 4.0
VAD_DB_ON = -35.0
VAD_DB_OFF = -45.0
VAD_ATTACK_MS = 40
VAD_RELEASE_MS = 250
ENV_FOLLOW_GAIN = 0.65

SWAY_F_PITCH = 2.2
SWAY_A_PITCH_DEG = 4.5
SWAY_F_YAW = 0.6
SWAY_A_YAW_DEG = 7.5
SWAY_F_ROLL = 1.3
SWAY_A_ROLL_DEG = 2.25

SWAY_DB_LOW = -46.0
SWAY_DB_HIGH = -18.0
LOUDNESS_GAMMA = 0.9
SWAY_ATTACK_MS = 50
SWAY_RELEASE_MS = 250


def _rms_dbfs(samples: list[float]) -> float:
    if not samples:
        return -90.0
    mean_sq = sum(x * x for x in samples) / max(len(samples), 1)
    rms = math.sqrt(max(mean_sq, 1e-12))
    return 20.0 * math.log10(rms + 1e-12)


def _loudness_gain(db: float, offset: float = SENS_DB_OFFSET) -> float:
    t = (db + offset - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    t = min(max(t, 0.0), 1.0)
    return t**LOUDNESS_GAMMA if LOUDNESS_GAMMA != 1.0 else t


def _wav_to_mono_float(raw: bytes, channels: int) -> list[float]:
    data = array("h")
    data.frombytes(raw)
    if not data:
        return []
    channels = max(channels, 1)
    out: list[float] = []
    for i in range(0, len(data), channels):
        s = 0.0
        for c in range(channels):
            if i + c < len(data):
                s += float(data[i + c])
        out.append((s / channels) / 32768.0)
    return out


def frames_from_wav(raw: bytes, sample_rate: int, channels: int) -> list[dict[str, float]]:
    mono = _wav_to_mono_float(raw, channels)
    if not mono or sample_rate <= 0:
        return []

    frame = max(1, int(sample_rate * FRAME_MS / 1000))
    hop = max(1, int(sample_rate * HOP_MS / 1000))
    vad_attack_fr = max(1, int(VAD_ATTACK_MS / HOP_MS))
    vad_release_fr = max(1, int(VAD_RELEASE_MS / HOP_MS))
    sway_attack_fr = max(1, int(SWAY_ATTACK_MS / HOP_MS))
    sway_release_fr = max(1, int(SWAY_RELEASE_MS / HOP_MS))

    vad_on = False
    vad_above = 0
    vad_below = 0
    sway_env = 0.0
    sway_up = 0
    sway_down = 0
    t = 0.0

    out: list[dict[str, float]] = []

    for pos in range(0, len(mono), hop):
        window_start = max(0, pos + hop - frame)
        frame_samples = mono[window_start : pos + hop]
        db = _rms_dbfs(frame_samples)

        if db >= VAD_DB_ON:
            vad_above += 1
            vad_below = 0
            if not vad_on and vad_above >= vad_attack_fr:
                vad_on = True
        elif db <= VAD_DB_OFF:
            vad_below += 1
            vad_above = 0
            if vad_on and vad_below >= vad_release_fr:
                vad_on = False

        if vad_on:
            sway_up = min(sway_attack_fr, sway_up + 1)
            sway_down = 0
        else:
            sway_down = min(sway_release_fr, sway_down + 1)
            sway_up = 0

        up = sway_up / sway_attack_fr
        down = 1.0 - (sway_down / sway_release_fr)
        target = up if vad_on else down
        sway_env += ENV_FOLLOW_GAIN * (target - sway_env)
        sway_env = min(max(sway_env, 0.0), 1.0)

        loud = _loudness_gain(db) * SWAY_MASTER
        t += HOP_MS / 1000.0
        env = sway_env

        pitch_deg = SWAY_A_PITCH_DEG * loud * env * math.sin(2 * math.pi * SWAY_F_PITCH * t)
        yaw_deg = SWAY_A_YAW_DEG * loud * env * math.sin(2 * math.pi * SWAY_F_YAW * t)
        roll_deg = SWAY_A_ROLL_DEG * loud * env * math.sin(2 * math.pi * SWAY_F_ROLL * t)

        out.append(
            {
                "pitch_deg": pitch_deg,
                "yaw_deg": yaw_deg,
                "roll_deg": roll_deg,
                "gain": min(max(loud * env, 0.0), 1.0),
            }
        )

    return out
