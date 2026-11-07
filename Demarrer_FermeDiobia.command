#!/bin/bash
# Double-cliquez sur ce fichier pour lancer La Ferme Diobia – ERP sur ce Mac.
cd "$(dirname "$0")"
PORT=8090
echo "🐔 La Ferme Diobia – ERP avicole"

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ Python 3 n'est pas installé. Installez-le depuis https://www.python.org/downloads/ puis relancez."
  read -n 1 -s -r -p "Appuyez sur une touche pour fermer…"; exit 1
fi

lsof -ti:$PORT | xargs kill -9 2>/dev/null
lsof -ti:5060 | xargs kill -9 2>/dev/null

if [ ! -d ".venv" ]; then
  echo "📦 Première installation (une seule fois, environ 1 minute)…"
  python3 -m venv .venv || exit 1
fi
source .venv/bin/activate
pip install -q --disable-pip-version-check -r requirements.txt || { echo "❌ Installation impossible (connexion internet ?)"; exit 1; }

# Sauvegarde automatique à chaque démarrage (30 dernières conservées)
mkdir -p sauvegardes
[ -f instance/diobia.db ] && cp instance/diobia.db "sauvegardes/diobia_$(date +%Y%m%d_%H%M).db" && ls -t sauvegardes/diobia_*.db | tail -n +31 | xargs rm -f 2>/dev/null

# Données de démonstration au tout premier lancement
if [ ! -f instance/diobia.db ]; then
  echo "🧪 Chargement des données de démonstration (supprimables dans Paramètres)…"
  FLASK_APP=run.py flask demo >/dev/null 2>&1
fi

echo "🚀 Démarrage sur http://127.0.0.1:$PORT"
mkdir -p logs
PORT=$PORT nohup python run.py >> logs/diobia.log 2>&1 &
echo $! > .diobia.pid
sleep 3
open "http://127.0.0.1:$PORT"
echo "✅ Application ouverte dans votre navigateur. Vous pouvez fermer cette fenêtre."
echo "   Connexion : admin@fermediobia.sn / Diobia2026!  (changez le mot de passe dans Paramètres)"
