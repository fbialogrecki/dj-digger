"""Read-only contract checks for the Hypeddit pages reported by the user.

Run explicitly with ``uv run pytest -m hypeddit_live tests/test_hypeddit_live.py``.
This module only performs GET requests and parses HTML; it never calls a gate
resolver, submits a profile, performs OAuth, or requests a download.
"""

from urllib.parse import urlparse

import pytest
import requests

from dj_digger import browser
from dj_digger.gates import providers as gates

HYPEDDIT_URLS = (
    "https://hypeddit.com/sinexvsylum/starryeyed",
    "https://hypeddit.com/track/xngfus",
    "https://hypeddit.com/track/zrw7vu",
    "https://hypeddit.com/burnarecords/wilfredosempadance4me",
    "https://hypeddit.com/analyst/mrdutty-1",
    "https://hypeddit.com/poskuk/zhufadedposkbootleg",
    "https://hypeddit.com/l87679",
    "https://hypeddit.com/stave/adeleskyfallstaveedit",
    "https://hypeddit.com/crucast/spicesomilikeitburtcopebootleg",
    "https://hypeddit.com/deepnotion/dimensionwhipslapdeepnotion2023bootleg",
    "https://hypeddit.com/link/ky9i8z",
    "https://hypeddit.com/dondarkoe/simonsays",
    "https://hypeddit.com/crucast/burtcopeouttamyhead",
    "https://hypeddit.com/dnblab/vektralanotherwayfreedownload",
    "https://hypeddit.com/repair/monolith",
    "https://hypeddit.com/t95dreadmc/hushhushftpolabryson",
    "https://hypeddit.com/corruptedmindsofia/gluevip",
    "https://hypeddit.com/bennie/chasestatussubfocusflashinglightsbennierefix",
    "https://hypeddit.com/crucast/burtcopehotsteppa",
    "https://hypeddit.com/yana069",
    "https://hypeddit.com/documentone/coordinatespt2",
    "https://hypeddit.com/circadiansotamilafalls/mesmer",
    "https://hypeddit.com/corruptedmind/dreamscircuits",
    "https://hypeddit.com/dnblab/adammimmadfreedownload",
    "https://hypeddit.com/burnarecords/tweedhandlethis",
    "https://hypeddit.com/corruptedmind/makeba",
    "https://hypeddit.com/tkmozey/breakmyheart",
    "https://hypeddit.com/dnblab/summertimesadnessjasebootleg",
    "https://hypeddit.com/missyelliot/workitpengoflipfreedownload",
    "https://hypeddit.com/partumaudio/fergielondonbridgechestabootlegfreedl",
    "https://hypeddit.com/fezthekid/fezthekidcucumberjunglefreedownload",
    "https://hypeddit.com/yanasos",
    "https://hypeddit.com/crucast/ac13doiwannaknow",
    "https://hypeddit.com/96k5yr",
    "https://hypeddit.com/jimmyb/limpbizkitbreakstuffjimmybbootleg",
    "https://hypeddit.com/hookedsoundsxscalez/hedexfteksmanmhitrscalezbootlegfreedownload",
    "https://hypeddit.com/freaksgeeks/function",
    "https://hypeddit.com/millbrook-blooom-madishu/ready-to-lose",
    "https://hypeddit.com/bennie/gnarlsbarkleycrazybennieremixfreedownload",
    "https://hypeddit.com/xou004",
    "https://hypeddit.com/bennie/tellemboy",
    "https://hypeddit.com/flxa201a",
    "https://hypeddit.com/duxnbass/epitome",
    "https://hypeddit.com/stormzy/vossibopjervisbootleg",
    "https://hypeddit.com/jameshiraeth/bbyxlikethatbootleg",
    "https://hypeddit.com/slinkeednb/slinkeebustout",
    "https://hypeddit.com/kadilak/liftmeup",
    "https://hypeddit.com/flxa208",
    "https://hypeddit.com/dubshotta/ablazewahdemahdo",
    "https://hypeddit.com/brigsy/examplechangedthewayyoukissmebrigsybootleg",
    "https://hypeddit.com/rokiau/idacorrfeddelegrandletmethinkaboutitrkibootleg",
    "https://hypeddit.com/primate/riversideprimatebootleg",
    "https://hypeddit.com/crucast/eliminateweeblewobblevipcircadiansvipofthevip",
    "https://hypeddit.com/povaudio/travisscottbutterflyeffectdaymubootleg",
    "https://hypeddit.com/nextgenaudio/falentin-time",
    "https://hypeddit.com/senditt/inmyheadvipfreedownload",
    "https://hypeddit.com/sublmnl/dance-1",
    "https://hypeddit.com/jamezy/jamezyfendinotfilafreedownload",
    "https://hypeddit.com/sustance/vapourep",
    "https://hypeddit.com/jbookey/whitenoisedub",
)

assert len(HYPEDDIT_URLS) == 60

pytestmark = pytest.mark.hypeddit_live


@pytest.fixture(scope="module")
def hypeddit_session():
    session = requests.Session()
    session.headers.update(browser.REQUEST_HEADERS)
    yield session
    session.close()


@pytest.mark.parametrize("url", HYPEDDIT_URLS)
def test_public_hypeddit_contract_is_still_parseable(url, hypeddit_session):
    response = hypeddit_session.get(url, timeout=(10, 20))
    if response.status_code in {404, 410, 451}:
        return  # An explicitly unavailable page is a supported classification.

    assert response.status_code == 200
    assert (urlparse(response.url).hostname or "").lower() in {
        "hypeddit.com",
        "www.hypeddit.com",
    }
    inspection = gates.inspect_hypeddit_html(response.url, response.text)
    assert inspection.kind in {"gate", "hub", "hybrid", "challenge"}
    if inspection.kind in {"gate", "hybrid"}:
        assert inspection.manifest is not None
        assert inspection.manifest.steps
        assert inspection.manifest.file_id


def _live_inspection(url, session):
    response = session.get(url, timeout=(10, 20))
    assert response.status_code == 200
    return gates.inspect_hypeddit_html(response.url, response.text)


def test_known_gate_has_a_manifest(hypeddit_session):
    inspection = _live_inspection(HYPEDDIT_URLS[0], hypeddit_session)
    assert inspection.kind in {"gate", "hybrid"}
    assert inspection.manifest is not None
    assert inspection.manifest.steps
    assert inspection.manifest.file_id


def test_duxnbass_is_a_shop_hub_despite_the_global_captcha_asset(hypeddit_session):
    inspection = _live_inspection(
        "https://hypeddit.com/duxnbass/epitome", hypeddit_session
    )
    assert inspection.kind == "hub"
    assert {gates.store_for_url(url) for url, _label in inspection.shops} >= {
        "bandcamp",
        "beatport",
    }


def test_ky9i8z_keeps_its_nested_gate_and_purchase_stores(hypeddit_session):
    inspection = _live_inspection(
        "https://hypeddit.com/link/ky9i8z", hypeddit_session
    )
    assert inspection.kind == "hub"
    assert inspection.nested_gates
    assert {gates.store_for_url(url) for url, _label in inspection.shops} >= {
        "bandcamp",
        "beatport",
    }
