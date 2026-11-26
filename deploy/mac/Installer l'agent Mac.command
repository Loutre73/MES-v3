#!/bin/bash
# Double-cliquer sur ce fichier dans le Finder pour installer l'agent Mac du MES v3.
# Il démarre avec la session et sert la page « Poste Mac » : http://localhost:9300
# Rien n'est installé en dehors de votre compte, aucun mot de passe administrateur n'est demandé.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd -P)"
DEPLOY="$(dirname "$DIR")"
LABEL=com.mes-v3.agent-mac
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=9300

echo "Installation de l'agent Mac du MES v3"
echo "Dossier du MES : $DEPLOY"
echo

PY=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 est introuvable. macOS va proposer d'installer les « outils de ligne de commande » :"
  echo "acceptez, attendez la fin, puis double-cliquez à nouveau sur ce fichier."
  xcode-select --install 2>/dev/null || true
  read -r -p "Appuyez sur Entrée pour fermer." _; exit 1
fi
command -v colima >/dev/null 2>&1 || [ -x /opt/homebrew/bin/colima ] || [ -x /usr/local/bin/colima ] || echo "Attention : Colima est introuvable, les boutons Colima ne fonctionneront pas."

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$(xml "$PY")</string><string>$(xml "$DIR/agent_mac.py")</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>MES_DEPLOY</key><string>$(xml "$DEPLOY")</string>
    <key>MES_AGENT_PORT</key><string>$PORT</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(xml "$HOME/Library/Logs/mes-agent-mac.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$HOME/Library/Logs/mes-agent-mac.log")</string>
</dict>
</plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in $(seq 1 20); do
  curl -sf "http://localhost:$PORT/api/ping" >/dev/null 2>&1 && break
  sleep 0.5
done
if ! curl -sf "http://localhost:$PORT/api/ping" >/dev/null 2>&1; then
  echo "L'agent ne répond pas. Journal : $HOME/Library/Logs/mes-agent-mac.log"
  tail -20 "$HOME/Library/Logs/mes-agent-mac.log" 2>/dev/null || true
  read -r -p "Appuyez sur Entrée pour fermer." _; exit 1
fi

# Raccourci sur le Bureau (macOS peut demander l'autorisation d'accéder au Bureau)
cat > "$HOME/Desktop/Poste Mac MES.webloc" 2>/dev/null <<WL || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>URL</key><string>http://localhost:$PORT/</string></dict></plist>
WL

echo "Agent installé. Il démarrera tout seul à chaque ouverture de session."
echo "Page « Poste Mac » : http://localhost:$PORT/  (raccourci « Poste Mac MES » sur le Bureau)"
open "http://localhost:$PORT/"
echo
echo "Vous pouvez fermer cette fenêtre."
