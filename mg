#!/usr/bin/env bash
# Entry point: ./mg import | serve | doctor | wipe
cd "$(dirname "$0")"
exec .venv/bin/python -m app.cli "$@"
