#!/bin/bash
# Installs and starts babymonitorWebRTC.service as a systemd user unit.
set -e

SERVICE_NAME="babymonitorWebRTC.service"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

tee "$UNIT_DIR/$SERVICE_NAME" > /dev/null <<EOF
[Unit]
Description=Baby Monitor WebRTC service
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/jsed/PiCameraWebRTC/
ExecStartPre=/bin/bash -c 'until ip route | grep -q default; do sleep 2; done'
ExecStart=/home/jsed/PiCameraTutorials/venv/bin/python3 /home/jsed/PiCameraWebRTC/server.py
Restart=always
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=60
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment="PYTHONPATH=/home/jsed/PiCameraWebRTC"

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start "$SERVICE_NAME"
echo "Service $SERVICE_NAME installed, enabled, and started."
echo "Check status: systemctl --user status $SERVICE_NAME"
echo "View logs:    journalctl --user -u $SERVICE_NAME -f"
