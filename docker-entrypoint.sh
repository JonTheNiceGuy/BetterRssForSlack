#!/usr/bin/env bash
set -euo pipefail

case "${1:-web}" in
  web)
    exec gunicorn --bind 0.0.0.0:8000 wsgi:app
    ;;
  worker)
    exec python worker_main.py
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown command: ${1:-}" >&2
    exit 1
    ;;
esac
