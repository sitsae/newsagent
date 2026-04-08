#!/usr/bin/env python3
"""
Nyhetsagent:
  1. Henter RSS-feeds fra norske medier
  2. Claude vurderer relevans og returnerer strukturert JSON
  3. Python formaterer og sender e-post fra JSON-dataene
"""

import json
import os
import re
import smtplib
import subprocess
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

MAX_ITEMS_PER_FEED = 20

SYSTEM_PROMPT = """Du er en nyhetsagent som vurderer om nyhetssaker er relevante for private helse- og velferdsbedrifter i Norge.

Relevante bransjer: barnehager, barnevern, rusbehandling, psykisk helsevern, sykehjem, hjemmetjenester, rehabilitering, arbeidsmarkedstiltak (NAV-leverandører), bedriftshelsetjeneste, tannhelse, digitale helsetjenester.

Relevante temaer:
- Politiske vedtak og lovforslag fra Stortinget eller kommuner
- Endringer i finansiering, anbud, tilskudd eller refusjonsordninger
- Tilsyn fra Helsetilsynet, Statsforvalter eller Arbeidstilsynet med presedensskapende utfall
- Bransjehendelser: konkurser, oppkjøp, nye aktører
- Meninger og politikeruttalelser som signaliserer retningsendringer

Vær streng: ta kun med saker der relevansen er konkret og direkte.
Utelat generelle helsesaker, kriminalitet og ulykker uten bransjemessig betydning.
Dedupliser: én sak per hendelse, selv om flere kilder har dekket den — bruk feltet "også_omtalt_i" for øvrige kilder."""

# JSON-schema Claude må fylle ut
TOOL = {
    "name": "rapporter_relevante_saker",
    "description": "Rapporter nyhetssaker som er relevante for private helse- og velferdsbedrifter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "saker": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tittel":          {"type": "string"},
                        "kilde":           {"type": "string"},
                        "url":             {"type": "string"},
                        "publisert":       {"type": "string"},
                        "hvorfor_relevant": {"type": "string", "description": "1–2 setninger"},
                        "også_omtalt_i":  {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["tittel", "kilde", "url", "publisert", "hvorfor_relevant"],
                },
            },
            "kilder_uten_funn": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Navn på kilder som ble gjennomsøkt uten relevante funn",
            },
        },
        "required": ["saker", "kilder_uten_funn"],
    },
}


# --- RSS-henting ---

def parse_pub_date(raw: str) -> datetime | None:
    from email.utils import parsedate_to_datetime
    for fn in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = fn(raw)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def fetch_feed(name: str, url: str) -> list[dict]:
    """Returnerer liste med sak-dicts: title, url, pub_date, source, ingress."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "NyhetsagentBot/1.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ADVARSEL] {name}: {e}", flush=True)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items = []

    try:
        # Erstatt ikke-standard XML-entiteter (f.eks. &nbsp;, &rsquo;) med mellomrom
        xml_text = re.sub(
            r"&(?!amp;|lt;|gt;|apos;|quot;|#)([^;]+);",
            " ",
            resp.content.decode("utf-8", errors="replace"),
        )
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        media_ns = {**ns, "media": "http://search.yahoo.com/mrss/"}

        for entry in entries:
            title = (entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or "").strip()
            link  = (entry.findtext("link")  or (entry.find("atom:link", ns) or {}).get("href", "") or "").strip()
            pub   = (entry.findtext("pubDate") or entry.findtext("atom:updated", namespaces=ns) or "").strip()
            desc  = (entry.findtext("description") or entry.findtext("atom:summary", namespaces=ns) or "").strip()

            # Bilde: prøv media:content → media:thumbnail → enclosure → første <img> i desc
            image_url = ""
            for tag in ("media:content", "media:thumbnail"):
                el = entry.find(tag, media_ns)
                if el is not None:
                    image_url = el.get("url", "")
                    if image_url:
                        break
            if not image_url:
                enc = entry.find("enclosure")
                if enc is not None and "image" in enc.get("type", ""):
                    image_url = enc.get("url", "")
            if not image_url:
                m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
                if m:
                    image_url = m.group(1)

            # Fjern HTML-tagger fra ingress (fallback til regex ved parse-feil)
            if desc:
                try:
                    desc = "".join(ET.fromstring(f"<x>{desc}</x>").itertext())
                except ET.ParseError:
                    desc = re.sub(r"<[^>]+>", " ", desc)

            dt = parse_pub_date(pub)
            if dt and dt < cutoff:
                break

            items.append({"source": name, "title": title, "url": link, "pub_date": pub,
                          "ingress": desc[:200], "image_url": image_url})
            if len(items) >= MAX_ITEMS_PER_FEED:
                break

    except ET.ParseError as e:
        print(f"  [ADVARSEL] {name}: XML-feil: {e}", flush=True)

    return items


# --- Claude-vurdering ---

def _build_user_message(articles: list[dict]) -> str:
    feed_lines = [
        f"[{a['source']}] {a['title']} | {a['url']} | {a['pub_date']}"
        + (f"\nIngress: {a['ingress']}" if a["ingress"] else "")
        for a in articles
    ]
    return f"Gjennomsøkte kilder: {', '.join(RSS_FEEDS.keys())}.\n\n" + "\n\n".join(feed_lines)


def find_relevant(articles: list[dict]) -> dict:
    """Sender artikkelliste til Claude, returnerer strukturert JSON med relevante saker.
    Bruker Anthropic SDK hvis ANTHROPIC_API_KEY er satt, ellers claude-binæren."""
    user_message = _build_user_message(articles)

    if os.environ.get("ANTHROPIC_API_KEY"):
        # GitHub Actions / lokal med API-nøkkel i .env
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "rapporter_relevante_saker"},
            messages=[{"role": "user", "content": user_message}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return block.input
        return {"saker": [], "kilder_uten_funn": list(RSS_FEEDS.keys())}

    # Fallback: bruk installert claude-binær (lokal maskin uten API-nøkkel)
    CLAUDE_BIN = (
        Path.home()
        / "Library/Application Support/Claude/claude-code/2.1.87/claude.app/Contents/MacOS/claude"
    )
    result = subprocess.run(
        [
            str(CLAUDE_BIN), "-p", user_message,
            "--append-system-prompt", SYSTEM_PROMPT,
            "--output-format", "json",
            "--json-schema", json.dumps(TOOL["input_schema"]),
        ],
        capture_output=True, text=True, cwd=str(SCRIPT_DIR),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude feilet:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    data = json.loads(result.stdout)
    return data.get("structured_output", {"saker": [], "kilder_uten_funn": list(RSS_FEEDS.keys())})


# --- E-postformatering ---

def build_html(result: dict, timestamp: datetime, images: dict[str, str] | None = None) -> str:
    saker = result.get("saker", [])
    kilder_uten_funn = result.get("kilder_uten_funn", [])
    dato = timestamp.strftime("%-d. %B %Y, kl. %H:%M")
    images = images or {}

    if not saker:
        artikler_html = "<p><em>Ingen relevante saker funnet de siste 24 timene.</em></p>"
    else:
        artikler = []
        for s in saker:
            også = ""
            if s.get("også_omtalt_i"):
                også = f" <span style='color:#7f8c8d;font-size:.9em'>(også omtalt i {', '.join(s['også_omtalt_i'])})</span>"
            img_html = ""
            img_url = images.get(s.get("url", ""))
            if img_url:
                img_html = f'<a href="{s["url"]}"><img src="{img_url}" alt="" style="width:100%;max-width:600px;height:180px;object-fit:cover;border-radius:4px;display:block;margin-bottom:8px"></a>'
            artikler.append(f"""
  <div style="margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #ecf0f1">
    {img_html}<p style="margin:0 0 4px">
      <a href="{s['url']}" style="color:#2c3e50;font-weight:bold;text-decoration:none">{s['tittel']}</a>
    </p>
    <p style="margin:0 0 6px;color:#7f8c8d;font-size:.85em">{s['kilde']} · {s['publisert']}{også}</p>
    <p style="margin:0;font-size:.95em;color:#555">{s['hvorfor_relevant']}</p>
  </div>""")
        artikler_html = "\n".join(artikler)

    footer_html = ""
    if kilder_uten_funn:
        footer_html = f"<p style='color:#95a5a6;font-size:.85em'>Også gjennomsøkt uten relevante funn: {', '.join(kilder_uten_funn)}.</p>"

    return f"""<!DOCTYPE html>
