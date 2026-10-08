# Spike F0: procedura i wyniki (R1–R6)

- Status: **procedura gotowa, wyniki do uzupełnienia przez właściciela**
- Plan: `docs/implementation/browser-extension-plan.md`, §6 „F0”, ryzyka §9 R1–R6
- Narzędzie: `scripts/webext_spike.py` (stdlib, wyrzucane po spike'u)
- Czas: około godziny, jeśli R1 i R5 zrobisz jednym przebiegiem (patrz „Plan godziny”)
- Kto uruchamia: **tylko właściciel**, w swojej zalogowanej przeglądarce na Fedorze.
- Zakres (decyzja 2026-10-06): **tylko Chrome**. Kolumny i kroki dla Firefoksa są odłożone
  (Firefox: selftest ALL PASS, ładowanie przez `web-ext run` działa; reszta nietestowana). Agent niczego z tego nie uruchamia (AGENTS.md: żadnych żywych, zapisujących
  przepływów bez wyraźnej prośby).

Wyniki wklej do tabel w tym pliku (albo odeślij je w czacie w tej samej kolejności). **Nie wklejaj**
wartości ciasteczek, tokenów, podpisanych URL-i pobrań (zapisz sam host, kształt ścieżki
i nazwy parametrów), adresów e-mail ani nazw i identyfikatorów konta.

## Plan godziny

| Min | Co | Sekcje |
|---|---|---|
| 0–10 | rejestracja, ładowanie rozszerzenia w obu przeglądarkach, `hello`, `selftest.html` | §0 |
| 10–15 | Native Messaging: procesy hosta, lokalizacje manifestów Firefoksa | R3 |
| 15–35 | jedna gate'owa ścieżka w Chrome i w Firefoksie: syntetyczne kliknięcia i pobranie | R1 + R5 |
| 35–45 | okno robocze: `normal` kontra `minimized` | R2 |
| 45–55 | Beatport: `probe-store` plus DevTools ręcznie, fixtures | R4 |
| 55–60 | Bandcamp: `probe-store` plus ścieżki checkoutu | R6 |

## Co skrypt robi, a czego nie

- Trzyma `InstanceLock` (jak `cli.py`), więc **zamknij GUI i inne polecenia `dj-digger`** przed
  uruchomieniem. Startuje `link.Listener`, czeka na `hello` rozszerzenia i drukuje każde polecenie,
  odpowiedź i zdarzenie z czasem (`HH:MM:SS.mmm`).
- Opcje globalne (`--browser chrome|firefox`, `--wait`, `--keep`) stoją **przed** podpolecenie.
  `--browser` wybiera połączenie po polu `browser` z `hello` (przy dwóch załadowanych
  rozszerzeniach inaczej wybrane jest ostatnio aktywne).
- Okno robocze jest domyślnie **ukryte** (`window.open` bez `visible`); `--visible` je pokazuje.
  Na wyjściu (`quit` albo Ctrl+C) okno jest zamykane, chyba że podasz `--keep`. Wtedy
  `window show|hide` i `close` działają na pozostałym oknie z kolejnego uruchomienia.
- `probe-store` **nigdy nie klika** (koszyk to faza F5/F6, przetestujesz go przez aplikację).
  Czyta: `tab.html` (rozmiar, `truncated`), liczniki `dom.count` dla kontrolek kupna/koszyka,
  kotwice `/track/<slug>/<id>`, bloki `ld+json`, `__NEXT_DATA__` (parsowane lokalnie) oraz wynik
  prawdziwego `store_parse.products_from_html` dla tej strony. Strona nigdy nie jest drukowana
  w całości. `--save PATH` zapisuje HTML (tryb 0600, adresy e-mail zamienione na
  `user@example.invalid`; reszta danych konta zostaje do ręcznego wyczyszczenia).
- `probe-gate` prowadzi bramkę Hypeddit tymi samymi selektorami co `gates/browser.py` i przed
  **każdym** kliknięciem pyta `[y/N]` (Enter = nie). Pole e-mail wypełnia tylko z jawnym
  `--email` (i dalej po `y`). Przy każdej akcji z efektem na prawdziwym koncie dopisuje to w pytaniu.
  Po ostatnim kroku zostaje w trybie obserwacji zdarzeń.
- W trybie obserwacji (`open`, `probe-*`) wpisz: `show`, `hide`, `info`, `timers`, `quit`.
  Zdarzenia po kliknięciu mają dopisek `(+X.XXs after the last click)`.
- Nic nie drukuje ciasteczek: o tokenie SoundCloud mówi tylko, czy istnieje.

## 0. Przygotowanie

### 0.1 Rejestracja hosta i kopia rozszerzenia

```bash
cd ~/Projects/dj-soundcloud-digger
uv sync --extra dev
ls -l .venv/bin/dj-digger-host          # musi istnieć: to jest host, który uruchomią przeglądarki
uv run dj-digger browser register
```

Co dzieje się w checkoucie deweloperskim (`register.host_executable()`): `path` w manifeście hosta to
**bezwzględna ścieżka** do `dj-digger-host` leżącego obok `sys.executable`, czyli
`<repo>/.venv/bin/dj-digger-host` (skrypt z `[project.scripts]`, tworzony przez `uv sync`; shebang
wskazuje `.venv/bin/python`). Dlatego:

- po `uv sync` ma być `.venv/bin/dj-digger-host`; jeśli go nie ma, `register` kończy się błędem
  „Cannot find the dj-digger-host executable”,
- nie usuwaj ani nie przenoś `.venv` po rejestracji (po odtworzeniu `.venv` uruchom `register`
  ponownie),
- `register` pisze manifesty tylko dla przeglądarek, których katalog profilu istnieje (uruchom
  przeglądarkę choć raz) i wypisuje `Registered <przeglądarka>: <ścieżka>` oraz
  `Load the unpacked extension from: <katalog>`. Domyślnie to
  `~/.local/share/dj-digger/browser-extension/` (albo pod `$XDG_DATA_HOME`),
- Firefox dostaje manifesty w **obu** katalogach (`~/.mozilla/native-messaging-hosts` i
  `~/.config/mozilla/native-messaging-hosts`; R3 sprawdza, który działa),
- przeglądarki z Flatpaka/Snapa są tylko zgłaszane (`Note: ... not supported`), nic się dla nich nie zapisuje.

Sprawdź manifest hosta (Chrome):

```bash
cat ~/.config/google-chrome/NativeMessagingHosts/com.github.fbialogrecki.djdigger.json
# "path": ".../.venv/bin/dj-digger-host", "allowed_origins": ["chrome-extension://klkgfigjpjkhjdiddhdadbkoeclhejni/"]
```

**Pułapka:** kopia w `data_dir()/browser-extension/` jest odświeżana tylko, gdy `version` w
`manifest.json` repozytorium jest inna niż w kopii. Po zmianie plików rozszerzenia w repo bez podbicia
wersji usuń kopię, żeby `register` skopiował ją od nowa:
`rm -r ~/.local/share/dj-digger/browser-extension && uv run dj-digger browser register`.
Odwrotnie: Twoje ręczne edycje kopii (R2) przeżyją kolejne `register`.

### 0.2 Chrome (RPM)

1. `chrome://extensions` → włącz **Developer mode** → **Load unpacked** → wskaż
   `~/.local/share/dj-digger/browser-extension/`.
2. Sprawdź, że ID to `klkgfigjpjkhjdiddhdadbkoeclhejni` (stałe dzięki `key`). Inne ID oznacza,
   że host odmówi połączenia.
3. Po instalacji otwiera się `welcome.html`. Gdy nie, otwórz
   `chrome-extension://klkgfigjpjkhjdiddhdadbkoeclhejni/welcome.html`.

### 0.3 Firefox (RPM)

1. `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → wskaż
   `~/.local/share/dj-digger/browser-extension/manifest.json`. (Dodatek tymczasowy znika po
   restarcie Firefoksa; ładuj go ponownie. Ostrzeżenia o nieznanych kluczach `key` lub
   `background.service_worker` są oczekiwane i niegroźne.)
2. **Uprawnienia hostów:** `about:addons` → dj-digger → Permissions (albo przycisk
   „Grant site access” w `welcome.html`). Dodatek tymczasowy może mieć je domyślnie odebrane.
3. `welcome.html` otwiera się przy instalacji; później: `about:addons` → dj-digger → Preferences
   albo `about:debugging` → Inspect → Internal UUID → `moz-extension://<uuid>/welcome.html`.

### 0.4 `welcome.html` i `hello`

`welcome.html` pokazuje „Desktop app link” i „Site access”. Znaczenie komunikatów:

| Komunikat | Znaczy |
|---|---|
| `connected, waiting for the app` | host wystartował; aplikacja (spike) jeszcze nie działa. To dobry stan |
| `not connected (Specified native messaging host not found.)` (Firefox: `No such native application ...`) | brak manifestu hosta dla tej przeglądarki (zły katalog, brak `register`) |
| `not connected (Access to the specified native messaging host is forbidden.)` | ID rozszerzenia nie zgadza się z `allowed_origins` / `allowed_extensions` |
| `not connected (Native host has exited.)` albo `Error when communicating...` | host nie wystartował: zła ścieżka, brak `.venv`, brak uprawnień wykonania |
| `Site access: missing` | brak uprawnień hostów (Firefox: patrz 0.3) |

Test kanału (zamknij GUI; przy obu przeglądarkach podaj `--browser`):

```bash
uv run python scripts/webext_spike.py --browser chrome hello
uv run python scripts/webext_spike.py --browser firefox hello
```

Oczekiwane: `connected to chrome <wersja>`, linia `hello: {...}` z `protocol: 1`, `permissions: true`
i `SoundCloud oauth_token present in this browser: True|False`. Czas od startu skryptu do `connected`
zanotuj (host próbuje co 1 s).

### 0.5 Self-test `dom.js` (macierz W11)

W obu przeglądarkach otwórz stronę rozszerzenia (to wyłącznie lokalna strona, nie wysyła komend):

- Chrome: `chrome-extension://klkgfigjpjkhjdiddhdadbkoeclhejni/selftest.html`
- Firefox: `moz-extension://<Internal UUID z about:debugging>/selftest.html`

Na dole ma być `ALL PASS`. Jeśli `N FAILED`, skopiuj linie `FAIL ...`.

| Krok | Chrome | Firefox | Uwagi |
|---|---|---|---|
| `register` zapisał manifest (ścieżka) | | | |
| `welcome.html`: link / site access | | | |
| `hello` (wersja przeglądarki, `protocol`, `permissions`) | | | |
| Czas do `connected` | | | |
| Token SoundCloud w przeglądarce (tak/nie) | | | |
| `selftest.html` | | | |

## Jak czytać wyniki (gdzie patrzeć)

1. **Terminal ze skryptem:** polecenia, odpowiedzi, zdarzenia (`tab.created`, `download.*`) z czasem.
2. **Konsola service workera (Chrome):** `chrome://extensions` → dj-digger → link **service worker**
   (Inspect views). Trzymanie otwartego DevTools utrzymuje worker przy życiu.
   **Konsola dodatku (Firefox):** `about:debugging#/runtime/this-firefox` → dj-digger → **Inspect**.
   (Chrome może poprosić o wpisanie `allow pasting` przed wklejeniem kodu.)
3. **Pobrania:** `chrome://downloads` (najechanie na nazwę albo „Show in folder”; adres źródłowy
   widać po najechaniu na link pod nazwą) albo `about:downloads` w Firefoksie.
4. **Sieć w karcie bramki:** DevTools → Network w karcie z `--visible` (F12; „Preserve log”).
5. **Zdarzenia pobrań niewidoczne dla rozszerzenia.** `background.js` emituje `download.created`
   tylko dla pobrań, które potrafi przypisać (odpowiedź `webRequest` z własnej karty z tym samym
   URL-em). Pobranie z hosta spoza `host_permissions` nie da **żadnego** zdarzenia ani logu, więc
   skrypt go nie zobaczy. Dlatego w R5 wklej do konsoli service workera:

   ```js
   // Chrome: `chrome`, Firefox: zamień na `browser`. Działa do restartu workera (DevTools go trzyma).
   (chrome.downloads.onCreated.addListener(i => console.log('DL created', new Date().toISOString(),
     { host: new URL(i.url).host, finalHost: new URL(i.finalUrl || i.url).host,
       referrer: i.referrer, danger: i.danger, mime: i.mime, filename: i.filename })),
    chrome.downloads.onChanged.addListener(d => console.log('DL changed', JSON.stringify(d))),
    chrome.webRequest.onResponseStarted.addListener(d => console.log('RS', d.tabId, d.type, d.statusCode,
     new URL(d.url).host), { urls: ['<all_urls>'] }),
    'listening')
   ```

   `RS` pokazuje tylko hosty, do których rozszerzenie ma uprawnienie. Jeśli `DL created` ma hosta,
   którego nie ma wśród linii `RS`, to ten host trzeba dodać (patrz R5 i „Decyzje po spike'u”).

## R1: syntetyczne zdarzenia na Hypeddit

**Pytanie:** czy `dom.click` (sekwencja `pointerdown`, `mousedown`, `pointerup`, `mouseup`, `click`
z `isTrusted=false`) przeprowadza bramkę (follow, like, Next, Connect, Download) i czy `a.undone`
z `target=_blank` otwiera popup, czy blokuje go popup blocker?

**Wybór bramki:** publiczna bramka Hypeddit, o którą nie dbasz (jak najwięcej kroków click-through,
najlepiej bez kroku e-mail na początek). Do kroku e-mail użyj **jednorazowego adresu**; adres
trafia do Hypeddit.

**Uwaga o skutkach:** kroki follow/like i Connect wykonują się na Twoich prawdziwych kontach
(D9). Skrypt pyta o każdy klik, możesz zatrzymać się przed nimi odpowiedzią `n`.

```bash
# Chrome, okno widoczne: widać pasek/ikonę zablokowanego popupu
uv run python scripts/webext_spike.py --browser chrome probe-gate --visible 'https://hypeddit.com/<gate>'
# potem to samo bez --visible (ukryty przebieg, jak w F4)
uv run python scripts/webext_spike.py --browser chrome probe-gate 'https://hypeddit.com/<gate>'
# Firefox
uv run python scripts/webext_spike.py --browser firefox probe-gate --visible 'https://hypeddit.com/<gate>'
# (do kroku e-mail, gdy chcesz go sprawdzić:)  ... probe-gate --email twoj.jednorazowy@example.com [--name Imie] <url>
```

Obserwuj i zapisz:

- po starcie: `start button: count=.. visible=..`, potem linie `SLIDE {...}` (kind, pending, next,
  connect, email, ...). Czy `kind` to znany typ (`sc`, `sp`, `ig`, `email`, `dw`...)?
- po kliknięciu `a.undone` (follow/like): czy pojawia się `EVENT {"event":"tab.created",...}` i po
  jakim czasie (`+X.XXs`), czy w Chrome jest ikona „Pop-up blocked” w pasku adresu, a w Firefoksie żółty
  pasek „prevented this site from opening a pop-up window”,
- czy po Next linia `slide moved on after Xs`, czy `slide did NOT move on` (wtedy ręcznie sprawdź,
  czego brakuje: zostało `a.undone`? Hypeddit nie uznał zdarzenia?),
- DevTools (F12 w karcie `--visible`): zaznacz `a.undone` i sprawdź `$0.target`, `$0.href`
  (zapisz tylko host), czy ma handler `click` wywołujący `window.open`,
- krok Connect: czy powstaje popup do dostawcy (`tab.created` z `url` zredukowanym do hosta), czy wraca
  sam i czy slajd się przesuwa,
- dla porównania: ten sam krok **ręcznie** (prawdziwy klik) w innej bramce. Jeśli ręcznie popup się
  otwiera, a syntetycznie nie, przyczyną jest brak aktywacji użytkownika (R1-fallback).

| Obserwacja | Chrome widoczne | Chrome ukryte | Firefox widoczne | Uwagi |
|---|---|---|---|---|
| `#downloadProcess` kliknięty, slajdy widoczne | | | | |
| kinds slajdów po kolei | | | | |
| `a.undone` → `tab.created` (tak/nie, po ile s) | | | | |
| popup zablokowany (ikona/pasek)? | | | | |
| Next → slajd przesuwa się (s) | | | | |
| Connect → popup, powrót, slajd | | | | |
| e-mail: pole wypełnione, `change` zadziałał, slajd | | | | |
| `dw`/`#gateDownloadButton` widoczny, klik → pobranie | | | | |
| błędy `dom.click` (`denied`, `timeout`...) | | | | |

**Kryterium (plan §6):** R1 działa albo ma potwierdzone obejście (R1-fallback z §9).

## R2: okno robocze `normal` kontra `minimized`

**Pytanie:** czy gate działa w zminimalizowanym oknie (throttling timerów, `visibilityState`) i czy
menedżer okien na Fedorze (GNOME/KDE) kradnie fokus przy `windows.create({focused:false})`?

W `background.js` jest `HIDDEN_STATE = "normal"` (okno w tle, niezfocusowane). Wariant `minimized`
przełączasz w **kopii** rozszerzenia (poniżej) i przeładowujesz rozszerzenie (Chrome:
`chrome://extensions` → ikona odświeżenia; Firefox: `about:debugging` → Reload):

```bash
EXT="${XDG_DATA_HOME:-$HOME/.local/share}/dj-digger/browser-extension/background.js"
sed -i 's/^const HIDDEN_STATE = "normal"/const HIDDEN_STATE = "minimized"/' "$EXT"   # wariant minimized
sed -i 's/^const HIDDEN_STATE = "minimized"/const HIDDEN_STATE = "normal"/' "$EXT"   # powrót
grep -n '^const HIDDEN_STATE' "$EXT"
```

Dla **każdego** wariantu i przeglądarki:

```bash
uv run python scripts/webext_spike.py --browser chrome open 'https://hypeddit.com/<gate>'
# w trybie obserwacji:
spike> timers        # 5 prób; 2.0x s = timery bez throttlingu, 3.x s = throttlowane (okno ukryte)
# poczekaj 30 s
spike> timers
spike> show          # okno wychodzi na wierzch
spike> timers
spike> hide
spike> quit
```

W trakcie pisz coś w innym oknie: czy nowe okno **kradnie fokus** przy `window.open` i przy `show`/`hide`,
czy okno widać na pasku zadań/w przełączniku (GNOME Activities, Alt+Tab), czy w ogóle się pojawiło
(Wayland vs X11, KDE vs GNOME: zapisz sesję `echo $XDG_SESSION_TYPE $XDG_CURRENT_DESKTOP`).
Potem `probe-gate` (bez `--visible`) na bramce z R1 i porównaj czasy `slide moved on after Xs` między
`normal` a `minimized`.

| Obserwacja | Chrome `normal` | Chrome `minimized` | Firefox `normal` | Firefox `minimized` |
|---|---|---|---|---|
| sesja / pulpit | | | | |
| okno kradnie fokus przy `window.open` (tak/nie) | | | | |
| okno widoczne w Alt+Tab/pasku (tak/nie) | | | | |
| `timers` ukryte (s) | | | | |
| `timers` po 30 s (s) | | | | |
| `timers` po `show` (s) | | | | |
| `show` / `hide` działają i fokus | | | | |
| `probe-gate` ukryty: czasy kroków i czy przechodzi | | | | |
| Firefox: czy honoruje `focused:false` | – | – | | |

## R3: Native Messaging na Fedorze

**Pytanie:** czy host działa w obu przeglądarkach z RPM, czy background żyje, czy host kończy się po
zamknięciu przeglądarki, które katalogi manifestu Firefoksa działają (147+)?

```bash
firefox --version; google-chrome --version
flatpak list 2>/dev/null | grep -i -E 'firefox|chrome|chromium'; snap list 2>/dev/null | grep -i -E 'firefox|chromium'
pgrep -af dj-digger-host                 # kiedy przeglądarka z rozszerzeniem jest otwarta: po jednym na przeglądarkę
```

1. **Utrzymanie background:** `uv run python scripts/webext_spike.py --browser chrome open <url>`; zostaw
   **5+ minut** (service worker bez otwartego DevTools), potem w trybie obserwacji `info`. Odpowiedź =
   worker żyje (port natywny go trzyma). Powtórz w Firefoksie. Zanotuj, czy `hello`/`instance`
   się zmienił (komunikat `session ended: browser extension restarted` oznacza restart).
2. **Host po zamknięciu przeglądarki:** zamknij Chrome (całkiem), po chwili `pgrep -af dj-digger-host`:
   procesu tej przeglądarki ma nie być w ciągu kilku sekund. To samo dla Firefoksa (host kończy się na
   EOF ze stdin).
3. **Host przeżywa restart aplikacji:** zakończ skrypt (Ctrl+C) i uruchom ponownie; host ma połączyć się
   sam (≤ 3 s) i dalej wysłać `hello`.
4. **Firefox, katalog manifestu:** sprawdź, który działa (jeden po drugim):

   ```bash
   mv ~/.mozilla/native-messaging-hosts/com.github.fbialogrecki.djdigger.json{,.off}
   # przeładuj dodatek i uruchom: ... --browser firefox hello   (działa z samym ~/.config/mozilla/...?)
   mv ~/.mozilla/native-messaging-hosts/com.github.fbialogrecki.djdigger.json{.off,}
   mv ~/.config/mozilla/native-messaging-hosts/com.github.fbialogrecki.djdigger.json{,.off}
   # powtórz hello, potem przywróć plik
   mv ~/.config/mozilla/native-messaging-hosts/com.github.fbialogrecki.djdigger.json{.off,}
   ```
5. **Wyrejestrowanie (przedtest W14):** `uv run dj-digger browser unregister`, potem `welcome.html`
   ma pokazać `Specified native messaging host not found`; `uv run dj-digger browser register` przywraca.

| Obserwacja | Chrome | Firefox | Uwagi |
|---|---|---|---|
| wersja przeglądarki, RPM/Flatpak/Snap | | | |
| `hello` bez dodatkowych kroków | | | |
| background żyje po 5+ min | | | |
| host kończy się po zamknięciu przeglądarki (s) | | | |
| host łączy się po restarcie skryptu (s) | | | |
| działa `~/.mozilla/native-messaging-hosts` | – | | |
| działa `~/.config/mozilla/native-messaging-hosts` | – | | |
| po `unregister` komunikat | | | |

## R4: Beatport

**Pytania:** jak działa „Add to cart” na stronie tracka (i czy wymaga logowania), jak wygląda
`beatport.com/cart` i licznik koszyka, jakie ścieżki checkoutu trafią do `forbiddenPaths`, czy strona
release'u (bez 403) ma ID tracków w `ld+json`/kotwicach czy tylko w `__NEXT_DATA__`, oraz fixtures do
`tests/fixtures/beatport/`.

Wymaga **zalogowanego** Beatporta w przeglądarce. **Nie klikamy niczego w sklepie ze skryptu.** Działanie
`dom.click`/`dom.fill` na komponentach React w sklepie jest mutacją koszyka, więc sprawdzimy je w F6
przez aplikację (w F0 `selftest.html` pokrywa `fill` na kontrolowanym inpucie React-like).

### 4.1 `probe-store` (tylko odczyt)

```bash
uv run python scripts/webext_spike.py --browser chrome probe-store --visible \
   'https://www.beatport.com/track/<slug>/<id>' --save /tmp/bp-track.html
uv run python scripts/webext_spike.py --browser chrome probe-store --visible \
   'https://www.beatport.com/release/<slug>/<id>' --save /tmp/bp-release.html
# (opcjonalnie to samo w Firefoksie i w jednym wyloguj-wariancie: prywatne okno nie ma rozszerzenia,
#  więc o wylogowaniu mówi licznik "log in / sign in links")
```

W wyniku patrz na:

- `anchors /track/<slug>/<id>`: ile unikalnych ID, czy ID z release'u pokrywają się z tym, co widzisz na stronie,
- `ld+json blocks` i ich `@type`; `__NEXT_DATA__` (długość, **track-like lists** ze ścieżką JSON i próbką id/nazwy),
- `app parser (products_from_html)`: czy aplikacja dziś znajduje produkty; `refused: SecurityChallengeBlocked`
  oznacza challenge/antybot (zanotuj, kiedy: tylko przy zautomatyzowanym ładowaniu?),
- `buttons on the page` i `cart-ish`: kandydaci na „Add to cart” (tekst, `aria-label`, `data-testid`),
- `log in / sign in links`: >0 sugeruje, że przeglądarka jest wylogowana,
- status `tab.goto` (403?).

### 4.2 Ręcznie w DevTools (zalogowany, własne konto)

1. Strona tracka → F12 → zaznacz kontrolkę „Add to cart”. Zapisz: znacznik, nazwę dostępną
   (zakładka Accessibility), `aria-label`/`data-testid`/stałe klasy (pomiń hasze CSS), czy jest w
   `<form>`, jak wygląda stan po dodaniu (zmiana etykiety?). Zapisz to jako **kandydat selektora**
   (np. `button[data-testid="..."]` albo `role=button name=/add to cart/i`).
2. Karta Network (Fetch/XHR) → **własnoręcznie** dodaj ten jeden track do koszyka i zanotuj
   metodę i ścieżkę żądania (bez nagłówków, ciasteczek i treści). Potem usuń track z koszyka.
3. `https://www.beatport.com/cart`: struktura wiersza (kotwica do `/track/<slug>/<id>`? atrybut z ID?),
   licznik koszyka w nagłówku (selektor), tekst pustego koszyka.
4. **Checkout (nie płać):** kliknij przycisk przejścia do płatności tylko po to, by odczytać adres
   pierwszego ekranu: zapisz **host i ścieżkę** (oraz czy to inny host), cofnij się, niczego nie wpisuj.
   To są ścieżki do `forbiddenPaths` (dziś zgadnięte: `/checkout`, `/cart/checkout`, `/payment`).

### 4.3 Fixtures do `tests/fixtures/beatport/`

Pliki: `track_page.html`, `release_page.html`, `cart_empty.html`, `cart_with_item.html` (zapisz
z DevTools: Elements → prawy przycisk na `<html>` → Copy → Copy outerHTML, albo `--save`).
**Zanim cokolwiek trafi do repo:** pliki < 2 MB (`MAX_HTML_BYTES`), usuń dane konta i sesji:

```bash
grep -n -i -E 'token|csrf|session|customer|account|@|<twoje imię>|<twój login>|<id konta>' plik.html | cut -c1-160
```

- imię, nazwisko, login, adres e-mail, id konta i klienta (także w `__NEXT_DATA__`: pola `user`,
  `session`, `customer`, `csrf`, `token`),
- nagłówki/ciasteczka (nie powinno ich być w HTML; sprawdź `<meta>` i skrypty inline),
- zostaw tylko to, co jest potrzebne parserom i selektorom: ID, tytuły, ceny, struktura koszyka.

Zatwierdzenia fixtures dokonaj dopiero po przejrzeniu różnicy (`git diff`/`git add -p`).

| Pytanie | Wynik | Uwagi |
|---|---|---|
| Add to cart: selektor / nazwa dostępna / `data-testid` | | |
| Wymaga logowania? Co dzieje się wylogowanym | | |
| Żądanie dodania (metoda, ścieżka) | | |
| Koszyk: selektor wiersza i ID tracka | | |
| Licznik koszyka: selektor | | |
| Pusty koszyk: tekst/selektor | | |
| Checkout: host + ścieżka pierwszego ekranu | | |
| Release: ID tracków w kotwicach `/track/<slug>/<id>`? | | |
| Release: ID w `ld+json`? | | |
| Release: ID tylko w `__NEXT_DATA__` (ścieżka JSON) | | |
| `products_from_html` na release'ie: liczba produktów | | |
| Challenge/403 (kiedy) | | |
| Fixtures zapisane i oczyszczone (lista plików) | | |

## R5: pobranie z Hypeddit: przypisanie `webRequest` ↔ `downloads`

**Pytania:** czy dopasowanie `webRequest.onResponseStarted` (`tabId`) do `downloads.onCreated`
(`url`/`finalUrl`) działa w obu przeglądarkach, jaki jest faktyczny `referrer`, jaki **host** (i łańcuch
przekierowań) ma plik, jak zachowują się „pytaj, gdzie zapisać”, `danger` dla `.zip` i limit wielu pobrań
w Chrome. Hosty pobrań trzeba znać, bo `webRequest` widzi tylko `host_permissions`; plik na innym CDN może
nigdy nie zostać przypisany.

Przygotowanie: otwórz konsolę service workera (sekcja „Jak czytać wyniki”) i wklej fragment z punktu 5.
W karcie bramki (okno `--visible`) otwórz F12 → Network → „Preserve log” **przed** kliknięciem pobrania.

```bash
uv run python scripts/webext_spike.py --browser chrome probe-gate --visible 'https://hypeddit.com/<gate>'
# (dalej odpowiadaj y aż do kroku "dw" / download; to ten sam przebieg co w R1, więc R1 i R5 robisz razem)
uv run python scripts/webext_spike.py --browser firefox probe-gate --visible 'https://hypeddit.com/<gate>'
```

Obserwuj:

- w skrypcie: po kliknięciu pobrania `EVENT download.created` (jest/nie ma), `download.done`
  (`path`, `bytes`, `mime`), `download.blocked` (powód), `download.failed`,
- w konsoli workera: `DL created` (host, finalHost, referrer, danger, mime) i linie `RS` z hostem pliku,
- w Network: żądanie z `Content-Disposition: attachment`: **host, łańcuch statusów (302 → 200),
  hosty z `Location`**, typ,
- w `chrome://downloads`/`about:downloads`: źródło pliku, ostrzeżenia (np. „dangerous file”).

**Interpretacja:** plik jest, a `download.created` nie ma → pobranie nieprzypisane (host spoza
`host_permissions` albo `tabId` niezgodny); porównaj `DL created.host` z hostami w liniach `RS`.

Dodatkowe przypadki (każdy w Chrome i Firefoksie):

1. **Pytaj, gdzie zapisać:** Chrome `chrome://settings/downloads` → „Ask where to save each file before
   downloading”; Firefox `about:preferences` → „Always ask you where to save files”. Powtórz pobranie.
   Oczekiwane: `download.blocked` z `save_as_dialog` po ok. 5 s (heurystyka w `background.js`). Przywróć ustawienie.
2. **`danger` dla `.zip`/innych typów:** zapisz `danger` i `mime` z `DL created` dla paczki `.zip` oraz
   pliku audio.
3. **Wiele pobrań (Chrome):** uruchom `probe-gate` na drugiej bramce w ciągu minuty od pierwszej i zobacz,
   czy Chrome pokazuje „hypeddit.com wants to download multiple files”; zapisz, czy `download.created`
   przychodzi mimo monitu.
4. Pliki z tych prób usuń ręcznie z folderu pobrań (spike nie woła `download.discard`; to F4).

| Obserwacja | Chrome | Firefox | Uwagi |
|---|---|---|---|
| host pliku (`DL created.host`/`finalHost`) | | | |
| łańcuch przekierowań (statusy, hosty) | | | |
| host jest w `host_permissions`? (linia `RS` z tym hostem) | | | |
| `download.created` przyszło (tak/nie) | | | |
| `referrer` (wartość, sam host) | | | |
| `tabId` w `RS` zgadza się z kartą bramki | | | |
| `download.done` (path, bytes, mime) | | | |
| `danger` `.zip` / audio | | | |
| „pytaj, gdzie zapisać”: zdarzenie/czas | | | |
| wiele pobrań: monit/zdarzenie | | | |

**Kryterium (plan §6):** R5 działa (przypisanie po `tabId`) albo hosty CDN dopisane i potwierdzone.

## R6: Bandcamp: tylko odczyt plus ścieżki checkoutu

Dodawanie do koszyka przez aplikację to testy F5; w F0 tylko `probe-store` i ręczne DevTools.

```bash
uv run python scripts/webext_spike.py --browser chrome probe-store --visible \
   'https://<artysta>.bandcamp.com/track/<slug>' --save /tmp/bc-track.html
uv run python scripts/webext_spike.py --browser firefox probe-store 'https://<artysta>.bandcamp.com/track/<slug>'
```

Patrz na: liczniki `buy control (button|link|text)`, `price input`, `add to cart (...)`,
`sidecart`, `sidecart rows`, `cart link`, `cart counter` (te same selektory co `stores/bandcamp.py`);
wyniki `dom.call bandcampTralbum` i `bandcampSuggestedPrice` (MAIN world, tylko odczyt: dane powinny
zgadzać się ze stroną), `app parser`.

Ręcznie w DevTools (zalogowany, własne konto):

1. Dla strony z „Buy Digital Track”: czy kontrolka to przycisk, link czy tekst; czy po kliknięciu **ręcznym**
   pojawia się formularz ceny (`userPrice`) i przycisk „Add to cart”.
2. Dodaj ręcznie jedną pozycję i obejrzyj `#sidecart` oraz `bandcamp.com/cart` (ten sam układ co fixtures
   w `tests/fixtures/bandcamp/`?); usuń pozycję.
3. **Checkout (nie płać):** kliknij „Check out”/„Pay” tylko po adres pierwszego ekranu: zapisz **host i
   ścieżkę** oraz przekierowania. Dziś w `forbiddenPaths` zgadnięte: `/checkout`, `/cart/checkout`, `/payment`.

| Pytanie | Chrome | Firefox | Uwagi |
|---|---|---|---|
| `probe-store`: buy control / price input / add to cart (liczniki) | | | |
| `sidecart`, wiersze, licznik koszyka (liczniki) | | | |
| `bandcampTralbum` / `bandcampSuggestedPrice` (MAIN world działa?) | | | |
| `app parser`: produkty | | | |
| zachowanie ręczne: kontrolka, formularz ceny, add to cart | | | |
| układ side cartu zgodny z fixtures? | | | |
| checkout: host + ścieżka pierwszego ekranu | | | |
| challenge/403 | | | |

## Decyzje po spike'u

| Wynik | Co zmienia |
|---|---|
| R2: który wariant działa lepiej | `HIDDEN_STATE` w `background.js` (`normal` albo `minimized`), opis D3 w planie; jeśli żaden nie działa (timery stoją, fokus ucieka), ukryty przebieg odpada i F4 pracuje tylko w oknie widocznym |
| R1: syntetyczne zdarzenia przechodzą bramkę i popup się otwiera | brak zmian; F4 jak w planie |
| R1: popup blokowany, ale bramka przechodzi / nie przechodzi | R1-fallback (§9): wrapper `window.open` w MAIN world + `tabs.create({openerTabId})`; w ostateczności `chrome.debugger` tylko w Chromium, Firefox przez widoczny przebieg |
| R5: host pliku spoza `host_permissions` | dopisać hosty CDN do `host_permissions` (**nie** do grup `allowlist.json`, jeśli nawigacja i DOM na CDN nie mają być dozwolone; wtedy test zgodności allowlisty z manifestem trzeba rozszerzyć o osobną listę hostów pobrań) |
| R5: przypisanie po `tabId` zawodzi w Firefoksie | inna reguła przypisania w `background.js` (np. `referrer` plus jedno pobranie naraz) albo wyłączenie ukrytego przebiegu w Firefoksie |
| R5: „pytaj, gdzie zapisać” / `danger` / wiele pobrań | potwierdzić albo poprawić `download.blocked` (5 s, `save_as_dialog`), dopisać do komunikatów F7 i do §13.2 spec |
| R3: który katalog Firefoksa działa | `register.py`: jeden albo oba, notatka w README/§13.2; gdy worker usypia, skrócić `alarms` (`periodInMinutes`) |
| R3: Flatpak/Snap wykryte | komunikat w dialogu „Połącz przeglądarkę” (F7) |
| R4: selektor „Add to cart”, licznik, wiersz koszyka | selektory i weryfikacja w `stores/beatport.py` (F6) |
| R4: ścieżki checkoutu (host + ścieżka) | `forbiddenPaths` Beatporta w `allowlist.json` (i test zgodności) |
| R4: ID tracków tylko w `__NEXT_DATA__` | parsowanie `__NEXT_DATA__` w `store_parse.py` (ścieżka JSON z `track-like lists`); inaczej bez zmian |
| R4: wymóg logowania/challenge | F6 odroczone; fallback playlista/Soundiiz (R4 w §9) |
| R4: fixtures | `tests/fixtures/beatport/*` (oczyszczone) dla testów F6 |
| R6: ścieżki checkoutu Bandcamp | `forbiddenPaths` Bandcampa w `allowlist.json` |
| R6: selektory/układ side cartu inne niż fixtures | poprawki w `stores/bandcamp.py` (F5) i fixtures |
| `bandcampTralbum` w MAIN world nie działa w którejś przeglądarce | zmiana `dom.call` / wariant dla tej przeglądarki (F2) |
| W11: `FAIL` w `selftest.html` | poprawka `dom.js` przed F2 |

**Kryterium przejścia F0 (plan §6):** R1, R5 i R6 działają albo mają potwierdzone obejście; w przeciwnym razie
wracamy do decyzji (D1–D5).

## Wyniki: podsumowanie

| Ryzyko | Wynik (OK / obejście / problem) | Decyzja |
|---|---|---|
| R1 | **OK.** Na bramce `1rjvpc` syntetyczne kliknięcia przechodzą cały gate: `a.undone` odhacza kroki (3→0), Next przesuwa slajd od razu, Download pobiera plik. Popupy SoundCloud otwierają się (`tab.created` z `openerTabId`) i **same odtwarzają muzykę**. Spotify Connect nie był jeszcze sprawdzony. | F4 bez zmian. Wszystkie karty rozszerzenia są wyciszane (`mute`), a popupy ukrytej sesji minimalizowane (e370bc1). |
| R2 | **Problem na GNOME/Wayland, obejście R2b działa.** `focused:false` nie ukrywa okna, a `state:"minimized"` (przy tworzeniu i ponownie przez `windows.update`) jest ignorowane: okno robocze i popupy są widoczne przez cały przebieg. To znane ograniczenie (Chromium ignoruje `state` przy `windows.create` na Linuksie, xdg-shell nie potwierdza minimalizacji). **R2b (2026-10-07):** gate w ramce dokumentu offscreen (`chrome.offscreen`, powód `IFRAME_SCRIPTING`), skrypt wstrzyknięty przez `scripting.registerContentScripts({allFrames})`: `visibilityState: visible`, timer 1000 ms na 1000 ms, `hasStorageAccess() = true` (sesje użytkownika), gate `se1faf` i `1rjvpc` przechodzą do pobrania bez żadnego okna; plik z `hypeddit-gates-prod.s3.amazonaws.com`, `referrer: https://hypeddit.com/`. Hypeddit i Bandcamp nie wysyłają `X-Frame-Options`/`frame-ancestors`; Beatport `deny`, SoundCloud `SAMEORIGIN`. Nie sprawdzono: gate z popupami follow i Spotify Connect w ramce, krok e-mail. Kod próbny nie jest w repo (łamie zasadę jednego kanału wejścia: `runtime.onMessage`). | Do decyzji właściciela: ukryty przebieg Hypeddit przez offscreen (zmiana granicy bezpieczeństwa: kanał content script → background tylko z ramki dokumentu offscreen, uprawnienie `offscreen`), albo karta w tle, albo przebieg widoczny. |
| R3 | **OK (Chromium RPM).** Host rejestruje się, `hello` w kilka sekund, uprawnienia nadane, odczyt tokenu SoundCloud działa. Zakończenie hosta po zamknięciu przeglądarki jeszcze niesprawdzone. Firefox odłożony (decyzja 8). | bez zmian |
| R4 | **OK.** Release (`/release/<slug>/<id>`): wiersze `[data-testid="tracks-table-row"]`, anchor `/track/<slug>/<id>`, przycisk `button[aria-label^="Add track"]` z `aria-label` np. `Add track 'SPACESHIP' (Original Mix) by Niereich to cart. Price: $1.49`; obok `Choose a cart for this item`. `products_from_html` wyciąga ID z anchorów (`ld+json` brak, `__NEXT_DATA__` niepotrzebne). Track (`/track/<slug>/<id>`): główny przycisk `Add track …` poza `tracks-table-row`/`tracks-list-item` (tam są rekomendacje). **Kliknięcie** `dom.click` z rozszerzenia dodaje do koszyka (zalogowany): licznik `a[data-testid="header-mainnav-link-cart"]` z `aria-label` `Open cart, 0 items` → `Open cart, 1 items` po ~2 s, bez dialogu. **Koszyk:** `/cart` przekierowuje na `/cart/cart/<cartId>` (wylogowany: `/?next=/cart`); pozycje i rekomendacje to `[data-testid="tracks-list-item"]`, pozycja koszyka ma kontrolkę `Move to cart`, rekomendacja `Add track … to cart`/`Choose a cart for this item`; `button[data-testid="checkout-button"]`. Trasy Next.js: `/checkout/[description]/[id]`, `/checkout/paypal/success`, `/checkout/thank-you`, `/account/billing`, `/subscriptions/billing`. | F6: selektory jak obok, weryfikacja licznikiem, potem strona koszyka; `forbiddenPaths` Beatporta: `/checkout` (już jest) plus `/account/billing`, `/subscriptions/billing`. |
| R5 | **OK po poprawce.** Plik idzie z CDN spoza `host_permissions`, więc `webRequest` go nie widzi i pobranie nie było przypisane (aplikacja by czekała). Poprawka: przypisanie po `referrer` do jedynej własnej karty na tym samym dozwolonym origin (e370bc1). Potem `download.created` po 1,2–1,7 s i `download.done` po 2,8–3 s, `danger: safe`, `audio/mpeg`. Pierwsze wejście po pobraniu przekierowuje na `/hot-or-not/…` (aplikacja już to obsługuje). | `host_permissions` bez zmian (bez dopisywania CDN). |
| R6 | **OK.** Prawdziwa ścieżka `CartBrowserSession.run_batch` przez rozszerzenie: `Folcoch - Blow` (EUR 1.99) `added`, „verified at stage count”; `Analect - Two Steps Back` (GBP 1.50) dodany, przy ponownym przebiegu `already_in_cart`. Side cart `#item_list .item` jak w fixtures. Jeden raz (pierwszy przebieg po teście offscreen) `Cart navigation failed … PageError` dla `https://bandcamp.com/cart` w obu próbach; nie powtórzyło się. | bez zmian w F5; obserwować PageError (W6) |
| W11 (`selftest.html`) | **OK** w Chrome i Firefoksie (ALL PASS). | bez zmian |

**F4b na żywo (2026-10-07 11:34, Chromium 154, GNOME/Wayland):** partia 3 bramek przez
`download_hypeddit_batch_in_browser` (`zcgy54`, `poumticafarfacid/unspoken-1`,
`hellbound/ghostvoices`; kroki SoundCloud i Instagram) przeszła w ~60 s w sesji offscreen: 3/3
pliki (MP3 320 kbps, WAV 16 bit, MP3 320 kbps) skopiowane do folderu docelowego, każdy pod swoim
wierszem (trzy karty na `hypeddit.com` naraz, przypisanie przez `download.expect`), w Pobranych nic
nie zostało (`download.discard`).

**F4b po poprawkach (2026-10-07 11:52–12:07):** partie 3, 2, 2 i 2 bramek (świeże:
`gsdl00`, `markgreene/whentheysleepwedance`, `stretch/inthissilence`,
`shibuya793/nightcrawlerremix`, `palouk/…`, `pragmaticuk/…`) — wszystkie pobrane, każda bramka we
własnym dokumencie offscreen. Właściciel potwierdził: bez okna i bez karty; przy 12:02 jeszcze
krótko grała muzyka z ostatniego etapu bramki (Hypeddit odtwarza kolejne utwory po Download), po
uszczelnieniu `frame-main.js` (12:07) cisza.

**F10 sesja „w tle” koszyka (2026-10-08, Chromium 154, GNOME/Wayland, tylko odczyt):**
`window.open {background:true}` daje wirtualne `windowId`; dwie karty `tab.new` powstały jako
nieaktywne karty w istniejącym oknie użytkownika (bez nowego okna, bez zmiany aktywnej karty),
`window.close` zamknął tylko je. `bandcamp.com/cart` przekierowuje na stronę ostatniej pozycji
koszyka z otwartym side-cartem. **R11 (throttling nieaktywnej karty):** sonda `waitVisible` 2000 ms
trwa 2,2–2,5 s zaraz po otwarciu i stale ~2,97–3,00 s przez 6,6 min (pomiary co 30 s) — timery są
wyrównywane do 1 s, intensywnego throttlingu po 5 min (raz na minutę) nie ma. Wniosek: koszyk
w nieaktywnych kartach zostaje, każde czekanie może się przedłużyć o ≤ 1 s; offscreen dla
Bandcampa niepotrzebny.

**F10 na żywo (2026-10-08, zgoda właściciela, profil: Filip / własny e-mail):**
- Bramka `low-e/clubbizarrehardedit` (e-mail, SoundCloud, Instagram, Spotify) przeszła w tle w 37 s:
  bez okna i bez pytania „Gates need you”; Spotify Connect przez nieaktywną kartę logowania
  (callback Hypeddit idzie przez `localStorage`, ramka offscreen go odbiera); WAV 86 MB w folderze
  docelowym, w Pobranych nic.
- Koszyk (Bandcamp 3, Beatport 3 + 1 release), 63 s, bez okna i bez akceptacji: Bandcamp 2 dodane
  po cenie minimalnej, 1 „no longer available”; Beatport: release `aetherborne` dopasowany do
  „Subterrain” i dodany. **Znaleziony błąd:** nagłówek Beatportu pokazuje „0 items” do czasu
  wczytania koszyka konta (0,6–1,4 s po załadowaniu), klik przed tym nic nie robi, a skok licznika
  0 → 2 był brany za dodanie (fałszywe „added”). Poprawka: licznik ustalony przez 2 s przed klikiem
  i w sprawdzaniu koszyka; po niej „Got Me” dodany naprawdę (2 → 3, wiersz w koszyku).
  „The Rave” był już kupiony na tym koncie: Beatport pyta „repurchase it again?” — teraz Cancel
  i wynik „bought on this Beatport account already”.

**ToneDen (2026-10-08, tylko odczyt: GET stron, API i publicznego JS `social.js` + chunków
`st.toneden.io/1.279/javascripts/*`, bez logowania i bez kliknięć akcji):**
- Strona `www.toneden.io/<artist>/post/<slug>` to SPA (React), `X-Frame-Options: ALLOWALL`
  (ramka offscreen działa), `toneden.io/…` przekierowuje na `www.`. Dane: `GET api/v1/posts?
  author_username=&link_path=` (`post.unlock`: `platform` soundcloud/spotify, `unlock_type`,
  `download_display_url`; brak URL-a pliku), potem `GET api/v1/attachments/<id>/gateMetadata`
  (`is_gate_open`, `viewer_id`). Brak posta: 404 (API i strona).
- Przebieg: duży przycisk `a.post-gate-btn` (tekst z posta) → krok „unlock”: przyciski akcji
  `.gate-btn-box a.btn` (`.gate-first-init` / `-load` / `-fin`), duży przycisk `.disabled` do
  otwarcia bramki. Otwarta bramka: duży przycisk robi `window.open(api/v1/attachments/<id>/download
  [?jwt=])` — plik w nowej karcie (bez sesji: 401 HTML).
- Akcja: fan nieznany ToneDen → `users.auth.external(platform)` → popup
  `www.toneden.io/auth/<platform>?fanOptOut&is_opted_in&redirectPostID&userType=fan&spotlightTrackID
  &clientAuth=true` (w ramce także `embed=true&redirectURL=document.referrer`) → 302 do dostawcy
  (`soundcloud.com/connect?…redirect_uri=https://www.toneden.io/auth/soundcloud/callback`,
  Spotify analogicznie) → callback. Strona czeka na `postMessage({type:"auth_response"})` od
  popupu (sprawdza `origin`) i co 250 ms na `popup.closed` (zamknięty = „popup blocker”, błąd
  i `alert()`). Dla SoundCloud przycisk zawsze idzie przez logowanie (`onUnlockClick.bind(null,
  true)`), dla Spotify fan już zalogowany robi `POST api/v1/attachments/<id>/unlock` bez popupu.
  Mobilny wariant tej samej akcji to zwykłe przekierowanie (`target="_top"`) na ten sam
  `/auth/…?spotlightTrackID=` z powrotem na post — stąd wniosek, że serwer odblokowuje bramkę już
  w callbacku (odpowiedź niesie `spotlightTrackStatus`). **Niepotwierdzone na żywo.**
- Wniosek: w ramce offscreen karta logowania nie ma `opener`, więc strona nie dostanie
  `postMessage`; aplikacja czeka, aż karta wróci na ToneDen (inna ścieżka niż start
  `/auth/<platform>`), zamyka ją i przeładowuje ramkę (serwer zna już fana). Stub
  `window.open` na ToneDen musi być „otwarty”, a ramka `no-referrer` (inaczej `redirectURL` =
  origin rozszerzenia).

Środowisko: Fedora 44, GNOME / Wayland, Chromium 154 (RPM), Firefox (tylko W11), data 2026-10-06.
