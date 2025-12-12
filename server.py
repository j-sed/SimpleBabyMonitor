#!/home/jsed/PiCameraTutorials/venv/bin/python3
"""
server.py

Produce HLS (stream.m3u8 + fragments) with camera video and embedded audio.
Audio is captured by ffmpeg (ALSA "default" device) and encoded to low-quality AAC
so the HLS stream contains both video and audio.

Also provides WiFi configuration endpoints for setting up wireless connections via web UI.

Notes:
- Requires ffmpeg on the system with ALSA support.
- This script uses Picamera2 to produce an H264 elementary stream which is piped
  into ffmpeg (via Picamera2's FfmpegOutput). ffmpeg also opens the ALSA input.
- The HTTP server serves index.html and any generated playlist/segment files so the
  browser can fetch /stream.m3u8 and accompanying fragments.
"""

import io
import json
import logging
import os
import socket
import socketserver
import subprocess
from http import server
from threading import Condition
import time
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput, FfmpegOutput

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# unix timestamp of last written frame; updated by StreamingOutput.write()
last_frame_time = 0.0

# Path to WiFi configuration script
WIFI_CONFIG_SCRIPT = '/home/jsed/AccessPopup/installconfig.sh'

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            # update global timestamp so clients can detect stalls
            try:
                global last_frame_time
                last_frame_time = time.time()
            except Exception:
                pass
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        # allow cross-origin so remote devices / apps can fetch playlist and segments
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Range,Content-Type')
        self.send_header('Access-Control-Expose-Headers', 'Content-Range,Accept-Ranges,Content-Length')

    def log_message(self, format, *args):
        """Override to use logging instead of stderr"""
        logging.info(format % args)

    def do_GET(self):
        # Convenience: path without query
        path = self.path.split('?', 1)[0]

        if path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
            return

        if path == '/index.html':
            try:
                with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', len(content))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                logging.error(f"Failed to serve index.html: {e}")
                self.send_error(500)
                self.end_headers()
            return

        if path == '/stream.mjpg':
            # Legacy MJPEG endpoint (kept for compatibility)
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self._send_cors_headers()
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
            return

        if path == '/frame-info':
            # Return JSON with last frame timestamp so clients can detect stalls
            try:
                info = {"lastFrameUnix": last_frame_time}
                content = json.dumps(info).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(content))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                logging.warning(f'Failed to serve frame-info: {e}')
            return

        # Serve static files (playlist, fragments, index.html, etc.)
        try:
            rel_path = path.lstrip('/')
            # Prevent directory traversal attacks
            if '..' in rel_path or rel_path.startswith('/'):
                raise FileNotFoundError()

            fs_path = os.path.join(os.path.dirname(__file__), rel_path)
            if os.path.isdir(fs_path):
                # don't serve directories
                raise FileNotFoundError()

            if os.path.exists(fs_path) and os.path.isfile(fs_path):
                # Determine content type
                ext = os.path.splitext(fs_path)[1].lower()
                ctype = 'application/octet-stream'
                if ext == '.m3u8':
                    ctype = 'application/vnd.apple.mpegurl'
                elif ext in ('.ts', '.mpegts'):
                    ctype = 'video/MP2T'
                elif ext in ('.m4s', '.mp4'):
                    ctype = 'video/mp4'
                elif ext in ('.aac',):
                    ctype = 'audio/aac'
                elif ext in ('.wav',):
                    ctype = 'audio/wav'
                elif ext in ('.jpg', '.jpeg'):
                    ctype = 'image/jpeg'
                elif ext == '.json':
                    ctype = 'application/json'
                elif ext == '.css':
                    ctype = 'text/css'
                elif ext == '.js':
                    ctype = 'application/javascript'
                file_size = os.path.getsize(fs_path)
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                # allow byte-range requests for efficient HLS fetching
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(file_size))
                self._send_cors_headers()
                self.end_headers()
                # Stream the file in binary mode in chunks
                with open(fs_path, 'rb') as f:
                    try:
                        while True:
                            chunk = f.read(64 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    except BrokenPipeError:
                        # client disconnected
                        pass
                return
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.warning(f'Error while serving static file {path}: {e}')

        # If we get here, nothing matched: 404
        self.send_error(404)
        self.end_headers()

    def do_POST(self):
        """Handle POST requests for configuration endpoints"""
        path = self.path.split('?', 1)[0]

        if path == '/api/configure-wifi':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8'))

                ssid = data.get('ssid', '')
                password = data.get('password', '')

                if not ssid:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    response = json.dumps({"status": "error", "message": "SSID is required"})
                    self.wfile.write(response.encode('utf-8'))
                    return

                # Call the WiFi configuration script
                logging.info(f"Configuring WiFi: {ssid}")
                result = subprocess.run(
                    [WIFI_CONFIG_SCRIPT, ssid, password],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    response = json.dumps({
                        "status": "success",
                        "message": f"WiFi configuration initiated for {ssid}",
                        "output": result.stdout
                    })
                    self.wfile.write(response.encode('utf-8'))
                    logging.info(f"WiFi configuration successful for {ssid}")
                else:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    response = json.dumps({
                        "status": "error",
                        "message": "WiFi configuration failed",
                        "error": result.stderr
                    })
                    self.wfile.write(response.encode('utf-8'))
                    logging.error(f"WiFi configuration failed: {result.stderr}")

            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = json.dumps({"status": "error", "message": "Invalid JSON"})
                self.wfile.write(response.encode('utf-8'))
            except subprocess.TimeoutExpired:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = json.dumps({"status": "error", "message": "Configuration timeout"})
                self.wfile.write(response.encode('utf-8'))
            except Exception as e:
                logging.error(f"Error handling WiFi configuration: {e}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = json.dumps({"status": "error", "message": str(e)})
                self.wfile.write(response.encode('utf-8'))
            return

        # Unknown POST endpoint
        self.send_error(404)
        self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_http_server():
    address = ('0.0.0.0', 8000)
    http_server = StreamingServer(address, StreamingHandler)
    logging.info("HTTP server listening on %s:%d", address[0], address[1])
    http_server.serve_forever()


def main():
    # Initialize camera
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
    global output
    enc = H264Encoder(repeat=True)

    # Build ffmpeg command fragment for FfmpegOutput:
    # - read camera H264 from stdin (-f h264 -i -)
    # - capture ALSA default device as audio (-f alsa -ac 1 -ar 16000 -i default)
    # - low-quality AAC audio (-b:a 32k), single channel 16k sample-rate to reduce bitrate/latency
    # - copy video stream (camera already provides H264), let ffmpeg mux video+audio into HLS
    # - produce HLS (stream.m3u8) in the script directory
    ffmpeg_args = (
        "-f hls -hls_time 0.5 -hls_list_size 50 -hls_flags delete_segments -hls_allow_cache 0 -nostdin hls/stream.m3u8"
    )

    # Use FfmpegOutput which starts ffmpeg and accepts H264 data on stdin from Picamera2
    output = FfmpegOutput(ffmpeg_args, audio=True)
    picam2.start_recording(enc, output)
    logging.info("Started Picamera2 recording and ffmpeg HLS packaging (video + audio)")

    try:
        run_http_server()
    finally:
        logging.info("Shutting down recording and HTTP server...")
        try:
            picam2.stop_recording()
        except Exception:
            pass


if __name__ == '__main__':
    try:
        os.remove("/home/jsed/PiCameraTutorials/*.m3u8")
        os.remove("/home/jsed/PiCameraTutorials/*.ts" )
    except:
        pass
    with open(os.path.join("/home/jsed/PiCameraTutorials","stream.m3u8"), "w") as fp:
        pass
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
