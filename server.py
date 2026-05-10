#!/home/jsed/PiCameraTutorials/venv/bin/python3
"""
WebRTC baby monitor server.

Uses aiortc for WebRTC media + aiohttp for HTTP/signaling.
Signaling: HTTP POST /offer, non-trickle ICE (answer returned after gathering).

Endpoints:
  GET  /                    → 301 /index.html
  GET  /index.html          → web UI
  POST /offer               → WebRTC SDP offer/answer signaling
  GET  /api/wifi-networks   → nmcli WiFi scan (JSON)
  POST /api/configure-wifi  → nmcli WiFi connect (JSON)
  POST /api/reboot          → sudo reboot
"""

import asyncio
import fractions
import logging
import os
import queue as _queue
import subprocess
import threading
import time
from typing import Set

import av
import numpy as np
import pyaudio
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack
from picamera2 import Picamera2

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ── Constants ─────────────────────────────────────────────────────────────────

CAMERA_WIDTH       = 320
CAMERA_HEIGHT      = 240
CAMERA_FPS         = 30
AUDIO_SAMPLE_RATE  = 48000
AUDIO_CHANNELS     = 1
AUDIO_CHUNK_SAMPLES = 960   # 20 ms at 48 kHz

VIDEO_CLOCK_RATE   = 90000
VIDEO_TIME_BASE    = fractions.Fraction(1, VIDEO_CLOCK_RATE)
AUDIO_TIME_BASE    = fractions.Fraction(1, AUDIO_SAMPLE_RATE)

STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

MIC_GAIN_MIN = 0.5
MIC_GAIN_MAX = 10.0

# ── Globals ───────────────────────────────────────────────────────────────────

picam2: Picamera2 | None = None
pcs: Set[RTCPeerConnection] = set()
mic_gain: float = 1.0

# ── WiFi helpers ──────────────────────────────────────────────────────────────

WIFI_LIST_CMD    = "nmcli -f ssid,mode,chan,rate,signal,bars,security -t dev wifi"
WIFI_CONNECT_CMD = "nmcli device wifi connect"


def get_wifi_networks():
    try:
        result = subprocess.run(WIFI_LIST_CMD, shell=True, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return []
        networks = []
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split(":")
            if len(parts) >= 5:
                try:
                    networks.append({
                        "ssid":     parts[0],
                        "signal":   int(parts[4]),
                        "security": " ".join(parts[6:]) if len(parts) > 6 else "Open",
                    })
                except (ValueError, IndexError):
                    continue
        return networks
    except Exception as e:
        logging.error(f"WiFi list error: {e}")
        return []


# ── Video track ───────────────────────────────────────────────────────────────

class CameraVideoTrack(VideoStreamTrack):
    """Captures Picamera2 RGB frames; uses wall-clock pts to avoid drift."""

    def __init__(self):
        super().__init__()
        self._t0: float | None = None

    async def recv(self) -> av.VideoFrame:
        loop = asyncio.get_running_loop()
        arr  = await loop.run_in_executor(None, picam2.capture_array, "main")
        now  = time.time()
        if self._t0 is None:
            self._t0 = now
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts       = int((now - self._t0) * VIDEO_CLOCK_RATE)
        frame.time_base = VIDEO_TIME_BASE
        return frame


# ── Audio track ───────────────────────────────────────────────────────────────

class MicrophoneAudioTrack(AudioStreamTrack):
    """
    Lazy-start microphone track.  Opens PyAudio only on first recv() call
    (after ICE is done) so no audio accumulates in the buffer during signaling.
    A background thread fills a 2-slot queue; oldest frame is dropped when full
    to keep latency near zero.  Wall-clock pts avoids long-running drift.
    """

    def __init__(self):
        super().__init__()
        self._buf        = _queue.Queue(maxsize=2)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0: float | None = None

    # ── internal ──

    def _start_capture(self):
        t = threading.Thread(target=self._capture_loop, daemon=True)
        t.start()
        self._thread = t

    def _capture_loop(self):
        # Suppress ALSA/JACK device-probe noise (cosmetic C-level stderr)
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        pa = pyaudio.PyAudio()
        os.dup2(old_stderr, 2)
        os.close(devnull)
        os.close(old_stderr)
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK_SAMPLES,
        )
        try:
            while not self._stop_event.is_set():
                raw = stream.read(AUDIO_CHUNK_SAMPLES, exception_on_overflow=False)
                if self._buf.full():
                    try:
                        self._buf.get_nowait()
                    except _queue.Empty:
                        pass
                try:
                    self._buf.put_nowait(raw)
                except _queue.Full:
                    pass
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    # ── aiortc interface ──

    async def recv(self) -> av.AudioFrame:
        if self._thread is None:
            self._start_capture()

        loop = asyncio.get_running_loop()
        raw  = await loop.run_in_executor(None, self._buf.get)

        now = time.time()
        if self._t0 is None:
            self._t0 = now
        pts = int((now - self._t0) * AUDIO_SAMPLE_RATE)

        samples = np.frombuffer(raw, dtype=np.int16).reshape(1, -1)
        if mic_gain != 1.0:
            samples = np.clip(samples.astype(np.float32) * mic_gain, -32768, 32767).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.pts         = pts
        frame.time_base   = AUDIO_TIME_BASE
        frame.sample_rate = AUDIO_SAMPLE_RATE
        return frame

    def stop(self):
        self._stop_event.set()


