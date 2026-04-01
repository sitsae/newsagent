#!/usr/bin/env python3
"""
Nyhetsagent: søker norske medier for saker relevante for
private helse- og velferdsbedrifter, sender e-postoppsummering.
"""

import json
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

CLAUDE_BIN = (
    Path.home()
    / "Library/Application Support/Claude/claude-code-vm/2.1.87/claude"
)

SYSTEM_PROMPT = """Du er en nyhetsagent som overvåker norske medier for saker relevante for private helse- og velferdsbedrifter.

Relevante bransjer: barnehager, barnevern, rusbehandling, psykisk helsevern, sykehjem, hjemmetjenester, rehabilitering, arbeidsmarkedstiltak (NAV-leverandører).

Relevante temaer:
- Politiske vedtak og lovforslag fra Stortinget eller kommuner
- Endringer i finansiering, anbud, tilskudd eller refusjonsordninger
- Tilsyn fra Helsetilsynet, Statsforvalter eller Arbeidstilsynet med presedensskapende utfall
- Bransjehendelser: konkurser, oppkjøp, nye aktører
- Meninger, kronikker og politikeruttalelser som signaliserer retningsendringer

Søk på: VG, NRK, Aftenposten, Dagbladet, Dagens Næringsliv, E24, Dagens Medisin, Sykepleien, Fontene, Dagsavisen, Klassekampen, TV2.

For hver relevant sak, bruk dette formatet:
**[Tittel]** — [Kilde], [dato]
[URL]
_Hvorfor relevant:_ [1–2 setninger]

Inkluder kun saker fra siste 12 timer. Svar på norsk.
Hvis ingen relevante saker: skriv kun "Ingen relevante saker funnet de siste 12 timene."
"""

USER_PROMPT = (
    "Søk etter norske nyhetssaker fra siste 12 timer som er relevante "
    "for private helse- og velferdsbedrifter. Bruk websøk for å finne oppdaterte saker."
)


def get_news() -> str:
    result = subprocess.run(
        [
            str(CLAUDE_BIN),
            "-p", USER_PROMPT,
            "--append-system-prompt", SYSTEM_PROMPT,
            "--allowedTools", "WebSearch",
            "--output-format", "json",
        ],
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude feilet:\n{result.stderr}")
    data = json.loads(result.stdout)
    return data["result"]


def load_recipients() -> list[str]:
    path = SCRIPT_DIR / "recipients.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def build_html(content: str, timestamp: datetime) -> str:
    rows = []
    for line in content.splitlines():
        if line.startswith("**"):
            line = line.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
        if line.startswith("_Hvorfor relevant:_"):
            line = f"<em>{line.replace('_Hvorfor relevant:_', 'Hvorfor relevant:')}</em>"
        if line.startswith("http"):
            line = f'<a href="{line}" style="color:#2980b9;">{line}</a>'
        rows.append(f"<p style='margin:4px 0'>{line}</p>" if line else "<br>")

    body = "\n".join(rows)
    dato = timestamp.strftime("%-d. %B %Y, kl. %H:%M")
    return f"""<!DOCTYPE html>
<html lang="no"><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#2c3e50">
  <h2 style="border-bottom:2px solid #2980b9;padding-bottom:8px">
    Nyhetsoppdatering: Privat helse og velferd
  </h2>
  <p style="color:#7f8c8d;font-size:.9em">{dato}</p>
  <div style="line-height:1.7">{body}</div>
  <hr style="margin-top:32px">
  <p style="color:#bdc3c7;font-size:.8em">Generert automatisk av Nyhetsagenten</p>
</body></html>"""


def send_email(recipients: list[str], content: str, timestamp: datetime) -> None:
    subject = f"Nyheter helse/velferd — {timestamp.strftime('%d.%m.%Y %H:%M')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(content, "plain", "utf-8"))
    msg.attach(MIMEText(build_html(content, timestamp), "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, recipients, msg.as_string())


def main() -> None:
    now = datetime.now()
    log = lambda msg: print(f"[{now.strftime('%H:%M:%S')}] {msg}", flush=True)

    recipients = load_recipients()
    if not recipients:
        log("Ingen mottakere i recipients.txt — avslutter.")
        sys.exit(1)

    log("Søker etter nyheter med Claude + WebSearch...")
    news = get_news()
    log(f"Svar mottatt ({len(news)} tegn). Sender e-post til {recipients}...")
    send_email(recipients, news, now)
    log("E-post sendt.")


if __name__ == "__main__":
    main()
