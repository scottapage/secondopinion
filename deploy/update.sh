#!/usr/bin/env bash
# Pulls the deployed branch from GitHub and restarts services when it changes.
# Run every minute by so-update.timer. FORCE=1 redeploys regardless.
# The branch comes from /var/lib/secondopinion/deploy-branch (default main). The superadmin switches it from
# Admin > Settings ("Site version"): `v1` is the frozen first release, `main` is the current site.
set -euo pipefail
LOG=/var/lib/secondopinion/deploy.log
trap 'echo "$(date -Is) FAILED at line $LINENO" >> $LOG' ERR
APP=/opt/secondopinion/app
VENV=/opt/secondopinion/venv
BRANCH=$(cat /var/lib/secondopinion/deploy-branch 2>/dev/null | tr -cd 'A-Za-z0-9._/-' || true)
BRANCH=${BRANCH:-main}
cd "$APP"
git config --system --get-all safe.directory | grep -qx "$APP" || git config --system --add safe.directory "$APP"
git fetch -q origin "$BRANCH"
OLD=$(git rev-parse HEAD); NEW=$(git rev-parse "origin/$BRANCH")
if [ "$OLD" = "$NEW" ] && [ "${FORCE:-0}" != "1" ]; then exit 0; fi
git checkout -q -B "$BRANCH" "origin/$BRANCH"
git reset -q --hard "origin/$BRANCH"
if ! cmp -s requirements.txt /opt/secondopinion/.requirements.installed; then
  "$VENV/bin/pip" install -q -r requirements.txt
  cp requirements.txt /opt/secondopinion/.requirements.installed
fi
bash deploy/postinstall.sh || echo "$(date -Is) postinstall failed" >> $LOG
# Caddy config: substitute this droplet's sslip.io hostname for testing before DNS is live
IP=$(curl -s --max-time 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address || hostname -I | awk '{print $1}')
sed "s/__IPHOST__/${IP//./-}.sslip.io/" deploy/Caddyfile > /etc/caddy/Caddyfile.new
if ! cmp -s /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile; then mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile; systemctl reload caddy || systemctl restart caddy; fi
for u in so-web.service so-worker.service so-update.service so-update.timer; do
  cmp -s "deploy/$u" "/etc/systemd/system/$u" || { cp "deploy/$u" /etc/systemd/system/; RELOAD=1; }
done
[ "${RELOAD:-0}" = "1" ] && systemctl daemon-reload
systemctl restart so-web so-worker
echo "$(date -Is) deployed $BRANCH $NEW" >> $LOG
tail -n 50 $LOG > $LOG.tmp && mv $LOG.tmp $LOG && chown so:so $LOG
