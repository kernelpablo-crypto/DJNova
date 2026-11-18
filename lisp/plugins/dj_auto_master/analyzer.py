# -*- coding: utf-8 -*-
"""DSP Audio Analyzer for Linux Show Player Auto-Master.

Uses ffmpeg + numpy FFT to measure loudness, peak, dynamic range (crest factor),
and 10-band spectral balance matching GStreamer equalizer-10bands.
"""

import math
import os
import subprocess
from urllib.parse import unquote, urlparse

import numpy as np

# GStreamer equalizer-10bands center frequencies (Hz)
BAND_CENTERS = [29, 59, 119, 237, 474, 947, 1889, 3770, 7523, 15011]
BAND_LABELS = ["29Hz", "59Hz", "119Hz", "237Hz", "474Hz", "947Hz", "1.9k", "3.8k", "7.5k", "15k"]

# Psychoacoustic broadcast/club target curve (relative dB per octave band, pink-like tilt)
# Centered around 0 dB average across the 10 bands
TARGET_TILT = np.array([+1.5, +2.5, +1.5, -0.5, -0.5, 0.0, +0.5, +0.5, -1.5, -4.0], dtype=np.float64)


def uri_to_local_path(uri):
    if not uri:
        return ""
    if hasattr(uri, "absolute_path"):
        return str(uri.absolute_path)
    if hasattr(uri, "uri"):
        uri = str(uri.uri)
    else:
        uri = str(uri)
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        return unquote(parsed.path)
    return uri


