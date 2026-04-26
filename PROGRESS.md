# PiCameraWebRTC — WebRTC Baby Monitor

## Goal
Replace HLS streaming in `~/PiCameraTutorials/` with a WebRTC-based server.
Same capabilities: live camera video + audio, WiFi config UI, systemd autostart.
Benefit: significantly lower latency (~100-300ms vs 2-5s for HLS).

## Architecture

```
Picamera2 (RGB888 raw capture)
       ↓  capture_array() via run_in_executor
CameraVideoTrack (aiortc VideoStreamTrack)
       ↓  WebRTC H264/VP8
RTCPeerConnection ──────────────────────────── Browser
       ↑  WebRTC Opus
MicrophoneAudioTrack (aiortc AudioStreamTrack)
       ↑  PyAudio ALSA read via run_in_executor

Signaling:  HTTP POST /offer  (non-trickle ICE — all candidates in single response)
HTTP server: aiohttp on port 8001
STUN:       stun:stun.l.google.com:19302  +  stun1.l.google.com:19302
```

## Stack
- Python 3.13 (venv at ~/PiCameraTutorials/venv)
- aiortc 1.14.0  ✓ already installed
- aiohttp 3.13.1 ✓ already installed
- av (PyAV) 14.2.0 ✓ already installed
- PyAudio 0.2.13 ✓ already installed
- picamera2 ✓ already installed

## File Layout
```
~/PiCameraWebRTC/
├── PROGRESS.md          ← this file
├── server.py            ← main aiohttp + aiortc server
├── index.html           ← web client (WebRTC + WiFi config tabs)
└── install_service.sh   ← installs/enables systemd user unit

~/.config/systemd/user/
└── babymonitorWebRTC.service
```

## Steps

### Phase 1 — Core implementation
- [x] Create ~/PiCameraWebRTC/ directory
- [x] Write PROGRESS.md
- [x] Write server.py
- [x] Write index.html
- [x] Write install_service.sh

### Phase 2 — Service & validation
- [x] Install and enable systemd unit babymonitorWebRTC.service
- [x] Verified: http://babymonitor.local:8001 serves index.html correctly
- [x] Verified: /api/wifi-networks returns valid JSON
- [ ] Test: open http://babymonitor.local:8001 in browser, verify video plays
- [ ] Verify audio works
- [ ] Test reconnection on browser refresh
- [ ] Tune if needed: fps, resolution, audio latency

### Phase 3 — Cutover (optional)
- [ ] Stop + disable old HLS service (babymonitorUser.service on port 8000)
- [ ] Change WebRTC service port from 8001 → 8000 in server.py + unit file
- [ ] Restart babymonitorWebRTC.service

## Important: Camera Exclusivity
The Pi camera can only be held by ONE process at a time.
- Old HLS service: babymonitorUser.service (port 8000)
- New WebRTC service: babymonitorWebRTC.service (port 8001)

They CANNOT run simultaneously. Current state:
  babymonitorUser.service    → STOPPED (manually stopped for testing)
  babymonitorWebRTC.service  → RUNNING on port 8001

The folder-watcher.sh (pid 901) was also killed — it would have restarted
the HLS service if no new HLS segment appeared within 10 s.

To restore HLS (if needed):
  systemctl --user stop babymonitorWebRTC.service
  systemctl --user start babymonitorUser.service

## Resuming After Session Limit
1. Check which service is running:
   ssh babymonitor.local "systemctl --user status babymonitorWebRTC.service"

2. If WebRTC service is running but browser test not done:
   Open http://babymonitor.local:8001 in browser and test video+audio.

3. If Phase 2 tests pass, proceed to Phase 3 cutover (port 8000, disable HLS).

4. If service is crashed/failed, check logs:
   ssh babymonitor.local "systemctl --user status babymonitorWebRTC.service -l"

## Key Design Decisions

### Why non-trickle ICE?
Avoids need for a persistent WebSocket channel for ICE candidate exchange.
Single POST /offer returns a fully-gathered SDP answer. Adds ~1-2s to initial
connection time (ICE gathering) but simplifies the signaling code greatly.

### Why raw RGB capture from Picamera2?
Avoids double-encode (camera H264 → decode → WebRTC re-encode).
At 320×240 RGB, capture_array() is very fast and thread-safe.
aiortc handles WebRTC codec negotiation (VP8/VP9/H264) automatically.

### Why share one Picamera2 instance?
Multiple concurrent viewers each call capture_array() independently.
Picamera2 is thread-safe; each call returns the latest available frame.

### Audio design
PyAudio opens one stream per RTCPeerConnection. 48kHz mono 16-bit.
Chunk size: 960 samples (20ms per chunk). Each client gets its own read.

## Known Issues / Gotchas
- Camera is exclusive: stop HLS service before starting WebRTC service.
- folder-watcher.sh (watchdog for HLS) must also be stopped to prevent
  it from restarting babymonitorUser.service automatically.
- First WebRTC connection takes ~1-3s for ICE gathering (normal).
- PyAudio may log ALSA warnings on startup — cosmetic, safe to ignore.
- If audio doesn't work: check that the PyAudio device index is correct
  (server.py uses default input device; may need explicit device_index=N).
