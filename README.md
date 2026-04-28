# SimpleBabyMonitor

A lightweight Raspberry Pi baby monitor that streams live video and audio over your local network via HLS, with a built-in web UI for viewing the stream and configuring WiFi.

## Overview

SimpleBabyMonitor runs a Python HTTP server on a Raspberry Pi that:
- Captures video from the Pi camera module (Picamera2) at 320×240
- Captures audio from the default ALSA sound device
- Muxes both into an HLS stream (H.264 video + AAC audio) via FFmpeg
- Serves a single-page web UI accessible from any browser on the local network
- Provides WiFi configuration so the Pi can be pointed at a new network without SSH

## Project Structure

```
SimpleBabyMonitor/
├── server.py               # Python HTTP server + HLS streaming logic
├── index.html              # Single-page web UI (vanilla HTML/JS/CSS)
└── babymonitor_service.sh  # Script to register server as a systemd user service
```

The `hls/` directory (containing `stream.m3u8` and `.ts` segments) is created at runtime by FFmpeg and is not tracked in git.

## Requirements

| Dependency | Purpose |
|---|---|
| Raspberry Pi (any model with camera port) | Target hardware |
| Python 3.8+ | Runtime |
| [Picamera2](https://github.com/raspberrypi/picamera2) | Camera capture |
| FFmpeg (with ALSA support) | HLS encoding and muxing |
| NetworkManager (`nmcli`) | WiFi configuration |
| A microphone on the ALSA `default` device | Audio capture |

No Node.js, database, or Docker is required.

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/j-sed/SimpleBabyMonitor.git
cd SimpleBabyMonitor

# 2. Install system packages
sudo apt-get install ffmpeg python3-picamera2

# 3. (Optional) Create a virtual environment
python3 -m venv venv
source venv/bin/activate
# picamera2 is a system package; you may need --system-site-packages
```

## Running

```bash
python3 server.py
```

The server starts on **port 8000**. Open `http://<pi-ip>:8000` in any browser on the same network.

On first launch the camera needs ~3 seconds to stabilise before the stream becomes available. Any stale HLS segments from a previous run are removed automatically on startup.

## Running as a systemd Service

`babymonitor_service.sh` installs the server as a systemd **user** service that starts automatically when you log in:

```bash
bash babymonitor_service.sh
```

This creates `~/.config/systemd/user/babymonitorUser.service`. To manage it:

```bash
systemctl --user status babymonitorUser.service
systemctl --user restart babymonitorUser.service
journalctl --user -u babymonitorUser.service -f
```

> **Note:** The script hard-codes `/home/jsed/PiCameraTutorials/` as the working directory. Edit `WorkingDirectory` and `ExecStart` inside the script to match your actual path before running it.

## Web UI

The UI (`index.html`) has two tabs:

### View
- HLS video player that loads `/hls/stream.m3u8`
- Automatic reconnection with exponential backoff if the stream stalls or is temporarily unavailable
- Double-tap to toggle full-screen on mobile

### Setup
- Scans for available WiFi networks via the `/api/wifi-networks` endpoint
- Connects the Pi to a chosen network via the `/api/configure-wifi` endpoint
- Useful for headless setup when moving the Pi to a new location

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Redirects to `/index.html` |
| `GET` | `/index.html` | Web UI |
| `GET` | `/hls/stream.m3u8` | HLS playlist (served as a static file) |
| `GET` | `/hls/*.ts` | HLS video/audio segments |
| `GET` | `/api/wifi-networks` | JSON list of nearby WiFi networks |
| `POST` | `/api/configure-wifi` | Connect to a WiFi network |
| `GET` | `/frame-info` | JSON with Unix timestamp of the last encoded frame (stall detection) |
| `GET` | `/stream.mjpg` | Legacy MJPEG stream (kept for compatibility) |

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
| Video codec | H.264 (copy from Picamera2) |
| Audio codec | AAC |
| Audio sample rate | 16 kHz (input) → 48 kHz (output) |
| Audio bitrate | 48 kbit/s |
| Audio gain | 2.5× |
| HLS segment duration | 0.2 s |
| HLS playlist length | 10 segments |
| HTTP port | 8000 |

## Troubleshooting

**Stream shows black frames at startup** — The server waits 3 seconds for the camera sensor to stabilise before FFmpeg begins writing segments. If you see black frames, wait a moment.

**No audio** — Verify that a microphone is connected and is the ALSA `default` device (`arecord -l`). Adjust the `audio_device` passed to `FfmpegOutput` if needed.

**WiFi scan returns empty list** — Ensure NetworkManager is running (`systemctl status NetworkManager`) and that `nmcli dev wifi` works from the command line.

**Browser cannot reach the stream** — The Pi and browser must be on the same local network. Check that port 8000 is not blocked by a firewall.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-change`)
3. Commit your changes (`git commit -m 'Add my change'`)
4. Push and open a Pull Request

## License

MIT License