# ── HTTP handlers ─────────────────────────────────────────────────────────────

async def handle_index_redirect(request: web.Request) -> web.Response:
    raise web.HTTPMovedPermanently("/index.html")


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def handle_offer(request: web.Request) -> web.Response:
    """Receive SDP offer, return fully ICE-gathered SDP answer."""
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    offer = RTCSessionDescription(sdp=body["sdp"], type=body["type"])

    # Default RTCPeerConnection uses Google STUN; race condition fix: register
    # icegatheringstatechange BEFORE setLocalDescription so we never miss the event.
    pc = RTCPeerConnection()
    pcs.add(pc)

    audio_track = MicrophoneAudioTrack()
    video_track = CameraVideoTrack()

    @pc.on("connectionstatechange")
    async def on_connection_state():
        logging.info(f"Connection state → {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
            audio_track.stop()

    pc.addTrack(video_track)
    pc.addTrack(audio_track)

    # Register BEFORE setLocalDescription to avoid missing instant-complete event
    ice_done = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def on_ice():
        if pc.iceGatheringState == "complete":
            ice_done.set()

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    if pc.iceGatheringState != "complete":
        try:
            await asyncio.wait_for(ice_done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logging.warning("ICE gathering timed out — returning partial candidates")

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_offer_options(request: web.Request) -> web.Response:
    return web.Response(headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


async def handle_wifi_list(request: web.Request) -> web.Response:
    loop     = asyncio.get_running_loop()
    networks = await loop.run_in_executor(None, get_wifi_networks)
    return web.json_response(
        {"status": "success", "networks": networks},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_wifi_configure(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    ssid     = data.get("ssid", "").strip()
    password = data.get("password", "")

    if not ssid:
        return web.json_response({"status": "error", "message": "SSID is required"}, status=400)

    cmd = f'{WIFI_CONNECT_CMD} "{ssid}"'
    if password:
        cmd = f'{WIFI_CONNECT_CMD} "{ssid}" password "{password}"'

    logging.info(f"Configuring WiFi: {ssid}")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30),
        )
    except subprocess.TimeoutExpired:
        return web.json_response({"status": "error", "message": "Configuration timeout"}, status=500)

    if result.returncode == 0:
        return web.json_response(
            {"status": "success", "message": f"WiFi configured for {ssid}", "output": result.stdout},
            headers={"Access-Control-Allow-Origin": "*"},
        )
    return web.json_response(
        {"status": "error", "message": "WiFi configuration failed", "error": result.stderr},
        status=500,
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_set_volume(request: web.Request) -> web.Response:
    global mic_gain
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    gain = data.get("gain")
    if not isinstance(gain, (int, float)):
        return web.json_response({"status": "error", "message": "gain must be a number"}, status=400)

    mic_gain = float(max(MIC_GAIN_MIN, min(MIC_GAIN_MAX, gain)))
    logging.info(f"Mic gain set to {mic_gain:.2f}x")
    return web.json_response(
        {"status": "success", "gain": mic_gain},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_reboot(request: web.Request) -> web.Response:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run("sudo reboot", shell=True, capture_output=True, text=True, timeout=5),
        )
        if result.returncode == 0:
            return web.json_response({"status": "success", "message": "Rebooting…"})
        return web.json_response(
            {"status": "error", "message": result.stderr or "Reboot failed"}, status=500
        )
    except subprocess.TimeoutExpired:
        return web.json_response({"status": "success", "message": "Rebooting…"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# ── App lifecycle ─────────────────────────────────────────────────────────────

async def on_startup(app: web.Application):
    global picam2
    logging.info("Initialising Picamera2...")
    picam2 = Picamera2()
    picam2.configure(
        picam2.create_video_configuration(
            main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"}
        )
    )
    picam2.set_controls({"Saturation": 0})
    picam2.start()
    logging.info("Camera warming up (3 s)...")
    await asyncio.sleep(3)
    logging.info("Camera ready.")


async def on_shutdown(app: web.Application):
    logging.info("Closing all peer connections...")
    await asyncio.gather(*[pc.close() for pc in pcs], return_exceptions=True)
    pcs.clear()
    if picam2:
        picam2.stop()
    logging.info("Shutdown complete.")


def build_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/",                       handle_index_redirect)
    app.router.add_get("/index.html",             handle_index)
    app.router.add_post("/offer",                 handle_offer)
    app.router.add_options("/offer",              handle_offer_options)
    app.router.add_get("/api/wifi-networks",      handle_wifi_list)
    app.router.add_post("/api/configure-wifi",    handle_wifi_configure)
    app.router.add_post("/api/set-volume",        handle_set_volume)
    app.router.add_post("/api/reboot",            handle_reboot)
    return app


if __name__ == "__main__":
    app = build_app()
    web.run_app(app, host="0.0.0.0", port=8001, access_log=logging.getLogger())
