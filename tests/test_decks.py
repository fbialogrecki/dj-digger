"""The deck rules against the playable-file tables of the operating instructions."""
from dj_digger.decks import DECK_NAMES, DECKS, DEFAULT_DECKS, SOURCES, compatibility


def verdict(deck, **media):
    return compatibility([dict(dict(channels=2), **media)], [deck])[deck]


def mp3(rate, bit_rate=192000):
    return dict(codec='mp3', rate=rate, bit_rate=bit_rate)


def pcm(codec, rate, bits):
    return dict(codec=codec, rate=rate, bits=bits, container='flac' if codec == 'flac' else 'mov' if codec == 'alac' else 'wav',
                extension='.m4a' if codec == 'alac' else '')


def test_every_rule_names_its_manual_and_the_default_set_exists():
    assert set(SOURCES) == set(DECK_NAMES) and len(DECKS) == len(set(DECK_NAMES))
    assert set(DEFAULT_DECKS) <= set(DECK_NAMES)


def test_mp3_sampling_rates_follow_each_generation():
    # The CDJ-3000 generation lists MP3 at 44.1 and 48 kHz only; older players add 32 kHz.
    for deck in ('CDJ-3000', 'CDJ-3000X', 'OPUS-QUAD', 'XDJ-AZ', 'OMNIS-DUO', 'XDJ-AN'):
        assert verdict(deck, **mp3(32000)) == 'incompatible' and verdict(deck, **mp3(44100)) == 'compatible'
    for deck in ('CDJ-350', 'CDJ-2000NXS2', 'XDJ-1000', 'XDJ-RX3'):
        assert verdict(deck, **mp3(32000)) == 'compatible'
    # MPEG-2 Layer-3 (16-24 kHz, up to 160 kbps) from the CDJ-900 on and on the XDJ players.
    for deck in ('CDJ-900', 'CDJ-2000', 'CDJ-2000NXS2', 'CDJ-TOUR1', 'XDJ-700', 'XDJ-1000MK2'):
        assert verdict(deck, **mp3(22050, 128000)) == 'compatible'
    for deck in ('CDJ-350', 'CDJ-850 / 850-K', 'XDJ-RX2', 'CDJ-3000'):
        assert verdict(deck, **mp3(22050, 128000)) == 'incompatible'
    assert verdict('XDJ-AERO', **mp3(48000)) == 'incompatible'


def test_lossless_support_differs_between_units():
    assert verdict('XDJ-XZ', **pcm('flac', 48000, 24)) == 'compatible'
    assert verdict('XDJ-XZ', **pcm('alac', 48000, 24)) == 'incompatible'  # FLAC only
    assert verdict('XDJ-XZ', **pcm('flac', 96000, 24)) == 'incompatible'
    assert verdict('XDJ-1000MK2', **pcm('alac', 48000, 16)) == 'compatible'
    assert verdict('XDJ-1000MK2', **pcm('flac', 96000, 24)) == 'incompatible'
    assert verdict('OMNIS-DUO', **pcm('flac', 88200, 24)) == 'incompatible'
    assert verdict('OPUS-QUAD', **pcm('alac', 96000, 24)) == 'compatible'
    assert verdict('XDJ-1000', **pcm('flac', 44100, 16)) == 'incompatible'
    assert verdict('XDJ-AERO', **pcm('pcm_s24le', 44100, 24)) == 'incompatible'  # 16-bit, 44.1 kHz only
    assert verdict('XDJ-AERO', **pcm('pcm_s16le', 44100, 16)) == 'compatible'


def test_raw_aac_and_file_systems_follow_the_manuals():
    aac = dict(codec='aac', rate=44100, profile='LC', bit_rate=256000)
    # The oldest players list MPEG-4 AAC LC only; a raw ADTS file may be MPEG-2.
    assert verdict('CDJ-350', container='aac', **aac) == 'unverified'
    assert verdict('CDJ-350', container='mov', **aac) == 'compatible'
    assert verdict('CDJ-2000NXS', container='aac', **aac) == 'compatible'
    by_name = {deck.name: deck for deck in DECKS}
    assert 'exfat' not in by_name['CDJ-3000'].filesystems and 'exfat' in by_name['CDJ-3000X'].filesystems
    assert by_name['XDJ-RX'].filesystems == {'fat'}
    assert all('ntfs' not in deck.filesystems and 'fat' in deck.filesystems for deck in DECKS)
