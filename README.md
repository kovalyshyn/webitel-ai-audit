# Webitel AI Audit

Automated call evaluation service for [Webitel](https://webitel.com) contact centers.

When a call ends, Webitel sends a webhook to this service. The service downloads the recording, transcribes it, and scores it against a supervisor-defined scorecard — all without manual effort.

## How it works

```
Webitel webhook (GET)
        │
        ▼
1. Fetch call details + recording file from Webitel
2. Download audio
3. Transcribe with AI provider (ElevenLabs or Gemini)
4. Save transcript to Webitel
5. Fetch scorecard questions from Webitel
6. Evaluate transcript against scorecard (Gemini)
7. Save audit score to Webitel
```

The endpoint returns `200 OK` immediately. All processing runs in the background.
Duplicate requests for the same call UUID are silently ignored.


## Features

- **Non-blocking** — webhook returns instantly, processing is async
- **Stereo-aware** — automatically splits stereo recordings into agent/customer channels before transcription
- **Skip existing** — if a transcript already exists for the call, transcription is skipped
- **Pluggable providers** — separate providers for transcription and evaluation
- **Structured logs** — every log line includes `call_uuid` for easy debugging

## Providers

| Role | Provider | Notes |
|------|----------|-------|
| Transcription | **ElevenLabs** Scribe v1 | Word-level timestamps, stereo channel split |
| Transcription | **Gemini** | Fallback option |
| Evaluation | **Gemini** | Scores answers against scorecard questions |

## Requirements

- Python 3.11+
- `ffmpeg` — required for stereo channel detection and splitting
- ElevenLabs API key (for transcription)
- Google Gemini API key (for evaluation)

## Installation

```bash
git clone https://github.com/your-org/webitel-ai-audit
cd webitel-ai-audit
pip install -r requirements.txt
```

Verify `ffmpeg` is available:
```bash
ffmpeg -version
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
# Webitel
WEBITEL_URL=https://your-instance.webitel.com
WEBITEL_TOKEN=your-webitel-token

# Transcription provider: "elevenlabs" or "gemini"
TRANSCRIPTION_PROVIDER=elevenlabs
TRANSCRIPTION_LANGUAGE=uk

# ElevenLabs
ELEVENLABS_API_KEY=your-elevenlabs-key
ELEVENLABS_MODEL=scribe_v1

# Evaluation provider: "gemini"
EVALUATION_PROVIDER=gemini

# Gemini
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=gemini-2.5-flash
```

## Running

### Development

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Production (Linux / Debian — systemd)

**1. Create a dedicated user:**
```bash
sudo useradd --system --no-create-home --shell /bin/false webitel-audit
```

**2. Clone and install:**
```bash
sudo mkdir -p /opt/webitel-ai-audit
sudo git clone https://github.com/your-org/webitel-ai-audit /opt/webitel-ai-audit
cd /opt/webitel-ai-audit

sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

sudo cp .env.example .env
sudo nano .env  # fill in your credentials

sudo chown -R webitel-audit:webitel-audit /opt/webitel-ai-audit
```

**3. Install ffmpeg:**
```bash
sudo apt update && sudo apt install -y ffmpeg
```

**4. Create systemd service:**
```bash
sudo nano /etc/systemd/system/webitel-ai-audit.service
```

```ini
[Unit]
Description=Webitel AI Audit Service
After=network.target

[Service]
Type=simple
User=webitel-audit
WorkingDirectory=/opt/webitel-ai-audit
EnvironmentFile=/opt/webitel-ai-audit/.env
ExecStart=/opt/webitel-ai-audit/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=webitel-ai-audit

[Install]
WantedBy=multi-user.target
```

**5. Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable webitel-ai-audit
sudo systemctl start webitel-ai-audit
sudo systemctl status webitel-ai-audit
```

**6. View logs:**
```bash
# All logs
journalctl -u webitel-ai-audit -f

# Filter by call UUID
journalctl -u webitel-ai-audit | grep "5bf297db"
```

**Useful commands:**
```bash
sudo systemctl restart webitel-ai-audit   # restart after config change
sudo systemctl stop webitel-ai-audit      # stop
sudo systemctl disable webitel-ai-audit   # remove from autostart
```

## Webitel Webhook Setup

In Webitel, configure a webhook on call end that sends:

```json
    {
        "trigger": {
            "disconnected": [
                {
                    "if": {
                        "expression": "+${variable_flow_billsec} > 60",
                        "then": [
                            {
                                "httpRequest": {
                                    "method": "GET",
                                    "path": {
                                        "uuid": "${variable_uuid}"
                                    },
                                    "url": "http://your-server:8000/evaluate?call_uuid=${uuid}&questionnaire_id=6"
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }
```

- `call_uuid` — the UUID of the completed call
- `questionnaire_id` — the ID of the audit scorecard form to evaluate against

## API

### `GET /evaluate`

Trigger evaluation for a call.

| Parameter | Type | Description |
|-----------|------|-------------|
| `call_uuid` | string | UUID of the call |
| `questionnaire_id` | int | ID of the Webitel audit form |

**Response:**
```json
{ "status": "accepted", "call_uuid": "5bf297db-..." }
```
or if already processing:
```json
{ "status": "already_processing", "call_uuid": "5bf297db-..." }
```

### `GET /health`

```json
{ "status": "ok", "in_progress": 2 }
```

## Project Structure

```
webitel-ai-audit/
├── main.py                 # FastAPI app, pipeline orchestration
├── config.py               # Settings (pydantic-settings, .env)
├── webitel.py              # Webitel API client (async httpx)
├── providers/
│   ├── base.py             # TranscriptionProvider / EvaluationProvider ABCs
│   ├── gemini.py           # Gemini provider (transcription + evaluation)
│   └── elevenlabs.py       # ElevenLabs provider (transcription, stereo-aware)
├── requirements.txt
└── .env.example
```

## Logs

Every log line includes the call UUID for easy filtering:

```
2026-04-15 18:10:01 INFO main [5bf297db-...]: Fetching call details
2026-04-15 18:10:02 INFO main [5bf297db-...]: Audio channels detected: 2
2026-04-15 18:10:02 INFO main [5bf297db-...]: Transcribing channel 0 (left / agent)...
2026-04-15 18:10:06 INFO main [5bf297db-...]: Transcribing channel 1 (right / customer)...
2026-04-15 18:10:09 INFO main [5bf297db-...]: Transcript saved: id=415
2026-04-15 18:10:10 INFO main [5bf297db-...]: Evaluating with provider=gemini
2026-04-15 18:10:14 INFO main [5bf297db-...]: Audit rate saved: id=88 score_required=8.2 score_optional=7.5
```

Filter logs for a specific call:
```bash
grep "5bf297db" uvicorn.log
```

## License

MIT
