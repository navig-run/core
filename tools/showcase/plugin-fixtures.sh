#!/usr/bin/env bash
# Off-camera fixtures for the plugin demos (tapes/plugins/*.tape). Called from a Hidden
# block inside the sandbox work dir. Everything here is generated — no real photos, apps
# or credentials ever appear in a recording.
#
#   bash plugin-fixtures.sh blackbox   # app.py that installs the crash handler, then crashes
#   bash plugin-fixtures.sh dedupe     # photos/ with three near-duplicates
#   bash plugin-fixtures.sh devhost    # a dev server on :5173
set -euo pipefail

case "${1:-}" in
  blackbox)
    cat > app.py <<'PY'
import navig_blackbox as bb

bb.install_crash_handler()


def charge(order):
    return order["total"] / order["items"]


charge({"total": 42, "items": 0})
PY
    ;;
  dedupe)
    mkdir -p photos
    python3 - <<'PY'
import random
import shutil

from PIL import Image, ImageDraw

for seed, name in [(1, "IMG_2041"), (2, "IMG_2042"), (3, "beach-sunset"), (4, "IMG_2050")]:
    r = random.Random(seed)
    im = Image.new("RGB", (1600, 1066), (r.randrange(40, 90), r.randrange(60, 120), r.randrange(90, 160)))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y, s = r.randrange(1600), r.randrange(1066), r.randrange(40, 260)
        d.ellipse([x, y, x + s, y + s], fill=(r.randrange(256), r.randrange(256), r.randrange(256)))
    im.save(f"photos/{name}.jpg", quality=92)
Image.open("photos/IMG_2041.jpg").resize((800, 533)).save("photos/IMG_2041 (1).jpg", quality=70)
Image.open("photos/IMG_2042.jpg").save("photos/IMG_2042-edit.jpg", quality=60)
shutil.copy("photos/beach-sunset.jpg", "photos/beach-sunset copy.jpg")
PY
    ;;
  devhost)
    mkdir -p site
    echo '<h1>my app is live on https://app.test</h1>' > site/index.html
    (cd site && nohup python3 -m http.server 5173 >/dev/null 2>&1 &)
    sleep 1
    ;;
  devhost-cleanup)
    pkill -f 'devhost up' || true
    pkill -f 'http.server 5173' || true
    navig devhost remove app.test >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: plugin-fixtures.sh blackbox|dedupe|devhost|devhost-cleanup" >&2
    exit 2
    ;;
esac
