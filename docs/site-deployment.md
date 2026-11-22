# Deploying the website

The project website lives in `site/` and is served by Firebase Hosting from
project `dj-digger-a9664`, the default in `.firebaserc`. The public address is
[djdigger.app](https://djdigger.app/). A `Website` workflow run with missing
deployment settings skips the deploy and reports a notice.

## One-time setup

1. **Open the Firebase project.** Sign in to the [Firebase console](https://console.firebase.google.com/)
   with an account that has access to `dj-digger-a9664`, then open
   **Build → Hosting**. For a new site, create a project, enable Hosting and
   record its **project ID**.
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
   - variable `SITE_URL`: `https://djdigger.app` for this site, or the public
     address without a trailing slash for a new site.

   Add yourself as a required reviewer of the environment if a deploy should wait
   for your approval. Delete the local copy of the key file.
5. **Deploy.** Run **Actions → Website → Run workflow**. From then on the site is
   also deployed whenever a stable release is published.

The build uses `SITE_URL` for canonical URLs, Open Graph tags, `sitemap.xml`,
`robots.txt` and the Linux one-liner on the download page. The Linux
installation guide uses the public [`install.sh`](https://djdigger.app/install.sh).

## What the workflow checks

Download links on the site carry the version from `pyproject.toml`. Before building,
the workflow asks GitHub for the three installers of that version
(`windows-x64-test.exe`, `macos-arm64-test.dmg`, `macos-x86_64-test.dmg`) and stops
if one is missing, so the site never links to a file that does not exist. If a
release is published before its installers are attached, run the workflow again by
hand once they are there.

`npm run build` also checks the five pages in all seven languages. It compares
their sections, pictures, code blocks and page-level styles and scripts, and
checks their language tags and internal link language.

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
SITE_URL=https://djdigger.app npm run build
cd ..
firebase deploy --only hosting --project dj-digger-a9664
```

`firebase hosting:channel:deploy preview --project dj-digger-a9664` publishes the same
build to a temporary preview address instead of the live site.
