# Server Monitor

![GitHub Ready](https://img.shields.io/badge/GitHub-ready-black?logo=github)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

Configurable server and service monitor for detecting outages, tracking recovery time, recording incidents, and sending local or remote alerts.

This project is intended for environments where it is important to know:

- when a service goes down
- when it comes back online
- how long the outage lasted
- what error was detected during the incident

## Features

- Monitoring via `http`, `tcp`, or `ping`
- Configurable outage and recovery thresholds
- Persistent incident history in CSV format
- Runtime log output to file
- Local Windows alert with sound and popup window
- SMTP email alert support
- Webhook alert support

## Requirements

- Python 3.10 or later
- Windows if local sound and popup alerts are required

## Installation

Clone the repository or download the project, then install the dependency:

```bash
pip install -r requirements.txt
```

If `pip` is not directly available:

```bash
python -m pip install -r requirements.txt
```

## Quick Start

1. Copy the example configuration file:

```powershell
Copy-Item .\config.example.json .\config.json
```

2. Edit `config.json` with the URL, host, or port you want to monitor.

3. Run a single test check:

```bash
python monitor_servidor.py --config config.json --once
```

4. Start continuous monitoring:

```bash
python monitor_servidor.py --config config.json
```

## Usage

The monitor supports three check types:

- `http`: validates a URL and compares the response code against expected values
- `tcp`: validates that a host and port accept connections
- `ping`: validates basic connectivity to a host

### UniFi Example

A local UniFi console commonly uses a self-signed certificate and may return a redirect instead of `200`. A typical configuration looks like this:

```json
{
  "target": {
    "name": "UniFi or local server",
    "type": "http",
    "url": "https://YOUR_HOST_OR_IP:8443/",
    "method": "GET",
    "timeout_seconds": 10,
    "expected_status_codes": [200, 302],
    "allow_redirects": false,
    "verify_ssl": false
  },
  "monitoring": {
    "check_interval_seconds": 10,
    "failure_threshold": 2,
    "recovery_threshold": 1,
    "heartbeat_every_checks": 30
  }
}
```

In this setup:

- `verify_ssl: false` avoids failures caused by a self-signed certificate
- `allow_redirects: false` keeps the original response visible to the monitor
- `expected_status_codes: [200, 302]` treats both responses as healthy

## Configuration

The public example file is [config.example.json](./config.example.json).

Main configuration sections:

- `target`: what to monitor and how to check it
- `monitoring`: interval and outage detection thresholds
- `alerts`: email, webhook, and desktop notification settings
- `files`: output paths for logs, state, and incident history

Key monitoring settings:

- `check_interval_seconds`: how often to run checks
- `failure_threshold`: number of consecutive failures required to declare an outage
- `recovery_threshold`: number of consecutive successful checks required to declare recovery

Example:

- `check_interval_seconds = 10`
- `failure_threshold = 2`

With that configuration, an outage is confirmed after 2 consecutive failed checks, usually in about 20 seconds.

## Alerts

The monitor can send alerts through three channels:

- `desktop`
- `email`
- `webhook`

### Desktop Alerts

When `alerts.desktop.enabled` is set to `true`, the monitor can:

- play a loud alert sound when an outage is detected
- show a popup window with the outage details

Useful desktop options:

- `sound_on_down`
- `popup_on_down`
- `sound_repeat_down`
- `sound_on_recovery`
- `popup_on_recovery`

### Email Alerts

SMTP configuration requires:

- `smtp_server`
- `smtp_port`
- `username`
- `password`
- `sender_email`
- `recipient_emails`

### Webhook Alerts

Webhook payloads are supported for:

- `discord`
- `slack`
- `teams`
- `generic`

## Output Files

The following files are created during execution:

- `data/monitor.log`: general runtime log
- `data/state.json`: current monitoring state
- `data/incidentes.csv`: outage and recovery history

## Incident History

Each entry saved to `data/incidentes.csv` includes:

- monitored target name
- check type
- hostname of the machine running the monitor
- outage start time
- recovery time
- duration in seconds
- human-readable duration
- failure reason
- failure details
- recovery details

## Security

This project is prepared to be published without exposing real local configuration.

The following are excluded from version control:

- `config.json`
- `data/`
- `__pycache__/`

Use `config.example.json` as the public template and keep real values only in your local `config.json`.

## Contributing

Contributions are welcome for:

- new alert channels
- monitoring improvements
- better Windows service integration
- bug fixes and documentation updates

If you plan to contribute:

1. Fork the repository.
2. Create a feature branch.
3. Make your changes with clear commits.
4. Test the behavior locally.
5. Open a pull request with a short description of the change.

## Project Structure

```text
.
|-- monitor_servidor.py
|-- config.example.json
|-- requirements.txt
|-- README.md
`-- data/
```
