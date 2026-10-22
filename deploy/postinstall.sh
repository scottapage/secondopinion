#!/usr/bin/env bash
# One-off extras that need root: a headless Chromium for the catalogue/benefits research agents
# (provider plan pages render their plan cards with JavaScript).
set -euo pipefail
MARK=/opt/secondopinion/.postinstall-v1
[ -f "$MARK" ] && exit 0
export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
mkdir -p /opt/pw-browsers
/opt/secondopinion/venv/bin/playwright install --with-deps chromium >> /var/lib/secondopinion/deploy.log 2>&1
chmod -R a+rX /opt/pw-browsers
touch "$MARK"
