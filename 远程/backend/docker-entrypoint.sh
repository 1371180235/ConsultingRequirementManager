#!/bin/sh
set -eu

python -m app.migrate
python -m app.seed

exec "$@"
