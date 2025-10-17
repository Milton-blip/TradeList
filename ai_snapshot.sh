#!/usr/bin/env bash
set -euo pipefail

REPO="Milton-blip/PortfolioAnalytics2"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
SHA="$(git rev-parse HEAD)"

FILES=(
  "TradesList.py"
  "portfolio_trades/cli.py"
  "portfolio_trades/engine.py"
  "portfolio_trades/io_utils.py"
  "portfolio_trades/conventions.py"
  "portfolio_trades/report_pdf.py"
  "portfolio_trades/fonts.py"
)

echo "Branch: $BRANCH"
echo "Commit: $SHA"
echo
echo "Raw URLs pinned to this commit:"
for f in "${FILES[@]}"; do
  echo "https://raw.githubusercontent.com/${REPO}/${SHA}/${f}"
done
