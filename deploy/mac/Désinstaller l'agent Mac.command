#!/bin/bash
# Double-cliquer pour retirer l'agent Mac du MES v3 (Colima et le MES ne sont pas touchés).
LABEL=com.mes-v3.agent-mac
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/Desktop/Poste Mac MES.webloc"
echo "Agent Mac retiré. Colima et le MES continuent de fonctionner normalement."
read -r -p "Appuyez sur Entrée pour fermer." _
