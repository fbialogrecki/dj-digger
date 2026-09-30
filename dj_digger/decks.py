"""Versioned manufacturer audio rules, separate from USB filesystem support.

Each deck carries the playable-file table of its operating instructions for USB
playback. These rules describe documented formats, never a claim of a hardware
test: a file outside a table is called incompatible because the manufacturer
does not list it, even where a unit might play it anyway.
"""
from dataclasses import dataclass

RULE_VERSION = '2026-09-28.2'
_MANUALS = 'https://downloads.support.alphatheta.com/manuals/'
SOURCES = {
    'CDJ-350': 'https://www.manualslib.com/manual/352426',
    'CDJ-850 / 850-K': 'https://www.manualslib.com/manual/260325',
    'CDJ-900': 'https://www.manualslib.com/manual/352441',
    'CDJ-2000': 'https://www.manualslib.com/manual/130120',
    'CDJ-900NXS': 'https://www.manualslib.com/manual/906825',
    'CDJ-2000NXS': 'https://www.manualslib.com/manual/712075',
    'CDJ-2000NXS2': 'https://imagescdn.juno.co.uk/manual/598186-01U.pdf',
    'CDJ-TOUR1': 'https://www.manualslib.com/manual/3564556',
    'CDJ-3000': _MANUALS + 'dj-players/CDJ-3000/CDJ-3000_DRI1586A_manual.pdf',
    'CDJ-3000X': _MANUALS + 'dj-players/CDJ-3000X/CDJ-3000X_DRI1956B_manual.pdf',
    'XDJ-700': 'https://www.manualslib.com/manual/1953042',
    'XDJ-1000': 'http://www.allfordj.ru/upload/iblock/e25/rukovodstvo_polzovatelya_pioneer_xdj_1000_usb.pdf',
    'XDJ-1000MK2': 'https://www.manualslib.com/manual/1203907',
    'XDJ-AERO': 'https://www.manualslib.com/manual/520490/Pioneer-Xdj-Aero.html?page=5',
    'XDJ-RX': _MANUALS + 'all-in-one-dj-systems/XDJ-RX/XDJ-RX_DRI1257_manual.pdf',
    'XDJ-RX2': _MANUALS + 'all-in-one-dj-systems/XDJ-RX2/XDJ-RX2_DRI1479A_manual.pdf',
    'XDJ-RR': _MANUALS + 'all-in-one-dj-systems/XDJ-RR/XDJ-RR_DRI1568B_manual.pdf',
    'XDJ-XZ': _MANUALS + 'all-in-one-dj-systems/XDJ-XZ/XDJ-XZ_DRI1625B_manual.pdf',
    'XDJ-RX3': _MANUALS + 'all-in-one-dj-systems/XDJ-RX3/XDJ-RX3_DRI1702C_manual.pdf',
    'OPUS-QUAD': _MANUALS + 'all-in-one-dj-systems/OPUS-QUAD/OPUS-QUAD_DRI1795D_manual.pdf',
    'XDJ-AZ': _MANUALS + 'all-in-one-dj-systems/XDJ-AZ/XDJ-AZ_DRI1936C_manual_EN.pdf',
    'OMNIS-DUO': _MANUALS + 'all-in-one-dj-systems/OMNIS-DUO/OMNIS_DUO_DRI1882B_manual.pdf',
    'XDJ-AN': _MANUALS + 'all-in-one-dj-systems/XDJ-AN/XDJ-AN_DRI2023A_EN_manual.pdf',
}

# Sampling rates as the manuals list them. MP3 below 32 kHz is MPEG-2 Layer-3.
LOW = (16000, 22050, 24000)
MPEG1 = (32000, 44100, 48000)
STANDARD = (44100, 48000)
HIGH = (44100, 48000, 88200, 96000)
PCM_CODECS = frozenset({'pcm_s16le', 'pcm_s24le', 'pcm_s16be', 'pcm_s24be'})
# USB file systems as the manuals list them; FAT covers FAT16 and FAT32. No deck reads NTFS.
FAT_HFS = frozenset({'fat', 'hfs+'})
WITH_EXFAT = frozenset({'fat', 'hfs+', 'exfat'})
# Every manual: folders deeper than this below the drive's root are not shown.
MAX_FOLDER_LEVELS = 8


