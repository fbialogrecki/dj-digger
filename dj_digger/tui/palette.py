"""Every key from the keymap as a command in Textual's palette.

Thirty-nine bindings, most of them hidden from the footer, is more than anyone
remembers. The palette (ctrl+p) searches their labels and help text, so a key
you forgot is one fuzzy word away - and it runs the same action the key would.
"""

from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider

from .keymap import KEY_DISPLAY, KEYMAP


class KeymapProvider(Provider):
    """One palette entry per keymap row: "Label  (key)" with its help text."""

    def _entries(self):
        for key, action, label, _group, _show, detail in KEYMAP:
            shown = KEY_DISPLAY.get(key, key)
            yield f"{label}  ({shown})", action, detail

    async def discover(self) -> Hits:
        for display, action, detail in self._entries():
            yield DiscoveryHit(display, partial(self.app.run_action, action), help=detail)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for display, action, detail in self._entries():
            text = f"{display} {detail}"
            score = matcher.match(text)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(display),
                    partial(self.app.run_action, action),
                    help=detail,
                )
