#!/usr/bin/python3

import io
import json
import logging
import os
import socket
import socketserver
from http import server
from threading import Condition, Thread, Lock
import time
import pyaudio

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# Global variables - properly initialized
output = None
picam2 = None
audio_manager = None
stream_lock = Lock()
last_frame_time = 0.0

class AudioManager:
    def __init__(self, sample_rate=48000, channels=1, chunk_size=1024):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.running = False
        self.audio = pyaudio.PyAudio()
        self.stream = None
        
    def start(self):
        if self.stream is None:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
        self.running = True
        logging.info("Audio stream started")
        
    def stop(self):
        self.running = False
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                logging.error(f"Error stopping audio stream: {e}")
            self.stream = None
        
    def restart(self):
        """Gracefully restart the audio stream"""
        logging.info("Restarting audio stream...")
        self.stop()
        time.sleep(0.5)
        try:
            self.start()
            logging.info("Audio stream restarted successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to restart audio stream: {e}")
            return False
        
    def read_audio(self):
        """Read a single chunk of audio data"""
        if not self.stream or not self.running:
            return None
        try:
            data = self.stream.read(self.chunk_size, exception_on_overflow=False)
            return data
        except Exception as e:
            logging.error(f"Audio read error: {e}")
            return None
            
    def get_audio_info(self):
        return {
            "sampleRate": self.sample_rate,
            "channels": self.channels,
            "encoding": "PCM",
            "bitDepth": 16
        }


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        global last_frame_time
        with self.condition:
            self.frame = buf
            last_frame_time = time.time()
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Reduce logging noise
        pass
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            try:
                with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', len(content))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                logging.error(f"Failed to serve index.html: {e}")
                self.send_error(500)
                self.end_headers()
        elif self.path == '/stream.mjpg':
            # MJPEG stream
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    if frame:
                        self.wfile.write(b'--FRAME\r\n')
                        self.wfile.write(b'Content-Type: image/jpeg\r\n')
                        self.wfile.write(f'Content-Length: {len(frame)}\r\n\r\n'.encode())
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(f'Video client disconnected: {e}')
        elif self.path == '/audio':
            self.send_response(200)
            self.send_header('Content-Type', 'audio/pcm')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                try:
                    self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
                while True:
                    data = audio_manager.read_audio()
                    if data:
                        self.wfile.write(data)
                        try:
                            self.wfile.flush()
                        except Exception:
                            pass
                    else:
                        break
            except Exception as e:
                logging.warning(f'Audio client disconnected: {e}')
        elif self.path == '/frame-info':
            try:
                info = {"lastFrameUnix": last_frame_time}
                content = json.dumps(info).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(content))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                logging.warning(f'Failed to serve frame-info: {e}')
        elif self.path == '/audio-info':
            try:
                info = audio_manager.get_audio_info()
                content = json.dumps(info).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(content))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                logging.warning(f'Failed to serve audio-info: {e}')
        elif self.path == '/reset-camera':
            self.handle_reset_camera()
        elif self.path == '/reset-audio':
            self.handle_reset_audio()
        else:
            self.send_error(404)
            self.end_headers()

    def handle_reset_camera(self):
        """Reset camera stream"""
        try:
            global picam2, output
            logging.info("Resetting camera...")
            
            with stream_lock:
                picam2.stop_recording()
                time.sleep(1)
                picam2.close()
                time.sleep(0.5)
                
                # Reinitialize camera
                picam2 = Picamera2()
                picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
                output = StreamingOutput()
                picam2.start_recording(MJPEGEncoder(), FileOutput(output))
            
            logging.info("Camera reset successful")
            self.send_response(200)
            response = json.dumps({"status": "success", "message": "Camera reset"}).encode('utf-8')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            logging.error(f"Camera reset failed: {e}")
            self.send_response(500)
            response = json.dumps({"status": "error", "message": str(e)}).encode('utf-8')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response)

    def handle_reset_audio(self):
        """Reset audio stream"""
        try:
            logging.info("Resetting audio...")
            success = audio_manager.restart()
            
            if success:
                logging.info("Audio reset successful")
                self.send_response(200)
                response = json.dumps({"status": "success", "message": "Audio reset"}).encode('utf-8')
            else:
                self.send_response(500)
                response = json.dumps({"status": "error", "message": "Failed to restart audio"}).encode('utf-8')
            
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            logging.error(f"Audio reset failed: {e}")
            self.send_response(500)
            response = json.dumps({"status": "error", "message": str(e)}).encode('utf-8')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response)


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_http_server():
    address = ('0.0.0.0', 8000)
    http_server = StreamingServer(address, StreamingHandler)
    http_server.serve_forever()


def main():
    global picam2, output, audio_manager
    
    # Initialize camera
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
    output = StreamingOutput()
    # MJPEG with quality 80 - good balance between quality and CPU usage
    picam2.start_recording(MJPEGEncoder(), FileOutput(output))
    logging.info("Camera started successfully")

    # Initialize audio
    audio_manager = AudioManager(sample_rate=48000, channels=1, chunk_size=512)
    audio_manager.start()

    # Run HTTP server
    try:
        run_http_server()
    finally:
        picam2.stop_recording()
        audio_manager.stop()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
