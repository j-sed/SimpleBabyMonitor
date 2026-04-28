# SimpleBabyMonitor

A lightweight Raspberry Pi baby monitor that streams live video and audio over your local network via WebRTC, with a built-in web UI for viewing the stream and configuring WiFi.

## Overview

SimpleBabyMonitor runs a Python HTTP server on a Raspberry Pi that:
- Captures video from the Pi camera module (Picamera2) at 320×240 in RGB888
- Captures audio from the default PyAudio input device at 48 kHz mono
- Streams both in real-time to any browser using WebRTC (aiortc)
- Serves a single-page web UI accessible from any browser on the local network
- Provides WiFi configuration and device reboot so the Pi can be managed without SSH

Latency is significantly lower than HLS-based approaches: **~100–300 ms** end-to-end.

## Project Structure

```
SimpleBabyMonitor/
├── server.py            # aiohttp HTTP server + aiortc WebRTC signaling + media tracks
├── index.html           # Single-page web UI (vanilla HTML/JS/CSS)
└── install_service.sh   # Installs and enables the systemd user service
```

## Requirements

| Dependency | Purpose |
|---|---|
| Raspberry Pi (any model with camera port) | Target hardware |
| Python 3.8+ | Runtime |
| [Picamera2](https://github.com/raspberrypi/picamera2) | Camera capture |
| [aiortc](https://github.com/aiortc/aiortc) | WebRTC peer connections and media |
| [aiohttp](https://docs.aiohttp.org/) | Async HTTP server and signaling |
| [PyAV (av)](https://pyav.org/) | Audio/video frame encoding |
| [PyAudio](https://people.csail.mit.edu/hubert/pyaudio/) | Microphone capture via ALSA |
| [NumPy](https://numpy.org/) | Audio buffer handling |
| NetworkManager (`nmcli`) | WiFi configuration |

No FFmpeg binary, Node.js, database, or Docker is required.

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/j-sed/SimpleBabyMonitor.git
cd SimpleBabyMonitor

# 2. Install system packages
sudo apt-get install python3-picamera2 portaudio19-dev

# 3. Create a virtual environment (recommended)
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 4. Install Python dependencies
pip install aiortc aiohttp av pyaudio numpy
```

> **Note:** `picamera2` is a system package. Use `--system-site-packages` when creating the venv so it is available inside the environment.

## Running

```bash
python3 server.py
```

The server starts on **port 8001**. Open `http://<pi-ip>:8001` in any browser on the same network.

On first launch the camera needs ~3 seconds to warm up. The loading overlay in the browser shows elapsed time and stage during connection.

## Running as a systemd Service

`install_service.sh` installs the server as a systemd **user** service that starts automatically at login:

```bash
bash install_service.sh
```

This creates `~/.config/systemd/user/babymonitorWebRTC.service`. To manage it:

```bash
systemctl --user status babymonitorWebRTC.service
systemctl --user restart babymonitorWebRTC.service
journalctl --user -u babymonitorWebRTC.service -f
```

> **Note:** The script hard-codes `/home/jsed/PiCameraWebRTC/` as the working directory. Edit `WorkingDirectory` and `ExecStart` inside the script to match your actual path before running it.

## Web UI

The UI (`index.html`) has two tabs:

### View
- WebRTC video + audio player that connects via `POST /offer`
- Loading overlay with elapsed-time counter and stage label during connection
- Automatic reconnection with exponential backoff on disconnect or stall (30 s watchdog)
- Double-tap to toggle full-screen on mobile
- Tap the overlay or video to resume playback if autoplay was blocked by the browser

### Setup
- **WiFi Configuration** — enter an SSID and optional password; the Pi connects via `nmcli`
- **Device Control** — two-step reboot button (tap once to arm, tap again within 4 s to confirm)
- **Device Information** — shows port, latency, and remote-access instructions

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Redirects to `/index.html` |
| `GET` | `/index.html` | Web UI |
| `POST` | `/offer` | WebRTC SDP offer/answer signaling |
| `GET` | `/api/wifi-networks` | JSON list of nearby WiFi networks |
| `POST` | `/api/configure-wifi` | Connect to a WiFi network |
| `POST` | `/api/reboot` | Reboot the Raspberry Pi |

### `POST /offer`

Request body:

```json
{
  "sdp": "<browser SDP offer>",
  "type": "offer"
}
```

Returns a fully ICE-gathered SDP answer (non-trickle ICE — all candidates included in the single response).

### `POST /api/configure-wifi`

Request body:

```json
{
  "ssid": "MyNetwork",
  "password": "mypassword"
}
```

`password` is optional for open networks.

## Stream Configuration

These values are hardcoded in `server.py` and can be changed there:

| Setting | Value |
|---|---|
| Video resolution | 320×240 |
| Video format | RGB888 (raw capture; codec negotiated by aiortc) |
| Camera FPS | 30 |
| Colour saturation | 0 (greyscale) |
| Audio sample rate | 48 kHz |
| Audio channels | Mono |
| Audio chunk size | 960 samples (20 ms per frame) |
| HTTP port | 8001 |
| ICE servers | `stun:stun.l.google.com:19302` |
| ICE gathering timeout | 5 s |

## Troubleshooting

**Stream shows a loading spinner indefinitely** — The camera warms up for 3 seconds on startup. If the spinner persists, check that no other process holds the camera (`fuser /dev/video0`). Only one process can use the Pi camera at a time.

**No audio** — Verify a microphone is connected and is the ALSA default device (`arecord -l`). PyAudio uses the default input; ALSA probe warnings on startup are cosmetic and safe to ignore.

**Initial connection takes 1–3 seconds** — This is normal. ICE gathering collects all network candidates before returning the SDP answer, which adds a small delay on first connect.

**WiFi scan returns an empty list** — Ensure NetworkManager is running (`systemctl status NetworkManager`) and that `nmcli dev wifi` works from the command line.

**Browser cannot reach the stream** — The Pi and browser must be on the same local network (or connected via ZeroTier for remote access). Check that port 8001 is not blocked by a firewall.

**Two services conflict** — The camera is exclusive. If the old HLS service (`babymonitorUser.service` on port 8000) is still running, stop it before starting the WebRTC service: `systemctl --user stop babymonitorUser.service`.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Commit your changes (`git commit -m 'Add my change'`)
4. Push and open a Pull Request

## License

MIT License