<html lang="no"><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#2c3e50">
  <h2 style="border-bottom:2px solid #2980b9;padding-bottom:8px">Nyhetsoppdatering: Privat helse og velferd</h2>
  <p style="color:#7f8c8d;font-size:.9em">{dato}</p>
  {artikler_html}
  <hr style="margin-top:24px">
  {footer_html}
  <p style="color:#bdc3c7;font-size:.8em">Generert automatisk av Nyhetsagenten</p>
</body></html>"""


def build_plain(result: dict, timestamp: datetime) -> str:
    saker = result.get("saker", [])
    dato = timestamp.strftime("%-d. %B %Y, kl. %H:%M")
    lines = [f"Nyhetsoppdatering: Privat helse og velferd — {dato}", ""]
    if not saker:
        lines.append("Ingen relevante saker funnet siden forrige utsending.")
    else:
        for s in saker:
            lines += [s["tittel"], f"{s['kilde']} · {s['publisert']}", s["url"], s["hvorfor_relevant"], ""]
    kilder_uten_funn = result.get("kilder_uten_funn", [])
    if kilder_uten_funn:
        lines.append(f"Også gjennomsøkt uten relevante funn: {', '.join(kilder_uten_funn)}.")
    return "\n".join(lines)


# --- E-postsending ---

def load_recipients() -> list[str]:
    lines = (SCRIPT_DIR / "recipients.txt").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def send_email(recipients: list[str], result: dict, timestamp: datetime, images: dict[str, str]) -> None:
    subject = f"Nyheter helse/velferd — {timestamp.strftime('%d.%m.%Y %H:%M')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(build_plain(result, timestamp), "plain", "utf-8"))
    msg.attach(MIMEText(build_html(result, timestamp, images), "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, recipients, msg.as_string())


# --- Hovedflyt ---

def main() -> None:
    now = datetime.now()
    log = lambda msg: print(f"[{now.strftime('%H:%M:%S')}] {msg}", flush=True)

    recipients = load_recipients()
    if not recipients:
        log("Ingen mottakere i recipients.txt — avslutter.")
        sys.exit(1)

    log("Henter RSS-feeds (siste 24 timer)...")

    all_articles: list[dict] = []
    for name, url in RSS_FEEDS.items():
        articles = fetch_feed(name, url)
        all_articles.extend(articles)
        n_img = sum(1 for a in articles if a.get("image_url"))
        print(f"  {name}: {len(articles)} saker ({n_img} med bilde)", flush=True)

    images = {a["url"]: a["image_url"] for a in all_articles if a.get("image_url")}

    if not all_articles:
        log("Ingen saker funnet — avslutter uten å sende.")
        return

    log(f"Sender {len(all_articles)} saker til Claude for vurdering...")
    result = find_relevant(all_articles)
    n_relevant = len(result.get("saker", []))
    log(f"Claude valgte {n_relevant} relevante saker. Sender e-post til {recipients}...")

    send_email(recipients, result, now, images)
    log("E-post sendt.")


if __name__ == "__main__":
    main()
