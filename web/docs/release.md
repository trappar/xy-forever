# Release and GitHub Pages

The image the web app installs, and its deployment ([the web app](../README.md)).

## The release image

Install and Update use the `os.ldr` whose SHA-256 `src/install/release.json` pins, fetched from this site at
`release/os.ldr`. `python3 scripts/web_release.py [BUILD] --notes "What changed"` (from the repository root) takes a verified product
build, checks it shares no bytes with the stock pair (`xy_forever/stock_bytes.py`; `xyf_build.py --verify` checks it
too), writes the pin (committed in a release pull request) and stages the image in `public/release/` (gitignored);
`--check` says whether the staged image matches the pin. Without a pin the page says there is nothing to install.

## GitHub Pages

The planned launch URL is `https://trappar.github.io/xy-forever/`. Deployment and the public firmware
asset are pending. A local Install or Update run requires a staged image matching the pin.
The app and the pinned image share that project path. The project and third-party notices are served at `LICENSES.txt`. Only `web/dist/` goes into the Pages artifact.

From the repository root, stage the `os.ldr` matching the checked-in pin in `web/public/release/`.
Use the locally verified build or download the same asset from the published `v<version>` release.
Then run the deployment checks with Chrome installed.

```sh
python3 scripts/web_release.py --check
pnpm --filter xy-forever-web run build:pages
pnpm --filter xy-forever-web run smoke:pages
```

The smoke test serves the built files at `/xy-forever/` on localhost without a fallback for missing
paths. It checks direct links, reloads, back and forward, and the Install page's download of the
pinned image.

At launch, select [GitHub Actions as the Pages source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
Attach the staged `os.ldr` to the GitHub release tagged `v<version>` from `release.json`, then run
**Deploy web app to GitHub Pages** on `main`. The workflow fetches that one asset, checks it against the
pin, builds and runs the smoke test before deploying. It uses Node dependencies and the verified
release asset. After it finishes, open the HTTPS site, reload an install stage and check the
SHA-256 of `/xy-forever/release/os.ldr` against the release pin.
