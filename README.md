# CheckMeIn Kiosk Client

A super-thin Python client for Raspberry Pi that runs at the facility entrance.

## What it does

1. **Displays current attendance** — fetches from the backend API, renders a local HTML page, auto-refreshes every 60 seconds
2. **Listens for badge scans** — reads USB barcode/QR scanner input, sends signed POST to backend
3. **Signs all requests** — Ed25519 signatures so the backend can trust the kiosk

## Setup

```bash
# Install dependencies (system-wide)
sudo apt update
sudo apt install python3-requests python3-nacl python3-evdev

# Generate a keypair (one-time)
python generate_keys.py
# → writes client.key (keep on Pi)
# → prints public key hex (paste into backend .env as KIOSK_PUBLIC_KEY)

# Copy and edit config
cp config.example.json config.json
# Edit backend_url, usb_device path, etc.
```

## Autostart (Raspberry Pi)

To have the kiosk start automatically at boot:

```bash
mkdir -p ~/.config/autostart
cp ~/checkmein-client/checkmein-kiosk.desktop ~/.config/autostart/
```

## Running

```bash
# Development (no USB scanner, reads from stdin)
python client.py

# Production (Raspberry Pi with Chromium kiosk)
./kiosk.sh
```

### Testing without a scanner

When `usb_device` is empty in `config.json`, the client reads participant IDs from stdin. Just type an ID and press Enter to simulate a scan.

## Configuration

| Key | Description |
|-----|-------------|
| `backend_url` | Full URL to the CheckMeIn backend (e.g. `https://checkmein.example.com`) |
| `private_key_path` | Path to the Ed25519 private key file |
| `usb_device` | Linux input device path (e.g. `/dev/input/event0`), empty for stdin |
| `listen_port` | Local HTTP server port (default `8080`) |

## Architecture

```
┌──────────────┐       signed GET        ┌──────────────┐
│  HTTP Server │  ←── /api/attendance ──  │   Backend    │
│  :8080       │                          │  (Next.js)   │
│              │       signed POST        │              │
│  USB Listener│  ──→ /api/scan ────────→ │              │
└──────────────┘                          └──────────────┘
     ↑                                         ↑
  Chromium                               KIOSK_PUBLIC_KEY
  kiosk mode                              in .env
```

## Files

- `client.py` — Main process (HTTP server + attendance fetcher + USB scanner)
- `generate_keys.py` — One-time keypair generator
- `kiosk.sh` — Pi startup script (launches client + Chromium)
- `checkmein-kiosk.desktop` — Desktop entry for autostart
- `config.json` — Runtime configuration (not committed)
- `client.key` — Private key (not committed)
