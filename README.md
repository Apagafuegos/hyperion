# Hyperion

A calm, instrument-dense service atlas for everything deployed on the VPS.

Read-only control surface: curated YAML catalog, Docker Compose and systemd
runtime inspection, public-route probing, bounded on-demand logs, and an
authenticated single-page atlas client.

## Documents

- `PRODUCT.md` — product boundary and priorities
- `DESIGN.md`, `.impeccable/design.json` — the approved Service Atlas visual system
- `TECHNICAL-DESIGN.md` — the final implementation contract
- `docs/superpowers/plans/2026-08-04-hyperion-v1-part*.md` — implementation plan

## Development

Fixture mode (no Docker needed):

    HYPERION_FIXTURE_MODE=1 HYPERION_CATALOG_PATH=tests/fixtures/fixture-services.yaml \
      uv run uvicorn hyperion.main:app --host 127.0.0.1 --port 8787 --workers 1
