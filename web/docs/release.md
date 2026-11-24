# Release and Cloudflare Pages

The images the web app installs, Stable and Canary, and its deployment ([the web app](../README.md)).

## The release image

Install and Update use the `os.ldr` whose SHA-256 `src/install/release.json` pins, fetched from this site at
`release/os.ldr`. `uv run scripts/web_release.py [BUILD] --notes "What changed"` (from the repository root) takes a verified product
build, writes the pin (committed in a release pull request) and stages the image in `public/release/` (gitignored);
`--check` says whether the staged image matches the pin. Without a pin the page says there is nothing to install.

## Canary

Canary is opt-in and experimental: a live build of whatever was last committed to `main`, not a scheduled one. Each
commit to `main` that changes the CI-built `os.ldr` publishes a GitHub prerelease tagged `canary-<commit SHA>`. Install (at its first step) and the Device page's update card offer it beside Stable when the
site has one, with its date, source commit and image; without one they offer Stable only.

How a build of `main` becomes a canary (`.github/workflows/firmware.yml`):

1. A push to `main` that changes the firmware's inputs (or a run started on `main`) builds the product `os.ldr` on
   macOS without stock files; `xyf-build` verifies it before it finishes. Website, catalog and documentation changes
   do not start it, so they keep the current canary.
2. A Linux job, only on `main` and never for a pull request, runs `uv run scripts/web_canary.py publish`: it checks
   the `os.ldr` against its build manifest and this checkout's version, and compares its SHA-256 with the newest
   canary's pin. The same bytes publish nothing. New bytes become prerelease `canary-<commit>` with two assets:
   `os.ldr` and its pin `release.json` (the stable pin's fields plus `channel`, `commit` and the commit's `date`).
   A new canary then starts the Pages deploy on `main` (**Deploy web app to Cloudflare Pages**); none, no deploy.
3. The next Pages deployment picks the newest eligible canary (`uv run scripts/web_canary.py stage`): its commit is
   on the deployed `main` and newer than the stable release's tag, its pin matches its tag and its `os.ldr`, and its
   image is not the stable one. It copies both assets to `web/public/canary/` (gitignored), and the stable pin to
   `web/public/release/release.json`.

Builds between releases share a version, so the app tells canaries apart by image: an update to a canary is offered
when the OP-XY runs another image of that version or an older version, and the update ends only when the restarted
OP-XY reports the chosen image. A channel switch (Stable to Canary, or a canary back to Stable's version) is offered
the same way.

## Preview a release

The production origin is `https://xy-forever.trappar.com`. The app, `/release/os.ldr`, and the
project and third-party notices at `/LICENSES.txt` all come from `web/dist/`. Direct Upload takes
that build folder from GitHub Actions while the repository can stay private.

From the repository root, stage the `os.ldr` matching the checked-in pin in `web/public/release/`.
Use the locally verified build or the exact pinned asset from a GitHub release if one exists.
Check the pin and stage the canary as the deployment does (or `uv run scripts/web_release.py --check` for Stable
only), build the site, and serve the built files locally with Wrangler.

```sh
uv run scripts/web_canary.py stage
pnpm --filter xy-forever-web run build
npx --yes wrangler@4.167.0 pages dev web/dist --ip 127.0.0.1 --port 8788
```

With the preview running, run `pnpm --filter xy-forever-web run smoke:pages` in another terminal
with Chrome installed. It checks direct links, reloads, back and forward navigation, missing
routes and assets, the Install page's download of the pinned image, and the canary when the build has one.

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

## When the site deploys

The site follows `main`. **Deploy web app to Cloudflare Pages** (`.github/workflows/pages.yml`) runs on `main` when:

- a push to `main` changes what the site build or the deploy reads: `web/` (not its tests or docs), `clients/ts/`,
  the license files in `LICENSES.txt`, the pnpm workspace and lockfile, the mise and uv pins, the scripts the deploy
  runs (`scripts/web_release.py`, `scripts/web_canary.py` and the `xy_forever` modules they import), the workflow
  and the shared CI action. The workflow's `paths` list is the exact set;
- the firmware workflow publishes a new canary (above);
- someone chooses **Run workflow** on `main`.

Pull requests never deploy. A newer run cancels one in progress: it builds a newer `main` and stages the newest
eligible canary, so the last to start wins.

## Deploy an approved release

Attach the verified `os.ldr` to the repository's GitHub release tagged `v<version>`, where
`<version>` matches `web/src/install/release.json` on `main`. After publication is approved in
[#223](https://github.com/trappar/xy-forever/issues/223), the merge that changes the pin deploys it; a deploy
fails while the pinned asset is missing, so attach it first, or run the workflow on `main` once it is attached.

The workflow downloads the pinned release asset with GitHub's built-in token, verifies the image,
adds the newest eligible canary, builds and smoke-tests the site with Wrangler, and uploads the checked output to the `xy-forever`
project's production branch.

Open the production HTTPS site, reload an install stage and compare the SHA-256 of
`/release/os.ldr` with the release pin.
