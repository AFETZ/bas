#!/usr/bin/env bash
# Publish wiki-build/ → GitHub wiki repo (separate from main repo).
set -e
cd ~/bas-prototype
WIKI_URL="$(git remote get-url origin | sed 's/\.git$/.wiki.git/')"
echo "wiki url: $WIKI_URL"
CLONE=/tmp/bas.wiki
rm -rf "$CLONE"
git clone "$WIKI_URL" "$CLONE"
cp wiki-build/*.md "$CLONE"/
mkdir -p "$CLONE/images"
# Картинки и анимации (png + gif). MP4 в вики не кладём (ссылаемся на репо).
cp wiki-build/images/*.png "$CLONE/images"/ 2>/dev/null || true
cp wiki-build/images/*.gif "$CLONE/images"/ 2>/dev/null || true
cd "$CLONE"
git add -A
if git diff --cached --quiet; then
    echo "WIKI: no changes"
    exit 0
fi
git -c user.name="$(cd ~/bas-prototype && git config user.name)" \
    -c user.email="$(cd ~/bas-prototype && git config user.email)" \
    commit -m "docs: Interfaces — управление дроном из витрины (Admin→Web GCS) + скриншот"
git push
echo "WIKI: pushed"
