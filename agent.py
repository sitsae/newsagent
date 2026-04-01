#!/usr/bin/env python3
"""
Nyhetsagent: henter RSS-feeds fra norske medier, sender innholdet til
Claude via Anthropic SDK for relevansvurdering, og e-poster resultatet.
"""

import os
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / ".env")

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

# RSS-feeds fra norske medier
RSS_FEEDS = {
    "NRK":            "https://www.nrk.no/toppsaker.rss",
    "VG":             "https://www.vg.no/rss/feed/",
    "Aftenposten":    "https://www.aftenposten.no/rss/",
    "Dagbladet":      "https://www.dagbladet.no/rss/nyheter",
    "DN":             "https://services.dn.no/api/feed/rss/",
    "E24":            "https://e24.no/rss/",
    "TV2":            "https://www.tv2.no/rss/nyheter",
    "Dagsavisen":     "https://www.dagsavisen.no/rss",
    "Dagens Medisin": "https://www.dagensmedisin.no/?lab_viewport=rss",
    "Sykepleien":     "https://sykepleien.no/rss.xml",
    "Fontene":        "https://fontene.no/lomediarss/fontene/feed",
    "Altinget":       "https://www.altinget.no/helse/rss",
}

SYSTEM_PROMPT = """Du er en nyhetsagent som vurderer om nyhetssaker er relevante for private helse- og velferdsbedrifter i Norge.

Relevante bransjer: barnehager, barnevern, rusbehandling, psykisk helsevern, sykehjem, hjemmetjenester, rehabilitering, legemidler, medisinsk utstyr, treningssenter, sykehus, arbeidsmarkedstiltak (NAV-leverandører), bedriftshelsetjeneste, tannhelse, digitale helsetjenester.

Relevante temaer:
- Politiske vedtak og lovforslag fra Stortinget eller kommuner
- Endringer i finansiering, anbud, tilskudd eller refusjonsordninger
- Tilsyn fra Helsetilsynet, Statsforvalter eller Arbeidstilsynet med presedensskapende utfall
- Bransjehendelser: konkurser, oppkjøp, nye aktører
- Meninger, kronikker og politikeruttalelser som signaliserer retningsendringer

## Utvelgelse — vær streng

Ta kun med saker der relevansen for bransjen er konkret og direkte. Utelat:
- Saker der tilknytningen til bransjen er spekulativ eller generell (f.eks. "dette kan påvirke helse generelt")
- Rene personalsaker, kriminalitet eller ulykker uten bransjemessig betydning
- Saker som handler om offentlige sykehus/kommunale tjenester uten å berøre private aktører

## Deduplisering

Flere kilder dekker ofte samme sak. Velg én representativ sak per hendelse — den med best tittel eller mest kjent kilde. Nevn kort om flere kilder har dekket samme sak, f.eks. "(også omtalt i Dagbladet)".

## Format per sak

**[Tittel]** — [Kilde], [dato]
[URL]
_Hvorfor relevant:_ [1–2 setninger om konkret bransjepåvirkning]

## Avslutning

Avslutt alltid med:
"Følgende kilder ble også gjennomsøkt uten relevante funn: [liste]."
Hvis alle kilder hadde relevante funn, utelat denne linjen.
"""


MAX_ITEMS_PER_FEED = 20


def fetch_feed(name: str, url: str) -> list[str]:
    """Henter RSS-feed og returnerer liste med saks-strenger (maks MAX_ITEMS_PER_FEED)."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "NyhetsagentBot/1.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ADVARSEL] {name}: {e}", flush=True)
        return []

    items = []

    try:
        root = ET.fromstring(resp.content)
        # Støtter både RSS og Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for entry in entries:
            title = (
                entry.findtext("title")
                or entry.findtext("atom:title", namespaces=ns)
                or ""
            ).strip()
            link = (
                entry.findtext("link")
                or (entry.find("atom:link", ns) or {}).get("href", "")
                or ""
            ).strip()
            pub = (
                entry.findtext("pubDate")
                or entry.findtext("atom:updated", namespaces=ns)
                or ""
            ).strip()

            items.append(f"[{name}] {title} | {link} | {pub}")
            if len(items) >= MAX_ITEMS_PER_FEED:
                break

    except ET.ParseError as e:
        print(f"  [ADVARSEL] {name}: XML-feil: {e}", flush=True)

    return items


def get_news() -> str:
    """Henter RSS-feeds og sender til Claude for relevansvurdering."""
    print("  Henter RSS-feeds...", flush=True)
    all_items = []
    for name, url in RSS_FEEDS.items():
        items = fetch_feed(name, url)
        all_items.extend(items)
        print(f"  {name}: {len(items)} saker", flush=True)

    if not all_items:
        return "Ingen saker hentet fra RSS-feeds."

    sources = ", ".join(RSS_FEEDS.keys())
    feed_text = "\n".join(all_items)
    user_message = (
        f"Følgende kilder er gjennomsøkt: {sources}.\n\n"
        f"Her er nyhetssaker fra disse kildene de siste timene:\n\n{feed_text}\n\n"
        "Vurder hvilke av disse som er relevante for private helse- og velferdsbedrifter. "
        "Husk å avslutte med å nevne kildene uten relevante funn."
    )

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


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

    log("Søker etter nyheter via RSS + Claude API...")
    news = get_news()
    log(f"Svar mottatt ({len(news)} tegn). Sender e-post til {recipients}...")
    send_email(recipients, news, now)
    log("E-post sendt.")


if __name__ == "__main__":
    main()