@dataclass(frozen=True)
class Deck:
    name: str
    mp3_rates: tuple = MPEG1
    aac_rates: tuple = MPEG1  # AAC LC only; no manual lists HE-AAC.
    pcm_rates: tuple = STANDARD  # WAV and AIFF, and FLAC/ALAC where listed.
    pcm_bits: tuple = (16, 24)
    lossless: frozenset = frozenset()  # 'flac' and/or 'alac'
    # The oldest players list MPEG-4 AAC LC only; a raw ADTS .aac file may be MPEG-2 AAC.
    mpeg2_aac: bool = True
    filesystems: frozenset = FAT_HFS

    def accepts(self, media: dict) -> str:
        codec, rate, channels = media.get('codec'), media.get('rate'), media.get('channels')
        if not codec or not rate or not channels:
            return 'unverified'
        if channels not in (1, 2):
            return 'incompatible'
        bitrate = media.get('bit_rate', 0)
        if codec == 'mp3':
            if rate not in self.mp3_rates:
                return 'incompatible'
            # MPEG-2 Layer-3 runs 8-160 kbps; MPEG-1 Layer-3 32-320 kbps.
            low, high = (8000, 160000) if rate < 32000 else (32000, 320000)
            return 'compatible' if low <= bitrate <= high else 'unverified'
        if codec == 'aac':
            if rate not in self.aac_rates:
                return 'incompatible'
            # ffprobe's codec name alone does not distinguish HE-AAC from LC.
            if media.get('profile') != 'LC' or (media.get('container') == 'aac' and not self.mpeg2_aac):
                return 'unverified'
            return 'compatible' if 16000 <= bitrate <= 320000 else 'unverified'
        container = media.get('container')
        if container == 'aiff' and codec not in ('pcm_s16be', 'pcm_s24be'):
            return 'incompatible'
        if container == 'mov' and codec == 'alac' and media.get('extension', '.m4a') != '.m4a':
            return 'unverified'
        if codec not in PCM_CODECS | self.lossless or media.get('bits') not in self.pcm_bits:
            return 'incompatible'
        return 'compatible' if rate in self.pcm_rates else 'incompatible'


_EARLY = dict(mp3_rates=LOW + MPEG1, aac_rates=LOW + MPEG1)  # 900 onwards and the XDJ players
_HIGH_RES = dict(pcm_rates=HIGH, lossless=frozenset({'flac', 'alac'}))
_CURRENT = dict(mp3_rates=STANDARD, aac_rates=STANDARD)  # CDJ-3000 generation: no 32 kHz lossy
_MPEG4_AAC = dict(mpeg2_aac=False)
_EXFAT = dict(filesystems=WITH_EXFAT)

DECKS = (
    Deck('CDJ-350', **_MPEG4_AAC),
    Deck('CDJ-850 / 850-K', **_MPEG4_AAC),
    Deck('CDJ-900', **_EARLY, **_MPEG4_AAC),
    Deck('CDJ-2000', **_EARLY, **_MPEG4_AAC),
    Deck('CDJ-900NXS', **_EARLY),
    Deck('CDJ-2000NXS', **_EARLY),
    Deck('CDJ-2000NXS2', **_EARLY, **_HIGH_RES),
    Deck('CDJ-TOUR1', **_EARLY, **_HIGH_RES),
    Deck('CDJ-3000', **_CURRENT, **_HIGH_RES),
    Deck('CDJ-3000X', **_CURRENT, **_HIGH_RES, **_EXFAT),
    Deck('XDJ-700', **_EARLY),
    Deck('XDJ-1000', **_EARLY),
    Deck('XDJ-1000MK2', **_EARLY, lossless=frozenset({'flac', 'alac'})),
    Deck('XDJ-AERO', mp3_rates=(44100,), aac_rates=(44100,), pcm_rates=(44100,), pcm_bits=(16,), **_MPEG4_AAC),
    Deck('XDJ-RX', filesystems=frozenset({'fat'})),
    Deck('XDJ-RX2'),
    Deck('XDJ-RR'),
    Deck('XDJ-XZ', lossless=frozenset({'flac'})),
    Deck('XDJ-RX3', lossless=frozenset({'flac'}), **_EXFAT),
    Deck('OPUS-QUAD', **_CURRENT, **_HIGH_RES, **_EXFAT),
    Deck('XDJ-AZ', **_CURRENT, **_HIGH_RES, **_EXFAT),
    Deck('OMNIS-DUO', **_CURRENT, lossless=frozenset({'flac', 'alac'}), **_EXFAT),
    Deck('XDJ-AN', **_CURRENT, lossless=frozenset({'flac', 'alac'}), **_EXFAT),
)

