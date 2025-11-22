import cv2
from flask import Flask, Response, request, jsonify

app = Flask(__name__)

# Initialize Video Capture
def initialize_camera():
    global camera
    camera = cv2.VideoCapture(0)  # Replace with your camera ID or URL

# Reset Camera and Audio Endpoints
@app.route('/reset', methods=['POST'])
def reset():
    initialize_camera()  # Reset the camera
    return jsonify({'status': 'success', 'message': 'Camera and audio reset.'})

# H.264 Video Streaming
@app.route('/video_feed')
def video_feed():
    initialize_camera()
    def generate_h264():
        while True:
            ret, frame = camera.read()
            if not ret:
                break
            # Encode the frame in H.264 format
            _, buffer = cv2.imencode('.h264', frame)
            frame_h264 = buffer.tobytes()
            # Yield the frame as a byte stream
            yield (b'--frame\r\n'
                   b'Content-Type: video/h264\r\n\r\n' + frame_h264 + b'\r\n')
    return Response(generate_h264(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    initialize_camera()
    app.run(host='0.0.0.0', port=5000, debug=True)
