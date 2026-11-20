# Deploying djdigger.app

The Website workflow builds the public site from main for a published stable
GitHub release or a manual dispatch on main. To deploy just the website:

```sh
gh workflow run site.yml --ref main
```

The environment `website` contains the following GitHub Actions variables:

| Variable | Production value |
| --- | --- |
| SITE_URL | https://djdigger.app |
| FIREBASE_PROJECT_ID | dj-digger-a9664 |
| GA4_MEASUREMENT_ID | G-YCNWMX1PQL |
| FIREBASE_IDENTITY_PROVIDER | projects/931896217000/locations/global/workloadIdentityPools/github-website/providers/actions |
| FIREBASE_DEPLOY_ACCOUNT | website-deploy@dj-digger-a9664.iam.gserviceaccount.com |

GitHub requests a short-lived Google credential through Workload Identity
Federation. The provider checks the repository and owner IDs, environment, and
workflow ref. The deploy service account has Firebase Hosting Admin on this
project. No JSON service-account key is needed.

The build checks for the newest stable release's installers and checksums, then waits for
the PyPI wheel while its independent publish job runs. It verifies the tag and main commit again before deployment. Downloads
on the site, including the generated Linux install script, then target that
release. Publishing the website does not submit an application package or tag.

Record the previous Hosting version before deployment. If a production check
fails, use the prior version's rollback action in the Firebase Hosting release
history. The deploy message records the main SHA and public application version.

## Local verification

From the repository root:

```sh
export DOWNLOAD_VERSION="$(python3 scripts/site_release.py | cut -d= -f2)"
cd site
npm ci
npm run build
```

Leave GA4_MEASUREMENT_ID unset for a preview without measurement. To exercise
consent in a test browser, use a test ID and intercept Google requests. The
production tag sends one page view per accepted page, installer events named
`download_windows_x64`, `download_macos_arm64`, `download_macos_x86_64`, and
`copy_linux_command`. In Analytics use Reports → Engagement → Events, or a
funnel exploration with the download page and these events. These events measure
actions on the website, while GitHub release asset counts measure file requests.
The website privacy text describes the two-month user/event retention setting
and 180-day cookie lifetime. Keep the property and consent text aligned.
