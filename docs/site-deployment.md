# Deploying the website

The project website lives in `site/` and is served by Firebase Hosting
(`firebase.json`). Nothing is deployed until the steps below are done once; until
then the `Website` workflow ends green with a notice.

## One-time setup

1. **Create the Firebase project.** Sign in to the [Firebase console](https://console.firebase.google.com/)
   with the Google account that should own the site, create a project and open
   **Build → Hosting → Get started**. Note the **project ID** (not the display name).
2. **Choose the address.** The site is available at `https://<project-id>.web.app`
   straight away. For your own domain, use **Hosting → Add custom domain** and add
   the DNS records it shows.
3. **Create a deploy key.** In the [Google Cloud console](https://console.cloud.google.com/iam-admin/serviceaccounts)
   of the same project, create a service account (for example `github-site-deploy`)
   with the role **Firebase Hosting Admin**, then **Keys → Add key → JSON**. Keep the
   downloaded file out of the repository.
4. **Tell GitHub.** In the repository, open **Settings → Environments → New
   environment**, name it `website`, and add:
   - secret `FIREBASE_SERVICE_ACCOUNT`: the whole content of the JSON key file;
   - variable `FIREBASE_PROJECT_ID`: the project ID;
   - variable `SITE_URL`: the public address without a trailing slash, for example
     `https://<project-id>.web.app`.

   Add yourself as a required reviewer of the environment if a deploy should wait
   for your approval. Delete the local copy of the key file.
5. **Deploy.** Run **Actions → Website → Run workflow**. From then on the site is
   also deployed whenever a stable release is published.

`SITE_URL` is the only place the address is set: canonical URLs, Open Graph tags,
`sitemap.xml`, `robots.txt` and the Linux one-liner on the download page are built
from it. After the address is final, you may point the one-liner in `README.md` and
`docs/installation.md` at `https://<address>/install.sh` instead of the GitHub copy.

## What the workflow checks

Download links on the site carry the version from `pyproject.toml`. Before building,
the workflow asks GitHub for the three installers of that version
(`windows-x64-test.exe`, `macos-arm64-test.dmg`, `macos-x86_64-test.dmg`) and stops
if one is missing, so the site never links to a file that does not exist. If a
release is published before its installers are attached, run the workflow again by
hand once they are there.

## Deploying by hand

The `firebase` command on a workstation may be signed in to a different Google
account. Add the right one without removing the other, and select it for this
directory only:

```bash
firebase login:add you@example.com
firebase login:use you@example.com
```

Then build with the public address and deploy:

```bash
cd site
npm ci
SITE_URL=https://<project-id>.web.app npm run build
cd ..
firebase deploy --only hosting --project <project-id>
```

`firebase hosting:channel:deploy preview --project <project-id>` publishes the same
build to a temporary preview address instead of the live site.
