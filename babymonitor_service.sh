# copy unit (use sudo or be root)
#sudo tee /etc/systemd/system/babymonitor.service > /dev/null <<EOF
sudo tee ~/.config/systemd/user/babymonitorUser.service > /dev/null <<EOF
[Unit]
Description=Baby Monitor (MJPEG + Audio) service ran as USER
After=network-online.target

[Service]
Type=simple
#Group=jsed
#User=jsed
WorkingDirectory=/home/jsed/PiCameraTutorials/
ExecStart=/home/jsed/PiCameraTutorials/server.py
Restart=on-failure
#RestartSec=30
#StartLimitBurst=5
#StartLimitInterval=200
StandardOutput=inherit
StandardError=inherit
Environment=PYTHONUNBUFFERED=1
Environment="PYTHONPATH=/home/jsed/PiCameraTutorials"
[Install]
WantedBy=default.target
EOF
# reload systemd to pick up new unit
#sudo systemctl daemon-reload
systemctl --user daemon-reload
# enable on boot
#sudo systemctl enable babymonitor.service
systemctl --user enable babymonitorUser.service
# start now
#sudo systemctl start babymonitor.service
systemctl --user start babymonitorUser.service