DECK_NAMES = tuple(deck.name for deck in DECKS)


def _groups():
    groups = {}
    for deck in DECKS:
        formats = (deck.mp3_rates, deck.aac_rates, deck.pcm_rates, deck.pcm_bits, deck.lossless, deck.mpeg2_aac)
        groups.setdefault(formats, []).append(deck.name)
    return tuple(tuple(names) for names in groups.values())


# Decks that play exactly the same files, in the fixed order: the choice offers
# these rather than two dozen boxes. File systems may differ inside a group;
# the destination warnings still name the deck.
DECK_GROUPS = _groups()
# Ticked before any choice is saved: the CDJ lineup this app first covered. Every
# deck would include the XDJ-AERO and hold every export to 16 bit / 44.1 kHz.
DEFAULT_DECKS = ('CDJ-350', 'CDJ-850 / 850-K', 'CDJ-2000', 'CDJ-2000NXS', 'CDJ-2000NXS2', 'CDJ-3000', 'CDJ-3000X')


def chosen(names=None) -> tuple[Deck, ...]:
    """The decks a user picked, in the fixed order; none named means every deck."""
    picked = tuple(deck for deck in DECKS if names is None or deck.name in names)
    if not picked:
        raise ValueError('Choose at least one deck')
    return picked


def best_profile(names=None) -> 'Profile':
    """The best export every chosen deck plays, according to its documentation.

    FLAC when every chosen deck reads it (lossless, smaller than PCM, carrying
    tags and artwork), WAV otherwise; the highest bit depth and sampling rate the
    chosen decks share. These are upper limits; nothing is upsampled.
    """

    picked = chosen(names)
    rates = set.intersection(*(set(deck.pcm_rates) for deck in picked))
    bits = set.intersection(*(set(deck.pcm_bits) for deck in picked))
    flac = all('flac' in deck.lossless for deck in picked)
    return Profile('flac' if flac else 'wav', max(bits), max(rates))


def compatibility(media_files, names=None) -> dict[str, str]:
    results = {}
    for deck in chosen(names):
        states = [deck.accepts(media) for media in media_files]
        results[deck.name] = ('incompatible' if 'incompatible' in states else
                              'unverified' if not states or 'unverified' in states else 'compatible')
    return results


@dataclass(frozen=True)
class Profile:
    format: str = 'wav'
    bits: int = 24
    rate: int = 48000

    def __post_init__(self):
        if self.format not in ('wav', 'aiff', 'flac') or self.bits not in (16, 24) or self.rate not in (44100, 48000, 88200, 96000):
            raise ValueError('Unsupported export profile')

    def label(self) -> str:
        return f'{self.format.upper()}, up to {self.bits}-bit / {self.rate / 1000:g} kHz'

    def codec(self, bits=None):
        bits = self.bits if bits is None else bits
        return 'flac' if self.format == 'flac' else f'pcm_s{bits}' + ('be' if self.format == 'aiff' else 'le')

    def media(self, *, bits=None, rate=None):
        return dict(container=self.format, codec=self.codec(bits),
                    bits=self.bits if bits is None else bits,
                    rate=self.rate if rate is None else rate, channels=2)
