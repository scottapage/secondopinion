#!/usr/bin/env bash
# Pulls the latest main from GitHub and restarts services when it changes.
# Run every minute by so-update.timer. FORCE=1 redeploys regardless.
set -euo pipefail
APP=/opt/secondopinion/app
VENV=/opt/secondopinion/venv
cd "$APP"
git fetch -q origin main
OLD=$(git rev-parse HEAD); NEW=$(git rev-parse origin/main)
if [ "$OLD" = "$NEW" ] && [ "${FORCE:-0}" != "1" ]; then exit 0; fi
git reset -q --hard origin/main
if ! cmp -s requirements.txt /opt/secondopinion/.requirements.installed; then
  "$VENV/bin/pip" install -q -r requirements.txt
  cp requirements.txt /opt/secondopinion/.requirements.installed
fi
# Caddy config: substitute this droplet's sslip.io hostname for testing before DNS is live
IP=$(curl -s --max-time 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address || hostname -I | awk '{print $1}')
sed "s/__IPHOST__/${IP//./-}.sslip.io/" deploy/Caddyfile > /etc/caddy/Caddyfile.new
if ! cmp -s /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile; then mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile; systemctl reload caddy || systemctl restart caddy; fi
for u in so-web.service so-worker.service so-update.service so-update.timer; do
  cmp -s "deploy/$u" "/etc/systemd/system/$u" || { cp "deploy/$u" /etc/systemd/system/; RELOAD=1; }
done
[ "${RELOAD:-0}" = "1" ] && systemctl daemon-reload
chown -R so:so "$APP"
systemctl restart so-web so-worker
echo "$(date -Is) deployed $NEW" >> /var/log/so-deploy.log
