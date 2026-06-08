# ProseMirror — vendored ESM bundles

These files were fetched from esm.sh and ship with the app. **No runtime CDN.**
Each file is a `?bundle-deps&external=<other-PM-pkgs>` build for `target=es2022`:
its non-ProseMirror transitive dependencies (orderedmap, w3c-keyname, rope-sequence)
are inlined, but other ProseMirror packages remain as bare-specifier imports — the
import map in `writing-studio.html` (and the dynamic-loader fallback in
`writing-studio.js`) resolves them back to these local files so every package
shares one copy of each PM class (`instanceof EditorState` keeps working).

## Pinned versions (all mature, well over 7 days old — org dep rule satisfied)

| Package                       | Version  |
|-------------------------------|----------|
| prosemirror-state             | 1.4.3    |
| prosemirror-view              | 1.33.6   |
| prosemirror-model             | 1.23.0   |
| prosemirror-transform         | 1.10.2   |
| prosemirror-schema-basic      | 1.2.3    |
| prosemirror-schema-list       | 1.4.1    |
| prosemirror-example-setup     | 1.2.3    |
| prosemirror-keymap            | 1.2.2    |
| prosemirror-history           | 1.4.1    |
| prosemirror-commands          | 1.6.2    |
| prosemirror-inputrules        | 1.4.0    |
| prosemirror-dropcursor        | 1.8.1    |
| prosemirror-gapcursor         | 1.3.2    |
| prosemirror-menu              | 1.2.4    |

## How to re-vendor (offline-safe — done once per upgrade)

The fetch script lives in the repo at `scripts/vendor-prosemirror.sh`. Re-running it
overwrites these files. esm.sh is consulted only at vendor time, never at runtime.

## CSS

- `prosemirror.css` — minimal stylesheet from `prosemirror-view@1.33.6/style/prosemirror.css`.
- `prosemirror-gapcursor.css` — from `prosemirror-gapcursor@1.3.2/style/gapcursor.css`.
