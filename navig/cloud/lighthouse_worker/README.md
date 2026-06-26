# lighthouse_worker (generated)

`worker.js` is the **prebuilt Lighthouse Worker bundle**, uploaded to the user's
Cloudflare account by `navig lighthouse deploy` (see `../lighthouse_deploy.py`).
It is a build artifact, not hand-edited.

## Regenerate

Source lives in the `navig-lighthouse` package. From there:

```bash
npm install
npm run build:bundle      # wrangler deploy --dry-run --outdir dist
```

then copy `navig-lighthouse/dist/index.js` → `navig/cloud/lighthouse_worker/worker.js`.

(`npm run build:bundle` followed by the copy is wrapped for releases; the Worker
itself is `tsc --noEmit`-clean and verified by `wrangler --dry-run`.)
