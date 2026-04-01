# Norsk nyhetsagent

## Prosjektbeskrivelse
AI-agent som søker på norske nyhetssteder etter saker som passer til et spesifikt formål (defineres nærmere). Bygget med Claude Agent SDK.

## Arkitektur
- **Hoved-agent**: Koordinerer søk og filtrering
- **Sub-agenter**: Spesialiserte agenter per nettsted eller søketype
- Følger retningslinjer i `Claude docs/`

## Utvikling
- Python eller TypeScript (avgjøres)
- `claude -p` med `--bare` for skriptede kall
- Strukturert output via `--output-format json`

## Konvensjoner
- Norsk som primærspråk i kode-kommentarer og docs
- Ikke legg til feilhåndtering for scenarioer som ikke kan skje
- Ikke opprett hjelpefunksjoner for engangsoperasjoner
- Kommiter bare ved eksplisitt forespørsel
