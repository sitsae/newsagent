#!/usr/bin/env python3
"""
Nyhetsagent: henter RSS-feeds fra norske medier, sender innholdet til
Claude via Anthropic SDK for relevansvurdering, og e-poster resultatet.
"""

import json
import os
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
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
SEEN_FILE = SCRIPT_DIR / "seen.json"
SEEN_MAX_AGE_HOURS = 48


def load_seen() -> set[str]:
    """Laster inn URL-er som allerede er sendt. Kaster ut oppføringer eldre enn 48 timer."""
    if not SEEN_FILE.exists():
        return set()
    data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SEEN_MAX_AGE_HOURS)).isoformat()
    return {url for url, ts in data.items() if ts >= cutoff}


def save_seen(seen: set[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = {}
    if SEEN_FILE.exists():
        existing = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    existing.update({url: now for url in seen})
    SEEN_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def parse_pub_date(raw: str) -> datetime | None:
    """Parser RSS (RFC 2822) og Atom (ISO 8601) datoformater."""
    from email.utils import parsedate_to_datetime
    for fn in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = fn(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def fetch_feed(name: str, url: str) -> list[str]:
    """Henter RSS-feed og returnerer saker ikke eldre enn 48 timer (maks MAX_ITEMS_PER_FEED)."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "NyhetsagentBot/1.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ADVARSEL] {name}: {e}", flush=True)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    items = []

    try:
        root = ET.fromstring(resp.content)
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

            dt = parse_pub_date(pub)
            if dt and dt < cutoff:
                break  # RSS-feeds er kronologiske — resten er eldre

            items.append(f"[{name}] {title} | {link} | {pub}")
            if len(items) >= MAX_ITEMS_PER_FEED:
                break

    except ET.ParseError as e:
        print(f"  [ADVARSEL] {name}: XML-feil: {e}", flush=True)

    return items


def get_news(seen: set[str]) -> tuple[str, set[str]]:
    """Henter RSS-feeds, filtrerer bort sett saker, sender til Claude.
    Returnerer (svar, nye_urls)."""
    print("  Henter RSS-feeds...", flush=True)
    all_items = []
    new_urls: set[str] = set()

    for name, url in RSS_FEEDS.items():
        items = fetch_feed(name, url)
        new_items = []
        for item in items:
            # URL er andre felt i "| "-separert streng
            parts = item.split(" | ")
            item_url = parts[1] if len(parts) > 1 else ""
            if item_url and item_url in seen:
                continue
            new_items.append(item)
            if item_url:
                new_urls.add(item_url)
        all_items.extend(new_items)
        print(f"  {name}: {len(new_items)} nye saker ({len(items) - len(new_items)} filtrert)", flush=True)

    if not all_items:
        return "Ingen nye saker siden forrige utsending.", new_urls

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
    return message.content[0].text, new_urls


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

    seen = load_seen()
    log(f"Søker etter nyheter via RSS + Claude API... ({len(seen)} URL-er filtrert fra tidligere)")
    news, new_urls = get_news(seen)
    log(f"Svar mottatt ({len(news)} tegn). Sender e-post til {recipients}...")
    send_email(recipients, news, now)
    save_seen(new_urls)
    log(f"E-post sendt. {len(new_urls)} nye URL-er lagret i seen.json.")


if __name__ == "__main__":
    main()
