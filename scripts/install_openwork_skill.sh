#!/bin/zsh
# Install the "facebook-brand-reviews" skill into OpenWork, pointing at this folder.
#   scripts/install_openwork_skill.sh                 # into every local OpenWork workspace
#   scripts/install_openwork_skill.sh /path/to/workspace
# Run it again after moving this folder or after `git pull`.
cd "$(dirname "$0")/.." || exit 1
TOOL_DIR="$(pwd)"
SRC="$TOOL_DIR/openwork/skills/facebook-brand-reviews/SKILL.md"
targets=()
if [ -n "$1" ]; then
  targets=("$1")
else
  cfg="$HOME/Library/Application Support/com.differentai.openwork/openwork-workspaces.json"
  if [ -f "$cfg" ]; then
    while IFS= read -r p; do [ -n "$p" ] && targets+=("$p"); done < <(/usr/bin/python3 -c "
import json,sys; d=json.load(open(sys.argv[1]))
print('\n'.join(w['path'] for w in d.get('workspaces',[]) if w.get('workspaceType')=='local' and w.get('path')))" "$cfg")
  fi
fi
[ ${#targets[@]} -gt 0 ] || { echo "No OpenWork workspace found. Pass its folder: scripts/install_openwork_skill.sh /path/to/workspace"; exit 1; }
for ws in "${targets[@]}"; do
  dst="$ws/.opencode/skills/facebook-brand-reviews"
  mkdir -p "$dst" && sed "s|__TOOL_DIR__|$TOOL_DIR|g" "$SRC" > "$dst/SKILL.md" && echo "installed: $dst/SKILL.md"
done
echo "Restart OpenWork (or open a new session) so it picks up the skill."
