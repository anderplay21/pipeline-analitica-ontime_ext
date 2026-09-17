"""Envío de alertas por correo (SMTP) o Teams (webhook) cuando el pipeline falla."""

import os
import smtplib
from email.mime.text import MIMEText

import requests


def enviar_alerta_email(asunto, mensaje, config):
    cfg = config.get("notificaciones", {})
    if not cfg.get("email_habilitado", False):
        return

    usuario = os.getenv("SMTP_USUARIO")
    password = os.getenv("SMTP_PASSWORD")
    destinatarios = cfg.get("destinatarios", [])
    if not destinatarios:
        return

    email = MIMEText(mensaje, "plain", "utf-8")
    email["Subject"] = asunto
    email["From"] = usuario
    email["To"] = ", ".join(destinatarios)

    with smtplib.SMTP(cfg["smtp_servidor"], cfg["smtp_puerto"]) as servidor:
        servidor.starttls()
        servidor.login(usuario, password)
        servidor.sendmail(usuario, destinatarios, email.as_string())


def enviar_alerta_teams(mensaje, config):
    cfg = config.get("notificaciones", {})
    if not cfg.get("teams_webhook_habilitado", False):
        return

    webhook_url = os.getenv("TEAMS_WEBHOOK_URL") or cfg.get("teams_webhook_url")
    if not webhook_url:
        return

    requests.post(webhook_url, json={"text": mensaje}, timeout=10)


def notificar_error(asunto, mensaje, config):
    enviar_alerta_email(asunto, mensaje, config)
    enviar_alerta_teams(f"{asunto}\n{mensaje}", config)
