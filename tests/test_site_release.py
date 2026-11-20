import pytest

from scripts.site_release import stable_version


def test_website_uses_only_a_complete_stable_release():
    names = ['SHA256SUMS']
    for name in ('windows-x64-test.exe', 'macos-arm64-test.dmg', 'macos-x86_64-test.dmg'):
        base = f'dj-digger-1.6.0-{name}'
        names.extend([base, base + '.sha256'])
    release = dict(tag_name='v1.6.0', draft=False, prerelease=False,
                   assets=[dict(name=n, size=42, state='uploaded') for n in names])
    assert stable_version(release) == '1.6.0'
    for change in ({'prerelease': True}, {'draft': True}, {'tag_name': 'main'}, {'assets': []}):
        with pytest.raises(ValueError):
            stable_version(release | change)