def analyze_audio_file(filepath, target_rms_db=-14.0):
    """Decode audio via ffmpeg and compute profile, 10-band EQ, compression, and volume gain."""
    if not filepath or not os.path.isfile(filepath):
        raise FileNotFoundError(f"Archivo no encontrado: {filepath}")

    sr = 44100
    # Decode up to 120 seconds mono float32 (very fast, typically 0.15s - 0.5s)
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", filepath,
        "-t", "120",
        "-ac", "1",
        "-ar", str(sr),
        "-f", "f32le",
        "pipe:1",
    ]
    raw = subprocess.check_output(cmd, timeout=20)
    samples = np.frombuffer(raw, dtype=np.float32)
    if samples.size < sr // 2:
        raise ValueError("El archivo de audio es demasiado corto o está vacío.")

    # Ignore near-silent intro/outro sections for accurate active loudness & spectral analysis
    frame_len = 4096
    n_frames = samples.size // frame_len
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)

    active_thresh = max(1e-4, float(np.percentile(frame_rms, 35)) * 0.5)
    active_mask = frame_rms >= active_thresh
    active_frames = frames[active_mask] if np.any(active_mask) else frames

    # Loudness & Dynamics metrics
    rms_lin = float(np.sqrt(np.mean(active_frames * active_frames) + 1e-12))
    rms_db = 20.0 * math.log10(max(rms_lin, 1e-6))

    peak_lin = float(np.max(np.abs(samples)) + 1e-12)
    peak_db = 20.0 * math.log10(max(peak_lin, 1e-6))

    # Crest factor / dynamic range estimate (dB)
    p95_rms_db = 20.0 * math.log10(max(float(np.percentile(frame_rms[active_mask], 95)), 1e-6))
    p25_rms_db = 20.0 * math.log10(max(float(np.percentile(frame_rms[active_mask], 25)), 1e-6))
    crest_db = max(1.0, peak_db - rms_db)
    dynamic_spread_db = max(1.0, p95_rms_db - p25_rms_db)

    # Multi-frame Hann-windowed FFT power spectrum
    window = np.hanning(frame_len).astype(np.float32)
    # Sample up to 250 evenly spaced active frames for speed
    step = max(1, active_frames.shape[0] // 250)
    sub = active_frames[::step] * window[None, :]
    spec = np.abs(np.fft.rfft(sub, axis=1)) ** 2
    avg_power = np.mean(spec, axis=0)
    freqs = np.fft.rfftfreq(frame_len, d=1.0 / sr)

    # Energy per octave band centered on GStreamer's 10 EQ frequencies
    band_db = np.zeros(10, dtype=np.float64)
    for i, fc in enumerate(BAND_CENTERS):
        f_lo = fc / math.sqrt(2.0)
        f_hi = fc * math.sqrt(2.0)
        idx = np.where((freqs >= f_lo) & (freqs <= f_hi))[0]
        if idx.size > 0:
            # Sum power in band (pink-noise equal-energy per octave)
            p_sum = float(np.sum(avg_power[idx]))
            band_db[i] = 10.0 * math.log10(max(p_sum, 1e-12))
        else:
            band_db[i] = -60.0

    # Normalize band spectrum around its mean (relative spectral shape)
    rel_spec = band_db - np.mean(band_db)

    # Spectral descriptors for automatic track classification
    sub_bass = float(np.mean(rel_spec[0:3]))   # 29-119 Hz
    low_mid = float(np.mean(rel_spec[3:5]))    # 237-474 Hz
    vocal_mid = float(np.mean(rel_spec[5:8]))  # 947-3.8 kHz
    air_high = float(np.mean(rel_spec[8:10]))  # 7.5-15 kHz

    # Profile detection
    if sub_bass < -4.5 and vocal_mid > 2.5 and air_high < -4.0:
        profile = "Voz / Locución / Discurso"
        profile_bias = np.array([-4.0, -2.5, -0.5, -1.5, 0.0, +1.0, +2.5, +2.0, +0.5, -1.0])
        comp_threshold, comp_ratio = 0.48, 0.30
    elif air_high < -11.0:
        profile = "Audio Opaco / Grabación Antigua"
        profile_bias = np.array([-1.5, 0.0, +0.5, -1.5, -1.0, +0.5, +1.5, +2.5, +3.5, +3.0])
        comp_threshold, comp_ratio = 0.62, 0.38
    elif sub_bass > 3.5 and crest_db < 12.5:
        profile = "Club / Electrónica / Urbano"
        profile_bias = np.array([+0.5, +1.5, +1.0, -1.5, -1.0, 0.0, +0.5, +1.5, +1.5, +1.0])
        comp_threshold, comp_ratio = 0.80, 0.45
    elif crest_db >= 14.5 or dynamic_spread_db >= 10.0:
        profile = "Fanfarria / Orquestal / Alta Dinámica"
        profile_bias = np.array([+0.5, +1.0, +0.5, -1.0, -0.5, +0.5, +1.0, +1.5, +1.5, +1.0])
        comp_threshold, comp_ratio = 0.56, 0.32
    else:
        profile = "Pop / Rock / Bailable"
        profile_bias = np.array([+0.5, +1.5, +0.5, -1.5, -0.5, 0.0, +1.0, +1.5, +1.5, +0.5])
        comp_threshold, comp_ratio = 0.72, 0.40

    # Gentle corrective EQ: 38% correction toward target tilt + profile musical polish
    raw_correction = (TARGET_TILT - rel_spec) * 0.38 + profile_bias * 0.65
    # Smooth neighboring bands slightly so curve stays musical
    smoothed = raw_correction.copy()
    for i in range(1, 9):
        smoothed[i] = 0.2 * raw_correction[i - 1] + 0.6 * raw_correction[i] + 0.2 * raw_correction[i + 1]
    # Remove overall DC gain bias so EQ doesn't blow up volume
    smoothed -= np.mean(smoothed) * 0.5
    # Clamp safely to [-6, +6] dB integers (Qt5 QSlider in Equalizer10Settings requires int)
    eq_bands = [int(round(max(-6.0, min(6.0, v)))) for v in smoothed]

    # Target loudness leveling (safely clamped so no digital distortion)
    gain_db = target_rms_db - rms_db
    # Ensure peak after gain doesn't exceed +1 dB (AudioDynamic compressor catches minor peaks)
    max_safe_gain_db = (0.5 - peak_db) + 3.0
    gain_db = max(-9.0, min(gain_db, max_safe_gain_db, 5.5))
    volume_linear = round(float(max(0.32, min(1.85, 10.0 ** (gain_db / 20.0)))), 3)

    return {
        "profile": profile,
        "rms_db": round(rms_db, 1),
        "peak_db": round(peak_db, 1),
        "crest_db": round(crest_db, 1),
        "eq_bands": eq_bands,
        "compressor": {
            "mode": "compressor",
            "characteristics": "soft-knee",
            "threshold": round(comp_threshold, 2),
            "ratio": round(comp_ratio, 2),
        },
        "volume": volume_linear,
        "gain_db": round(gain_db, 1),
    }
