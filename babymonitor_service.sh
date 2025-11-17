# copy unit (use sudo or be root)
sudo tee /etc/systemd/system/babymonitor.service > /dev/null <<EOF
[Unit]
Description=Baby Monitor (MJPEG + Audio) service
After=network-online.target

[Service]
Type=simple
User=jsed
Group=jsed
WorkingDirectory=/home/jsed/PiCameraTutorials
ExecStart=/home/jsed/PiCameraTutorials/venv/bin/python3 /home/jsed/PiCameraTutorials/mjpeg_server.py
Restart=on-failure
RestartSec=15
StartLimitBurst=5
StartLimitInterval=120
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
# reload systemd to pick up new unit
sudo systemctl daemon-reload
# enable on boot
sudo systemctl enable babymonitor.service
# start now
sudo systemctl start babymonitor.service
