#!/usr/bin/env bash
set -e

OUT_DIR="/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/composer-foundation/public/vendor/prosemirror"
mkdir -p "$OUT_DIR"

# Pinned versions — all mature/stable (released well over 7 days ago, per org dep rule)
PKGS=(
  "prosemirror-state:1.4.3"
  "prosemirror-view:1.33.6"
  "prosemirror-model:1.23.0"
  "prosemirror-transform:1.10.2"
  "prosemirror-schema-basic:1.2.3"
  "prosemirror-schema-list:1.4.1"
  "prosemirror-example-setup:1.2.3"
  "prosemirror-keymap:1.2.2"
  "prosemirror-history:1.4.1"
  "prosemirror-commands:1.6.2"
  "prosemirror-inputrules:1.4.0"
  "prosemirror-dropcursor:1.8.1"
  "prosemirror-gapcursor:1.3.2"
  "prosemirror-menu:1.2.4"
)

# All PM package names — these become "externals" so each bundle's PM imports
# stay as bare specifiers, resolved by import-map to the local vendored files
# (keeps a single class identity across packages).
EXTERNAL_PM="prosemirror-state,prosemirror-view,prosemirror-model,prosemirror-transform,prosemirror-schema-basic,prosemirror-schema-list,prosemirror-example-setup,prosemirror-keymap,prosemirror-history,prosemirror-commands,prosemirror-inputrules,prosemirror-dropcursor,prosemirror-gapcursor,prosemirror-menu"

for entry in "${PKGS[@]}"; do
  PKG="${entry%%:*}"
  VER="${entry##*:}"
  # Exclude self from externals (esm.sh complains otherwise)
  EXT=$(echo "$EXTERNAL_PM" | sed -E "s/(^|,)${PKG}(,|$)/\1/; s/^,//; s/,$//; s/,,/,/g")
  URL="https://esm.sh/${PKG}@${VER}?bundle-deps&external=${EXT}&target=es2022"
  echo ">> Fetching $PKG@$VER"
  REDIR=$(curl -sL "$URL" | grep -oE 'from "/[^"]+"' | head -1 | sed -E 's/from "([^"]+)"/\1/')
  if [ -z "$REDIR" ]; then
    echo "FAILED to resolve $PKG"
    curl -sL "$URL" | head -5
    exit 1
  fi
  REAL_URL="https://esm.sh${REDIR}"
  curl -sL "$REAL_URL" -o "$OUT_DIR/${PKG}.mjs"
  SIZE=$(wc -c < "$OUT_DIR/${PKG}.mjs")
  echo "   -> $OUT_DIR/${PKG}.mjs (${SIZE} bytes)"
done

echo "Done."
ls -la "$OUT_DIR"
