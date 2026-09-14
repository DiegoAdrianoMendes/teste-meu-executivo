#!/bin/sh
set -eu

if [ -d src/modules/enterprise ] && find src/modules/enterprise -type f -print -quit | grep -q .; then
  echo "Refusing to build initialized private frontend enterprise gitlink" >&2
  exit 1
fi

patch --batch --forward --strip=1 < /opt/chattermate-overlay/local-runtime.patch

# Build the public widget from the same source as the dashboard. Its module URL
# is used at runtime to discover the gateway origin, including when embedded in
# a srcdoc iframe on another website.
mkdir -p ../backend/assets public/assets public/webclient
npm run type-check
npm run build:widget
cp ../backend/assets/widget.js public/assets/widget.js
cp ../backend/assets/widget.css public/assets/widget.css

# The stock helper bakes a deployment URL into the loader. Build the patched
# loader directly so it discovers the origin of its own <script> URL instead.
./node_modules/.bin/esbuild src/webclient/chattermate.js \
  --bundle \
  --minify \
  --outfile=public/webclient/chattermate.min.js \
  --format=iife \
  --target=es2015 \
  --define:process.env.NODE_ENV=\"production\"

npm run build-only

test -s dist/index.html
test -s dist/assets/widget.js
test -s dist/assets/widget.css
test -s dist/webclient/chattermate.min.js

if grep -R -n -E 'https?://api\.chattermate\.chat|wss://api\.chattermate\.chat|localhost:8000' \
  dist/assets/widget.js dist/webclient/chattermate.min.js; then
  echo "Public ChatterMate artifacts still contain an upstream API origin" >&2
  exit 1
fi
