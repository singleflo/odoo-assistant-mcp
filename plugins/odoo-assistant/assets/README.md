# assets/

The plugin icon (`icon.png`, composer icon) and logo (`logo.png`) land here
with the listing dossier work: a Pillow script renders a 512x512 PNG and
copies both files into this directory. Until then, `composerIcon` and `logo`
are deliberately absent from `plugin.json` — the plugin spec rejects icon
fields that do not point at real files.
