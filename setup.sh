#!/bin/bash
# setup.sh — install and configure Yamaha R-N500 Controller on Raspberry Pi
#
# Usage:
#   ./setup.sh            — full install (Python env + system packages)
#   ./setup.sh --https    — full install + generate TLS cert (needed for PWA on desktop)
#   ./setup.sh --help     — show this help
#
# What it does:
#   1. Installs system packages: python3-venv, git, curl
#   2. Creates Python venv and installs pip packages (requirements.txt)
#   3. Copies config.yaml.example → config.yaml (if missing)
#   4. [--https] Installs mkcert, generates cert for this machine's IP + hostname
#      and prints step-by-step instructions for trusting the CA on client devices
#
# Generated cert covers: <IP>, <hostname>, <hostname>.local, localhost
#
# After setup:
#   ./run.sh          — starts server (HTTPS auto-used if certs exist)
#   ./run.sh --http   — force plain HTTP
#   ./run.sh --https  — force HTTPS (error if no certs)
#
set -e
cd "$(dirname "$0")"

HTTPS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --https)   HTTPS=1; shift ;;
    --help|-h)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown option: $1  (try --help)"; exit 1 ;;
  esac
done

echo ""
echo "  Yamaha R-N500 Controller — Setup"
echo "  ─────────────────────────────────────────"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "📦  Installing system packages..."
sudo apt update -qq
sudo apt install -y python3-venv git curl

# ── 2. Python environment ─────────────────────────────────────────────────────
echo "🐍  Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✅  Python environment ready."

# ── 3. Config file ────────────────────────────────────────────────────────────
if [[ ! -f config.yaml ]]; then
  cp config.yaml.example config.yaml
  echo ""
  echo "📝  config.yaml created — set your Yamaha amplifier's IP address:"
  echo "    nano config.yaml"
else
  echo "✅  config.yaml already exists."
fi

# ── 4. HTTPS / mkcert (optional) ─────────────────────────────────────────────
if [[ $HTTPS -eq 1 ]]; then
  echo ""
  echo "🔒  Setting up HTTPS with mkcert..."

  if ! command -v mkcert &>/dev/null; then
    echo "📦  Installing mkcert..."
    sudo apt install -y mkcert
  else
    echo "✅  mkcert already installed: $(mkcert -version 2>/dev/null || echo 'unknown version')"
  fi

  LOCAL_IP=$(hostname -I | awk '{print $1}')
  if [[ -z "$LOCAL_IP" ]]; then
    echo "❌  Could not detect local IP. Set LOCAL_IP manually and rerun."
    exit 1
  fi
  LOCAL_HOST=$(hostname 2>/dev/null || true)

  # Build list of SANs: IP + hostname + hostname.local + localhost
  SANS=("$LOCAL_IP")
  [[ -n "$LOCAL_HOST" ]] && SANS+=("$LOCAL_HOST" "${LOCAL_HOST}.local")
  SANS+=("localhost" "127.0.0.1")

  echo "🌐  Generating TLS cert for: ${SANS[*]}"
  mkcert "${SANS[@]}"

  CAROOT=$(mkcert -CAROOT)
  CERT_FILE=$(ls "${LOCAL_IP}"*.pem 2>/dev/null | grep -v '\-key' | head -1 || true)

  echo ""
  echo "✅  Certificate created: ${CERT_FILE}"
  echo "    CA root:             ${CAROOT}/rootCA.pem"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  IMPORTANT — Trust the CA on every device that will use"
  echo "  the app in a browser (one-time setup per device):"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  macOS / Safari / Chrome / Brave:"
  echo "    1. Copy the CA file to your Mac:"
  echo "       scp ${LOCAL_HOST}:${CAROOT}/rootCA.pem ~/Downloads/yamaha-ca.pem"
  echo "    2. Open it — Keychain Access opens automatically"
  echo "    3. Find 'mkcert' in the certificate list"
  echo "    4. Double-click → Trust → Always Trust  (enter your Mac password)"
  echo ""
  echo "  iOS / iPadOS:"
  echo "    1. AirDrop the file:  ${CAROOT}/rootCA.pem"
  echo "       Or open:  https://${LOCAL_IP}:8080  — install prompted on first visit"
  echo "    2. Settings → General → VPN & Device Management → install profile"
  echo "    3. Settings → General → About → Certificate Trust Settings → enable"
  echo ""
  echo "  Android:"
  echo "    Settings → Security → Install certificate → CA Certificate"
  echo "    File: download rootCA.pem from http://${LOCAL_IP}:8080/static/ first"
  echo "    (or send via email/AirDrop)"
  echo ""
  echo "  Windows:"
  echo "    Double-click rootCA.pem → Install → 'Trusted Root Certification Authorities'"
  echo ""
  echo "  Linux:"
  echo "    sudo cp ${CAROOT}/rootCA.pem /usr/local/share/ca-certificates/mkcert.crt"
  echo "    sudo update-ca-certificates"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
LOCAL_IP_FINAL=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
LOCAL_HOST_FINAL=$(hostname 2>/dev/null || echo "")
PROTO="http"
CERTS_FOUND=$(ls 192.168.*.pem 2>/dev/null | grep -v '\-key' | head -1 || true)
[[ -n "$CERTS_FOUND" ]] && PROTO="https"

echo ""
echo "✅  Setup complete!"
echo ""
echo "  Next steps:"
if [[ ! -f config.yaml ]] || grep -q "192.168.x.x" config.yaml 2>/dev/null; then
  echo "  1. Edit config.yaml — set your Yamaha's IP:"
  echo "     nano config.yaml"
  echo ""
  echo "  2. Start the server:"
else
  echo "  Start the server:"
fi
echo "     ./run.sh"
echo ""
echo "  Then open in your browser:"
echo "     ${PROTO}://${LOCAL_IP_FINAL}:8080"
[[ -n "$LOCAL_HOST_FINAL" ]] && echo "     ${PROTO}://${LOCAL_HOST_FINAL}:8080"
echo ""
