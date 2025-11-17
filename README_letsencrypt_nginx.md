Goal

Provide a minimal, safe nginx + Let's Encrypt (Certbot) setup for the BabyMonitor Python MJPEG server.

Prerequisites
- A domain name you control (e.g. mybabymonitor.example).
- DNS A record pointing the domain to your Pi's public IP (or to your router's public IP if port-forwarding).
- Port forwarding on your router: forward TCP 80 and 443 to the Pi (same machine running nginx/Python server).
- The Python server should continue to listen on localhost:8000 (no TLS). nginx will terminate TLS and proxy to it.

Files created
- `nginx_babymonitor.conf` — example nginx site config (in this repo). Copy to `/etc/nginx/sites-available/babymonitor` and symlink into `sites-enabled`.

High-level steps (copy/paste on the Raspberry Pi, run as root or with sudo)

1) Install nginx and certbot

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

2) Create webroot directory for certbot ACME challenge

```bash
sudo mkdir -p /var/www/certbot
sudo chown -R www-data:www-data /var/www/certbot
```

3) Put the nginx config in place

Copy the provided `nginx_babymonitor.conf` to `/etc/nginx/sites-available/babymonitor`.

```bash
# example -- run from the project directory where the file exists
sudo cp ~/PiCameraTutorials/nginx_babymonitor.conf /etc/nginx/sites-available/babymonitor
sudo ln -s /etc/nginx/sites-available/babymonitor /etc/nginx/sites-enabled/
```

4) Test nginx config and start nginx

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl enable --now nginx
```

5) Obtain a Let's Encrypt certificate using certbot

Replace `your.domain.example` with your real domain in the certbot command.

```bash
# certbot will place the certificates under /etc/letsencrypt/live/your.domain.example/
sudo certbot --nginx -d your.domain.example
```

Certbot will:
- Temporarily modify nginx to serve the ACME challenge or use the webroot path.
- Request certificates from Let's Encrypt.
- Install the certificates and reload nginx to enable HTTPS.

6) Confirm HTTPS works from your phone

- Visit: https://your.domain.example/ on the iPhone (use full URL including https and the domain). The page should load via nginx and proxy to the Python server.
- If the Python server is still on port 8000 and not using TLS, nginx will forward requests over plain HTTP, and TLS client hellos will no longer hit the Python server (this removes the 400 logs containing TLS garbage).

Notes and tuning

- Long-lived streaming connections (MJPEG / audio) need proxy buffering disabled and longer timeouts. The example config sets `proxy_buffering off; proxy_read_timeout 3600s;` etc.
- If you plan to access the service only locally (no public domain), Let's Encrypt won't be able to issue a cert for a private IP. Alternatives:
  - Use a real domain + port-forwarding.
  - Use a reverse tunnel (ngrok, Cloudflare Tunnel) that provides TLS.
  - Use self-signed certs (manual trust needed on the iPhone) — not recommended.
  - Use Caddy as an alternative: Caddy can automatically obtain certificates and is easier to configure on small devices.

Troubleshooting

- If certbot fails with "Timeout during connect", ensure port 80 is reachable from the public internet and DNS is set correctly.
- If nginx gives 502/504 when proxying, check that the Python server is running and listening on 127.0.0.1:8000 and that firewall allows localhost connections.
- If iPhone still tries HTTPS then fails, check for HSTS or cached redirects in Safari; try a private tab or clear cache. Ensure you typed https:// explicitly.

Optional: systemd service for Python server

Create `/etc/systemd/system/babymonitor.service` (adjust paths and user):

```
[Unit]
Description=BabyMonitor Python server
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/PiCameraTutorials
ExecStart=/usr/bin/python3 /home/pi/PiCameraTutorials/mjpeg_server.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Enable/start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now babymonitor.service
sudo journalctl -u babymonitor -f
```

If you'd like I can also:
- Generate a fully-done nginx config with `server_name` already substituted if you tell me the domain.
- Add a small systemd unit file in the repo (ready to copy) and/or a script that deploys the nginx file and runs certbot (I won't run it on your Pi, I'll only provide the script).

Security reminder

Don't expose your Pi's ports to the public internet without considering security. Use a strong password on the Pi or better SSH keys. Consider restricting access via IP, basic auth, or putting the service behind a VPN if you need remote access without public exposure.
