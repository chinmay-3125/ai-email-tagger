#!/usr/bin/env bash
set -euo pipefail

pip install -r requirements.txt
python -m spacy download en_core_web_md
echo "Setup complete. Run: python app.py"
