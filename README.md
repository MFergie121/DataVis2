# Near, not served — Victoria's venues and public transport

A single-page data visualisation for **FIT2179 Data Visualisation 2** (Monash University, Semester 1 2026).

A six-part story about Victorian licensed hospitality venues and how close they sit to public
transport. The page sets up the intuition that wealthier areas are better served, then overturns it:
once you separate metropolitan from regional Victoria, access to transport barely tracks
socio-economic advantage. The real divide is geographic, not socio-economic.

> **Status:** in development. The page shell, runtime, and data pipeline are in place; the ten
> Vega-Lite charts (including three maps) are being built into `src/specs/`.

## Repository layout

```
src/
  index.html      — the page
  css/            — styles
  specs/          — one human-readable Vega-Lite JSON spec per chart
  data/           — trimmed datasets the page loads (~2.8 MB total)
```

## Data sources

Two-plus combined, real, recent Australian open-data sources:

- **Victorian liquor licences by location** — Liquor Control Victoria / data.vic.gov.au
- **Public transport stops** — Department of Transport and Planning, Victoria
- **ABS** — Regional Population by SA2, SEIFA 2021, and ASGS 2021 SA2 boundaries

All are Australian-government open data (generally Creative Commons Attribution 4.0); see the
submission writeup for exact per-source licence wording. "Near public transport" means proximity
to a stop, not service frequency. The analysis describes associations only, not causation.

## Built with

[Vega-Lite](https://vega.github.io/vega-lite/) via [vega-embed](https://github.com/vega/vega-embed)
(pinned: vega 5.30.0 / vega-lite 5.21.0 / vega-embed 6.26.0). Hosted on GitHub Pages.

## Acknowledgements

Author: Max Fergie. Built with the assistance of an AI coding agent (Claude); see the Moodle
submission for the full AI-use acknowledgement.
