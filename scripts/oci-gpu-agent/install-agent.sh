#!/usr/bin/env bash
# Install the OCI GPU agent on a VM.GPU.* instance as a systemd service.
#
# Requirements on the instance:
#   - nvidia-smi available (NVIDIA driver installed)
#   - python3 + oci SDK:  pip3 install --user oci   (or system-wide)
#
# Usage (run as root or with sudo on EACH GPU instance):
#   sudo bash install-agent.sh [interval_seconds]
set -Eeuo pipefail

INTERVAL="${1:-60}"
AGENT_DIR="/opt/oci-gpu-agent"
SERVICE="oci-gpu-agent"

mkdir -p "$AGENT_DIR"
cp "$(dirname "$(readlink -f "$0")")/oci_gpu_agent.py" "$AGENT_DIR/oci_gpu_agent.py"
chmod +x "$AGENT_DIR/oci_gpu_agent.py"

cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=OCI GPU agent (nvidia-smi -> OCI Monitoring custom_gpu)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $AGENT_DIR/oci_gpu_agent.py --interval $INTERVAL
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
echo "installed: systemctl status $SERVICE"
