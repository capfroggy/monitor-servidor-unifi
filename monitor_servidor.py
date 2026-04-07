#!/usr/bin/env python3
"""
Monitor configurable de servidores con alertas, historial de incidentes
y duracion de caidas.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import smtplib
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

DISPLAY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"


@dataclass
class CheckResult:
    is_up: bool
    summary: str
    details: str
    response_time_ms: int | None = None
    status_code: int | None = None


def now_local() -> datetime:
    return datetime.now().astimezone()


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime(DISPLAY_TIME_FORMAT)


def format_duration(total_seconds: float) -> str:
    seconds = max(0, int(round(total_seconds)))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    return path


def truncate_text(value: str, limit: int = 280) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


class ServerMonitor:
    def __init__(self, config: dict[str, Any], config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.base_dir = config_path.parent

        self.target = config["target"]
        self.monitoring = config.get("monitoring", {})
        self.alerts = config.get("alerts", {})
        self.files = config.get("files", {})

        self.target_name = self.target.get("name", "Servidor")
        self.target_type = self.target.get("type", "http").lower()
        self.check_interval = int(self.monitoring.get("check_interval_seconds", 60))
        self.failure_threshold = max(1, int(self.monitoring.get("failure_threshold", 2)))
        self.recovery_threshold = max(1, int(self.monitoring.get("recovery_threshold", 1)))
        self.heartbeat_every_checks = max(
            1,
            int(self.monitoring.get("heartbeat_every_checks", 30)),
        )

        self.state_file = resolve_path(self.base_dir, self.files.get("state_file", "data/state.json"))
        self.incident_file = resolve_path(
            self.base_dir,
            self.files.get("incident_csv", "data/incidentes.csv"),
        )
        self.runtime_log_file = resolve_path(
            self.base_dir,
            self.files.get("runtime_log", "data/monitor.log"),
        )

        self.session = requests.Session()
        if self.target_type == "http" and not bool(self.target.get("verify_ssl", True)):
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.check_counter = 0
        self.state = self.load_state()
        self.setup_logging()

    def setup_logging(self) -> None:
        ensure_parent(self.runtime_log_file)

        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        file_handler = logging.FileHandler(self.runtime_log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    def default_state(self) -> dict[str, Any]:
        return {
            "outage_active": False,
            "down_since": None,
            "down_summary": None,
            "down_details": None,
            "first_failure_at": None,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "consecutive_successes": 0,
        }

    def load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return self.default_state()

        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self.default_state()

    def save_state(self) -> None:
        ensure_parent(self.state_file)
        self.state_file.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def run(self) -> None:
        logging.info(
            "Monitoreando %s (%s) cada %ss. Umbral de falla=%s, recuperacion=%s.",
            self.target_name,
            self.target_type,
            self.check_interval,
            self.failure_threshold,
            self.recovery_threshold,
        )

        try:
            while True:
                checked_at = now_local()
                result = self.check_target()
                self.process_result(checked_at, result)
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            logging.info("Monitoreo detenido manualmente.")

    def run_once(self) -> int:
        checked_at = now_local()
        result = self.check_target()
        status = "OK" if result.is_up else "DOWN"
        print(f"[{status}] {self.target_name} | {result.summary}")
        print(result.details)
        print(f"Fecha y hora: {format_timestamp(checked_at)}")
        return 0 if result.is_up else 1

    def check_target(self) -> CheckResult:
        if self.target_type == "http":
            return self.check_http()
        if self.target_type == "tcp":
            return self.check_tcp()
        if self.target_type == "ping":
            return self.check_ping()
        raise ValueError(
            "target.type debe ser 'http', 'tcp' o 'ping'."
        )

    def check_http(self) -> CheckResult:
        url = self.target["url"]
        method = self.target.get("method", "GET").upper()
        timeout_seconds = float(self.target.get("timeout_seconds", 10))
        allow_redirects = bool(self.target.get("allow_redirects", True))
        verify_ssl = bool(self.target.get("verify_ssl", True))
        headers = self.target.get("headers", {})
        expected_status_codes = self.target.get("expected_status_codes")

        started = time.perf_counter()
        try:
            response = self.session.request(
                method=method,
                url=url,
                timeout=timeout_seconds,
                allow_redirects=allow_redirects,
                verify=verify_ssl,
                headers=headers,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            if expected_status_codes:
                is_up = response.status_code in expected_status_codes
                expected_label = ",".join(str(code) for code in expected_status_codes)
            else:
                is_up = 200 <= response.status_code < 400
                expected_label = "2xx/3xx"

            summary = f"HTTP {response.status_code}"
            details = (
                f"URL={url} | metodo={method} | tiempo_ms={elapsed_ms} "
                f"| esperado={expected_label}"
            )
            return CheckResult(
                is_up=is_up,
                summary=summary,
                details=details,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
            )
        except requests.RequestException as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return CheckResult(
                is_up=False,
                summary=type(exc).__name__,
                details=f"URL={url} | error={truncate_text(str(exc))} | tiempo_ms={elapsed_ms}",
                response_time_ms=elapsed_ms,
            )

    def check_tcp(self) -> CheckResult:
        host = self.target["host"]
        port = int(self.target["port"])
        timeout_seconds = float(self.target.get("timeout_seconds", 10))

        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return CheckResult(
                    is_up=True,
                    summary=f"TCP conectado a {host}:{port}",
                    details=f"host={host} | puerto={port} | tiempo_ms={elapsed_ms}",
                    response_time_ms=elapsed_ms,
                )
        except OSError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return CheckResult(
                is_up=False,
                summary=type(exc).__name__,
                details=(
                    f"host={host} | puerto={port} | error={truncate_text(str(exc))} "
                    f"| tiempo_ms={elapsed_ms}"
                ),
                response_time_ms=elapsed_ms,
            )

    def check_ping(self) -> CheckResult:
        host = self.target.get("host") or self.host_from_target()
        timeout_seconds = max(1, int(float(self.target.get("timeout_seconds", 5))))

        if sys.platform.startswith("win"):
            command = ["ping", "-n", "1", "-w", str(timeout_seconds * 1000), host]
        else:
            command = ["ping", "-c", "1", "-W", str(timeout_seconds), host]

        started = time.perf_counter()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        output = truncate_text((completed.stdout or completed.stderr or "").strip())
        if completed.returncode == 0:
            return CheckResult(
                is_up=True,
                summary=f"Ping OK a {host}",
                details=f"host={host} | tiempo_ms={elapsed_ms} | salida={output}",
                response_time_ms=elapsed_ms,
            )

        return CheckResult(
            is_up=False,
            summary=f"Ping fallido a {host}",
            details=f"host={host} | tiempo_ms={elapsed_ms} | salida={output}",
            response_time_ms=elapsed_ms,
        )

    def host_from_target(self) -> str:
        if "url" in self.target:
            parsed = urlparse(self.target["url"])
            if parsed.hostname:
                return parsed.hostname
        raise ValueError("No se pudo resolver el host para el ping.")

    def process_result(self, checked_at: datetime, result: CheckResult) -> None:
        self.check_counter += 1

        if result.is_up:
            self.handle_success(checked_at, result)
        else:
            self.handle_failure(checked_at, result)

        self.save_state()

    def handle_success(self, checked_at: datetime, result: CheckResult) -> None:
        self.state["consecutive_successes"] = int(self.state.get("consecutive_successes", 0)) + 1
        self.state["consecutive_failures"] = 0
        self.state["last_failure_at"] = None

        if self.state.get("outage_active"):
            if self.state["consecutive_successes"] < self.recovery_threshold:
                logging.info(
                    "Recuperacion parcial detectada (%s/%s). Esperando confirmacion.",
                    self.state["consecutive_successes"],
                    self.recovery_threshold,
                )
                return

            down_since = datetime.fromisoformat(self.state["down_since"])
            duration_seconds = (checked_at - down_since).total_seconds()
            incident = {
                "target_name": self.target_name,
                "target_type": self.target_type,
                "monitor_host": socket.gethostname(),
                "started_at": self.state["down_since"],
                "ended_at": checked_at.isoformat(timespec="seconds"),
                "duration_seconds": round(duration_seconds, 2),
                "duration_human": format_duration(duration_seconds),
                "failure_summary": self.state.get("down_summary"),
                "failure_details": self.state.get("down_details"),
                "recovery_summary": result.summary,
                "recovery_details": result.details,
            }
            self.append_incident(incident)

            subject = f"RECUPERADO: {self.target_name}"
            body = "\n".join(
                [
                    f"Servidor: {self.target_name}",
                    f"Tipo de chequeo: {self.target_type}",
                    f"Caida detectada: {format_timestamp(down_since)}",
                    f"Recuperado: {format_timestamp(checked_at)}",
                    f"Duracion: {incident['duration_human']}",
                    f"Motivo inicial: {incident['failure_summary']}",
                    f"Detalle inicial: {incident['failure_details']}",
                    f"Estado de recuperacion: {incident['recovery_summary']}",
                    f"Detalle de recuperacion: {incident['recovery_details']}",
                    f"Monitor: {incident['monitor_host']}",
                ]
            )
            self.dispatch_alerts("recovery", subject, body, incident)

            logging.warning(
                "Servidor recuperado. Duracion total: %s.",
                incident["duration_human"],
            )

            self.state = self.default_state()
            return

        if self.check_counter % self.heartbeat_every_checks == 0:
            logging.info(
                "Heartbeat OK | %s | %s",
                self.target_name,
                result.details,
            )

    def handle_failure(self, checked_at: datetime, result: CheckResult) -> None:
        self.state["consecutive_failures"] = int(self.state.get("consecutive_failures", 0)) + 1
        self.state["consecutive_successes"] = 0
        self.state["last_failure_at"] = checked_at.isoformat(timespec="seconds")

        if not self.state.get("first_failure_at"):
            self.state["first_failure_at"] = checked_at.isoformat(timespec="seconds")

        if self.state.get("outage_active"):
            logging.error("Servidor sigue caido | %s", result.details)
            return

        if self.state["consecutive_failures"] < self.failure_threshold:
            logging.warning(
                "Fallo detectado (%s/%s) | %s",
                self.state["consecutive_failures"],
                self.failure_threshold,
                result.details,
            )
            return

        down_since = datetime.fromisoformat(self.state["first_failure_at"])
        self.state["outage_active"] = True
        self.state["down_since"] = down_since.isoformat(timespec="seconds")
        self.state["down_summary"] = result.summary
        self.state["down_details"] = result.details

        event = {
            "target_name": self.target_name,
            "target_type": self.target_type,
            "monitor_host": socket.gethostname(),
            "started_at": self.state["down_since"],
            "failure_summary": result.summary,
            "failure_details": result.details,
            "failure_threshold": self.failure_threshold,
        }

        subject = f"ALERTA: servidor caido - {self.target_name}"
        body = "\n".join(
            [
                f"Servidor: {self.target_name}",
                f"Tipo de chequeo: {self.target_type}",
                f"Caida detectada: {format_timestamp(down_since)}",
                f"Motivo: {result.summary}",
                f"Detalle: {result.details}",
                f"Umbral aplicado: {self.failure_threshold} chequeo(s) fallidos consecutivos",
                f"Monitor: {event['monitor_host']}",
            ]
        )
        self.dispatch_alerts("down", subject, body, event)
        logging.error("Caida confirmada y alertada | %s", result.details)

    def append_incident(self, incident: dict[str, Any]) -> None:
        ensure_parent(self.incident_file)

        fieldnames = [
            "target_name",
            "target_type",
            "monitor_host",
            "started_at",
            "ended_at",
            "duration_seconds",
            "duration_human",
            "failure_summary",
            "failure_details",
            "recovery_summary",
            "recovery_details",
        ]

        file_exists = self.incident_file.exists()
        with self.incident_file.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(incident)

    def dispatch_alerts(
        self,
        event_type: str,
        subject: str,
        body: str,
        payload: dict[str, Any],
    ) -> None:
        email_cfg = self.alerts.get("email", {})
        webhook_cfg = self.alerts.get("webhook", {})
        desktop_cfg = self.alerts.get("desktop", {})

        if email_cfg.get("enabled"):
            try:
                self.send_email(subject, body, email_cfg)
                logging.info("Alerta enviada por email.")
            except Exception as exc:  # noqa: BLE001
                logging.error("No se pudo enviar el email: %s", exc)

        if webhook_cfg.get("enabled"):
            try:
                self.send_webhook(subject, body, payload, webhook_cfg)
                logging.info("Alerta enviada por webhook.")
            except Exception as exc:  # noqa: BLE001
                logging.error("No se pudo enviar el webhook: %s", exc)

        if desktop_cfg.get("enabled"):
            try:
                self.send_desktop_alert(event_type, subject, body, desktop_cfg)
                logging.info("Alerta local enviada.")
            except Exception as exc:  # noqa: BLE001
                logging.error("No se pudo enviar la alerta local: %s", exc)

    def send_email(self, subject: str, body: str, email_cfg: dict[str, Any]) -> None:
        smtp_server = email_cfg["smtp_server"]
        smtp_port = int(email_cfg.get("smtp_port", 587))
        username = email_cfg.get("username")
        password = email_cfg.get("password")
        sender = email_cfg["sender_email"]
        recipients = email_cfg["recipient_emails"]
        use_tls = bool(email_cfg.get("use_tls", True))
        use_ssl = bool(email_cfg.get("use_ssl", False))

        if isinstance(recipients, str):
            recipients = [recipients]

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(body)

        if use_ssl:
            server: smtplib.SMTP | smtplib.SMTP_SSL
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)

        with server:
            if use_tls and not use_ssl:
                server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(message)

    def send_webhook(
        self,
        subject: str,
        body: str,
        payload: dict[str, Any],
        webhook_cfg: dict[str, Any],
    ) -> None:
        url = webhook_cfg["url"]
        provider = webhook_cfg.get("provider", "generic").lower()
        timeout_seconds = float(webhook_cfg.get("timeout_seconds", 15))
        headers = webhook_cfg.get("headers", {})

        text_message = f"{subject}\n\n{body}"

        if provider in {"slack", "teams"}:
            json_payload: dict[str, Any] = {"text": text_message}
        elif provider == "discord":
            json_payload = {"content": text_message}
        else:
            json_payload = {
                "subject": subject,
                "message": body,
                "event": payload,
            }

        response = requests.post(
            url,
            json=json_payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()

    def send_desktop_alert(
        self,
        event_type: str,
        subject: str,
        body: str,
        desktop_cfg: dict[str, Any],
    ) -> None:
        if not sys.platform.startswith("win"):
            logging.warning("La alerta local solo esta soportada en Windows.")
            return

        sound_enabled = bool(desktop_cfg.get(f"sound_on_{event_type}", False))
        popup_enabled = bool(desktop_cfg.get(f"popup_on_{event_type}", False))

        if sound_enabled:
            threading.Thread(
                target=self.play_desktop_sound,
                args=(event_type, desktop_cfg),
                daemon=True,
            ).start()

        if popup_enabled:
            threading.Thread(
                target=self.show_desktop_popup,
                args=(subject, body, event_type),
                daemon=True,
            ).start()

    def play_desktop_sound(self, event_type: str, desktop_cfg: dict[str, Any]) -> None:
        try:
            import winsound
        except ImportError:
            logging.warning("No se pudo cargar winsound para la alerta sonora.")
            return

        repeat = max(1, int(desktop_cfg.get(f"sound_repeat_{event_type}", 3)))
        pause_ms = max(50, int(desktop_cfg.get("sound_pause_ms", 120)))
        patterns = {
            "down": [(1318, 180), (988, 180), (1318, 180), (784, 220), (523, 700)],
            "recovery": [(784, 140), (988, 140), (1318, 240)],
        }
        pattern = patterns.get(event_type, [(880, 200), (659, 250)])

        try:
            for _ in range(repeat):
                for frequency, duration in pattern:
                    winsound.Beep(frequency, duration)
                time.sleep(pause_ms / 1000)
        except RuntimeError:
            for _ in range(repeat):
                winsound.MessageBeep(winsound.MB_ICONHAND)
                time.sleep(pause_ms / 1000)

    def show_desktop_popup(self, title: str, body: str, event_type: str) -> None:
        try:
            import ctypes
        except ImportError:
            logging.warning("No se pudo cargar ctypes para la ventana emergente.")
            return

        style_map = {
            "down": 0x00000010,
            "recovery": 0x00000040,
        }
        flags = style_map.get(event_type, 0) | 0x00010000 | 0x00040000
        ctypes.windll.user32.MessageBoxW(0, body, title, flags)


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"No existe el archivo de configuracion: {config_path}"
        )

    data = json.loads(config_path.read_text(encoding="utf-8"))

    if "target" not in data:
        raise ValueError("La configuracion debe incluir la seccion 'target'.")

    target_type = data["target"].get("type", "http").lower()
    if target_type == "http" and "url" not in data["target"]:
        raise ValueError("Para target.type='http' debes definir target.url.")
    if target_type == "tcp" and not {"host", "port"} <= set(data["target"]):
        raise ValueError("Para target.type='tcp' debes definir target.host y target.port.")
    if target_type == "ping" and not ({"host"} <= set(data["target"]) or {"url"} <= set(data["target"])):
        raise ValueError("Para target.type='ping' debes definir target.host o target.url.")

    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor de servidores con alertas y registro de incidentes.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Ruta al archivo JSON de configuracion. Por defecto: config.json",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola verificacion y termina.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()

    try:
        config = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error de configuracion: {exc}")
        print(
            "Tip: copia config.example.json como config.json y luego ajusta tus datos."
        )
        return 2

    monitor = ServerMonitor(config, config_path)

    if args.once:
        return monitor.run_once()

    monitor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
