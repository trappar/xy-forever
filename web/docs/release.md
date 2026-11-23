# Release and Cloudflare Pages

The image the web app installs, and its deployment ([the web app](../README.md)).

## The release image

Install and Update use the `os.ldr` whose SHA-256 `src/install/release.json` pins, fetched from this site at
`release/os.ldr`. `uv run scripts/web_release.py [BUILD] --notes "What changed"` (from the repository root) takes a verified product
build, checks it shares no bytes with the stock pair (`xy_forever/stock_bytes.py`; `xyf_build.py --verify` checks it
too), writes the pin (committed in a release pull request) and stages the image in `public/release/` (gitignored);
`--check` says whether the staged image matches the pin. Without a pin the page says there is nothing to install.

## Preview a release

The production origin is `https://xy-forever.trappar.com`. The app, `/release/os.ldr`, and the
project and third-party notices at `/LICENSES.txt` all come from `web/dist/`. Direct Upload takes
that build folder from GitHub Actions while the repository can stay private.

From the repository root, stage the `os.ldr` matching the checked-in pin in `web/public/release/`.
Use the locally verified build or the exact pinned asset from a GitHub release if one exists.
Check the pin, build the site, and serve the built files locally with Wrangler.

```sh
uv run scripts/web_release.py --check
pnpm --filter xy-forever-web run build
npx --yes wrangler@4.167.0 pages dev web/dist --ip 127.0.0.1 --port 8788
```

With the preview running, run `pnpm --filter xy-forever-web run smoke:pages` in another terminal
with Chrome installed. It checks direct links, reloads, back and forward navigation, missing
routes and assets, and the Install page's download of the pinned image.

## Configure deployment

To create an empty [Direct Upload project](https://developers.cloudflare.com/pages/get-started/direct-upload/),
sign in with `npx --yes wrangler@4.167.0 login` and run
`npx --yes wrangler@4.167.0 pages project create xy-forever --production-branch=main`.
Add `xy-forever.trappar.com` in the project's
[Custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/) settings.

In the GitHub repository, open **Settings → Secrets and variables → Actions**. Add
`CLOUDFLARE_API_TOKEN` as a **repository secret** and `CLOUDFLARE_ACCOUNT_ID` as a
**repository variable**. Give the [API token](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)
the **Account → Cloudflare Pages → Edit** permission scoped to the account that owns the Pages project.

## Deploy an approved release

Attach the verified `os.ldr` to the repository's GitHub release tagged `v<version>`, where
`<version>` matches `web/src/install/release.json` on `main`. After publication is approved in
[#223](https://github.com/trappar/xy-forever/issues/223), open **Actions → Deploy web app to
Cloudflare Pages** and choose **Run workflow** on `main`.

The workflow downloads the pinned release asset with GitHub's built-in token, verifies the image,
builds and smoke-tests the site with Wrangler, and uploads the checked output to the `xy-forever`
project's production branch.

Open the production HTTPS site, reload an install stage and compare the SHA-256 of
`/release/os.ldr` with the release pin.
