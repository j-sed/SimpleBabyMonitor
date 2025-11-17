#!/usr/bin/python3

import io
import json
import logging
import os
import asyncio
import socket
import socketserver
from http import server
from threading import Condition, Thread
import time
import numpy as np
import pyaudio
from fractions import Fraction
from typing import Optional, Dict

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# Audio handling class
class AudioManager:
    def __init__(self, sample_rate=48000, channels=1, chunk_size=1024):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.running = False
        
        # Initialize PyAudio
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
        
    def stop(self):
        self.running = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self.audio.terminate()
        
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


# unix timestamp of last written frame; updated by StreamingOutput.write()
last_frame_time = 0.0

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
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        elif self.path == '/audio':
            self.send_response(200)
            self.send_header('Content-Type', 'audio/pcm')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                # try to disable Nagle's algorithm to reduce latency
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
                logging.warning(f'Audio streaming client disconnected: {e}')
        elif self.path == '/frame-info':
            # Return JSON with last frame timestamp so clients can detect stalls
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
            # Provide audio stream configuration
            info = audio_manager.get_audio_info()
            content = json.dumps(info).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(content))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)
            self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def run_http_server():
    address = ('0.0.0.0', 8000)
    http_server = StreamingServer(address, StreamingHandler)
    http_server.serve_forever()

def main():
    # Initialize camera
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
    global output
    output = StreamingOutput()
    picam2.start_recording(JpegEncoder(), FileOutput(output))

    # Initialize audio
    global audio_manager
    # use a smaller chunk size to reduce per-chunk latency
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
        # main is synchronous (starts the HTTP server in the foreground)
        main()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
