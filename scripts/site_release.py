"""Resolve the public downloads once, before building the static site."""
import json
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import urlopen


def stable_version(release):
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError(f'Expected a public stable tag, got {tag!r}')
    version = tag[1:]
    files = {a['name'] for a in release['assets'] if a['state'] == 'uploaded' and a['size'] > 0}
    required = {'SHA256SUMS'}
    for platform in ('windows-x64-test.exe', 'macos-arm64-test.dmg', 'macos-x86_64-test.dmg'):
        installer = f'dj-digger-{version}-{platform}'
        required.update((installer, installer + '.sha256'))
    if missing := required - files:
        raise ValueError(f'{tag} is missing {sorted(missing)}')
    return version


def main():
    release = subprocess.check_output(['gh', 'api', 'repos/fbialogrecki/dj-digger/releases/latest'])
    version = stable_version(json.loads(release))
    # Publishing to PyPI has an independent CI test matrix after a release.
    for attempt in range(60):
        try:
            with urlopen(f'https://pypi.org/pypi/dj-sc-digger/{version}/json', timeout=15) as response:
                package = json.load(response)
            if any(f['packagetype'] == 'bdist_wheel' and not f['yanked'] for f in package['urls']):
                print(f'DOWNLOAD_VERSION={version}')
                return
        except HTTPError as exc:
            if exc.code != 404:
                raise
        if attempt < 59:
            time.sleep(20)
    raise SystemExit(f'No published PyPI wheel for {version}')


if __name__ == '__main__':
    main()
