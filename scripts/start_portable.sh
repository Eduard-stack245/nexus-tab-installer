#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m nexus_tab_installer.ui.flet_app
