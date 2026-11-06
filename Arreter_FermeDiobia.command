#!/bin/bash
cd "$(dirname "$0")"
[ -f .diobia.pid ] && kill "$(cat .diobia.pid)" 2>/dev/null && rm -f .diobia.pid
lsof -ti:5060 | xargs kill -9 2>/dev/null
echo "🛑 La Ferme Diobia – ERP arrêtée."
