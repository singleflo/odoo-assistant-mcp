#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--diff|--apply]\n' "$0"
}

mode=diff
case "${1:-}" in
  "") ;;
  --diff) mode=diff ;;
  --apply) mode=apply ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$script_dir/.." && pwd)
skill_dir=${ODOO_SKILL_DIR:-"$HOME/.agents/skills/odoo"}
script_target="$repo_dir/src/odoo_assistant/odoo_scripts"
reference_target="$repo_dir/references"

if [[ ! -d "$skill_dir/scripts" || ! -d "$skill_dir/references" ]]; then
  printf 'Canonical skill not found: %s\n' "$skill_dir" >&2
  exit 1
fi

copy_file() {
  local source=$1
  local target=$2

  if [[ "$mode" == diff ]]; then
    if [[ -f "$target" ]]; then
      diff -u "$target" "$source" || true
    else
      printf 'New file: %s\n' "$target"
    fi
  else
    mkdir -p "$(dirname -- "$target")"
    cp "$source" "$target"
  fi
}

shopt -s nullglob
for source in "$skill_dir/scripts/"*.py "$skill_dir/references/"*.md; do
  if [[ "$source" == "$skill_dir/scripts/"* ]]; then
    copy_file "$source" "$script_target/$(basename -- "$source")"
  else
    copy_file "$source" "$reference_target/$(basename -- "$source")"
  fi
done
copy_file "$skill_dir/SKILL.md" "$reference_target/SKILL.md"

if [[ "$mode" == diff ]]; then
  printf 'Preview only; no files changed. Use --apply to copy skill → repo.\n'
else
  printf 'Synced skill → repo. Review the diff before committing.\n'
fi
