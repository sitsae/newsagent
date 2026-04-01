# Norsk nyhetsagent

## Prosjektbeskrivelse
AI-agent som søker norske nyhetssteder etter saker som påvirker private helse- og velferdsbedrifters drift, økonomi eller rammebetingelser. Bygget med Claude Agent SDK.

## Formål
Fange opp nyheter om:
- **Politikk og regulering**: Lovforslag, stortingsdebatter, kommunale vedtak som berører privat sektor innen helse/velferd
- **Økonomi og finansiering**: Endringer i refusjonsordninger, anbud, tilskudd, skatt
- **Bransjehendelser**: Konkurser, oppkjøp, nye aktører, kapasitetsendringer
- **Meninger og debatt**: Kronikker, politikeruttalelser, brukerorganisasjoner som signaliserer retningsendringer
- **Tilsyn og kvalitet**: Statsforvalter, Helsetilsynet, Arbeidstilsynet — hendelser som kan gi presedens

### Relevante bransjer
Barnehager, barnevern, rusbehandling, psykisk helsevern, sykehjem, hjemmetjenester, arbeidsmarkedstiltak (NAV-leverandører), rehabilitering.

### Norske nyhetssteder å dekke
- Riksdekkende: VG, Dagbladet, Aftenposten, NRK, TV2, Dagens Næringsliv, E24
- Bransje/fagpresse: Dagens Medisin, Sykepleien, Fontene, Dagsavisen
- Politisk: Klassekampen, Nettavisen
- Lokalt: Relevante regionsaviser ved hendelser av nasjonal betydning

## Arkitektur
- **Hoved-agent**: Koordinerer søk, vurderer relevans og produserer oppsummering
- **Sub-agenter**: Én per kilde eller søketype (for å bevare kontekstvindu)
- Strukturert output: hver sak med tittel, kilde, dato, URL og kort relevansbeskrivelse
- Følger retningslinjer i `Claude docs/`

## Utvikling
- Python (primær) med Claude Agent SDK
- `claude -p --bare` for skriptede/planlagte kjøringer
- `--output-format json` + JSON Schema for strukturert output
- Planlagt kjøring: daglig eller ved behov

## Oppsett (én gang)
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Installer launchd-jobb (kjører kl. 10:00 og 14:00)
cp com.nyhetsagent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nyhetsagent.plist

# Test manuelt
bash run.sh
```

## Administrasjon
```bash
# Stopp planlagt jobb
launchctl unload ~/Library/LaunchAgents/com.nyhetsagent.plist
# Start igjen
launchctl load ~/Library/LaunchAgents/com.nyhetsagent.plist
# Se logg
tail -f logs/agent.log
```

## Nøkkelfiler
- `agent.py` — hoved-skript (søk + e-post)
- `recipients.txt` — mottakerliste (én adresse per linje)
- `.env` — Gmail-credentials (ikke i git)
- `run.sh` — wrapper for launchd
- `com.nyhetsagent.plist` — launchd-konfig (10:00 og 14:00)

## Konvensjoner
- Norsk som primærspråk i kode-kommentarer og docs
- Ikke legg til feilhåndtering for scenarioer som ikke kan skje
- Ikke opprett hjelpefunksjoner for engangsoperasjoner
- Kommiter bare ved eksplisitt forespørsel
