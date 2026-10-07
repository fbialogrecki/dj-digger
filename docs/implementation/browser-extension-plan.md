# Plan: rozszerzenie przeglądarki zamiast Playwright/Chromium

- Status: **wdrożone na branchu F0–F9 i F4b**; **F10 (praca bez okien, doprecyzowanie właściciela
  2026-10-07) wdrożone** (sesja w tle koszyka sprawdzona na żywo; akceptacja W4–W7, W15 na żywo
  czeka). Akceptacja na żywo (§8) po F10.
  Poniżej plan z rewizji 2; opisy „dziś” w §1 dotyczą stanu sprzed zmiany. Zachowanie opisuje
  `PROJECT-SPECIFICATION.md`.
- Branch: `feat/browser-extension` (od `main` @ `1fe6060`, wersja 1.3.0); wydanie docelowe 1.4.0
- Tryb wdrożenia: bezpośrednia zamiana, bez flag i bez równoległego backendu Playwright.
  Branch merguje się dopiero, gdy przejdzie pełna macierz testów z §8.
- Źródła: `PROJECT-SPECIFICATION.md` §3.6, §6.1, §9, §10.2–10.4, §13.2; kod wymieniony w §1.
- Historia: rew. 3 (2026-10-07) dodaje zasadę „bez okien” (Z1–Z6), F10 i poprawia D3, D7,
  W4–W7. rew. 1 to pierwszy szkic. Rew. 2 dodaje poprawki z przeglądu (K1–K6, W1–W15,
  drobne): semantykę czekania, natywny async, zakaz pickle, przypisywanie pobrań, wspólną
  allowlistę, jawny opis akcji na prawdziwych kontach, stan service workera i inne.

## 0. Cel i zakres

**Cel:** każdą automatyzację przeglądarki wykonuje rozszerzenie (Chrome/Chromium/Edge/Brave
oraz Firefox) zainstalowane w zwykłej przeglądarce użytkownika, a nie Playwright z pobranym
Chromium. Dzięki temu automat działa na prawdziwych sesjach użytkownika (Spotify, Beatport,
Bandcamp, SoundCloud), z prawdziwym odciskiem przeglądarki i bez binarki ~150 MB.

**Efekt dla użytkownika (doprecyzowanie właściciela 2026-10-07, nadrzędne wobec reszty planu):**

- **Gate'y:** użytkownik zaznacza gate'y w aplikacji, a program przechodzi je w tle (e-mail,
  imię, losowy komentarz, kroki social). Użytkownik widzi **wyłącznie** pobieranie utworów
  i ich status w aplikacji — żadnego okna, karty ani popupu przeglądarki.
- **Koszyki Bandcamp i Beatport:** dodawanie dzieje się w tle; wynikiem jest otwarta karta
  przeglądarki z koszykiem zawierającym wszystkie dodane pozycje oraz powiadomienie
  w aplikacji o utworach, których nie znaleziono albo które nie miały linku do zakupu,
  z pytaniem, czy otworzyć te linki w przeglądarce.
- **Niepowodzenia:** gdy gate nie dał pliku, aplikacja o tym mówi i daje szansę ponowić
  automatycznie albo przejść go w przeglądarce — zawsze na wyraźne polecenie użytkownika.
- Rozszerzenie jest narzędziem do tego efektu. Porównanie alternatyw (headless Chrome,
  Qt WebEngine, czyste HTTP) z 2026-10-07: tylko rozszerzenie łączy prawdziwe sesje
  użytkownika, niski profil antybotowy i koszyk Bandcamp w przeglądarce użytkownika
  (koszyk Bandcamp żyje w ciasteczkach przeglądarki, nie na koncie; Beatport — na koncie).

**W zakresie:**

1. Kanał aplikacja ↔ rozszerzenie (Native Messaging z przekaźnikiem, tylko stdlib).
2. Rozszerzenie MV3 z jednym kodem dla Chrome i Firefoksa.
3. Przeniesienie wszystkich czterech obecnych użyć Playwrighta (§1).
4. **Nowość:** koszyk Beatport, czyli zakup wszystkiego naraz bez Soundiiz dla dokładnych tracków.
5. Usunięcie Playwrighta, Chromium, profili zarządzanych i ich UI. Do tego aktualizacja
   paczkowania, specyfikacji i dokumentacji.

**Poza zakresem:**

- Safari.
- Przeglądarki z Flatpaka i Snapa (patrz R3).
- Karty incognito i kontenery Firefoksa.
- Checkout i płatności (zostają ręczne jak dziś).
- Wyszukiwanie Beatport po artyście i tytule (świadomie nigdy, D7).
- Automatyczne testy E2E rozszerzenia (D8).

Publikacja w sklepach **nie** blokuje merge, ale jest otwartą decyzją przed wydaniem 1.4.0
(§10, W11).

## 1. Inwentarz: co dziś używa Playwrighta

| # | Użycie | Kod | Tryb | Co robi |
|---|---|---|---|---|
| U1 | Logowanie SoundCloud | `auth.login_with_browser` (wcześniej `login_with_chromium`), `_wait_for_oauth_cookie`; wywołania: `services/accounts.py:35`, `cli.py:399` | sync, headed, profil `soundcloud-browser` | otwiera `/signin` i czyta ciasteczko `oauth_token` |
| U2 | Gate'y Hypeddit (fallback) | `gates/browser.py` (814 linii); wywołanie: `services/downloads.py:finish_gates` | sync, najpierw ukryty przebieg, potem widoczny; generatory round-robin; profil `soundcloud-browser` | przechodzi slajdy kroków, obsługuje popupy, łapie pobrania |
| U3 | Koszyk Bandcamp | `services/purchases.py:CartBrowserSession`, `stores/bandcamp.py` | async, headless, profil `store-browser`; osobny widoczny „viewer” z kopią ciasteczek | preflight, rewalidacja, kliknięcie, 3-stopniowa weryfikacja (`asyncio.wait_for` na etap), diagnostyka, finisz ręczny, logowanie w Ustawieniach |
| U4 | Lookup Beatport (release → track) | ten sam `CartBrowserSession` + `_page_products_async` → `store_parse.products_from_html` | async, headless | odczyt strony; 403 lub challenge daje wpis playlisty po metadanych |

Elementy pomocnicze do usunięcia lub zmiany: `browser_session.py` (cały plik),
`automation_errors.ChromiumMissing`, `files.save_browser_download` (interfejs `download.save_as`),
`gui/backend.py:with_chromium` i `action_store_login`, `gui/runtime_smoke.py`,
`gui/__init__.py` (`PLAYWRIGHT_BROWSERS_PATH`), `packaging/desktop.spec`,
`packaging/{windows,macos}/build.py` (instalacja Chromium do bundla), zależność `playwright`
w `pyproject.toml`, markery `bandcamp_dom` i `shop_mutate`.

### Używany fragment API Playwrighta i semantyka, którą trzeba zachować

`goto(wait_until="domcontentloaded")`, `content`, `evaluate` (dwa wywołania: `TralbumData`
i sugerowana cena Bandcamp), `reload`, `locator(css)` z `.first`, `.nth()` i `.count()`,
`get_by_role(role, name=re)`, `get_by_text`, `get_by_placeholder`, `click(force)`, `fill`,
`input_value`, `inner_text`, `get_attribute`, `is_visible`, `wait_for(state=visible)`,
`wait_for_timeout`, `bring_to_front`, `screenshot`, `context.pages`, `page.opener`, zdarzenia
`popup`, `page`, `download`, `close`, `response`, `console` i `crash`, `cookies()`,
`clear_cookies(name=)` oraz `download.save_as` i `suggested_filename`.

Zachowania, na których kod **polega**, choć nie widać ich w wywołaniach:

- **Czekanie na element:** `click`, `fill`, `inner_text`, `get_attribute`, `input_value`
  i `wait_for` czekają do timeoutu, aż element istnieje. Akcje dodatkowo czekają, aż element
  będzie widoczny i włączony. `count` i `is_visible` odpowiadają natychmiast.
- **Zapytania po roli pomijają ukryte elementy** (`get_by_role` domyślnie bez `include_hidden`).
  `get_by_text` ich nie filtruje.
- **`click(force=True)`** pomija sprawdzanie zasłaniania, ale nadal przewija element do widoku
  i wysyła prawdziwą sekwencję zdarzeń myszy w środek elementu.
- **`fill`** działa także z kontrolowanymi inputami Reacta.
- **Anulowanie w async** (`asyncio.wait_for`) przerywa wywołanie protokołu, a nie zostawia go
  w tle.

## 2. Docelowa architektura

```
┌──────────────────────┐   multiprocessing.connection    ┌──────────────────────┐  Native Messaging  ┌─────────────────────────────┐
│ dj-digger (GUI/CLI)  │◄──── (AF_UNIX / named pipe, ───►│ dj-digger-host       │◄── stdin/stdout ──►│ Rozszerzenie (background)   │
│ webext.link.Listener │   send_bytes/recv_bytes + JSON, │ przekaźnik, ~100 lin.│     (JSON, 4B len) │ allowlista, własne karty,   │
│ webext.page.* (sync  │   wzajemne uwierzytelnienie     │ spawnowany przez     │                    │ downloads, cookies,         │
│  i natywny async)    │   authkey z pliku 0600)         │ przeglądarkę         │                    │ scripting → dom.js          │
└──────────────────────┘                                 └──────────────────────┘                    └─────────────────────────────┘
```

### Decyzje

- **D1. Cienki sterownik, logika zostaje w Pythonie.** Rozszerzenie wystawia małe API
  w kształcie strony (nawigacja, lokatory, klik, wypełnienie, odczyt, zdarzenia). Python
  dostaje adapter `ExtensionPage`/`ExtensionContext` z tymi samymi nazwami metod, których
  dziś używa (§1), **i z tą samą semantyką** (czekanie, ukryte elementy, zdarzenia myszy,
  anulowanie). Powody:
  - Około 2,5 tys. linii logiki (slajdy gate'ów, dopasowanie produktów, rewalidacja,
    weryfikacja koszyka, circuit breaker) już działa i jest otestowane.
  - Testy (`test_gates.py`, `test_cart.py`) używają zwykłych fałszywych stron i kontekstów.

  Czekanie na element realizuje rozszerzenie **wewnątrz jednej komendy**, żeby nie mnożyć
  przejść przez IPC. Bezpieczeństwo zapewnia §4, a nie „gruby” content script.
- **D2. Native Messaging plus przekaźnik zamiast localhost.**
  - Strona Pythona to tylko stdlib: Native Messaging (4 bajty długości i JSON) oraz
    `multiprocessing.connection` (AF_UNIX na Linux/macOS, named pipe na Windows).
  - **Używamy wyłącznie `send_bytes`/`recv_bytes` z JSON-em, nigdy `send`/`recv`**, bo te
    serializują przez pickle, co przy wycieku klucza oznacza zdalne wykonanie kodu.
  - `Client(authkey=…)` i `Listener(authkey=…)` uwierzytelniają się wzajemnie (wyzwanie
    w obie strony), więc proces podszywający się pod nazwę pipe'a bez klucza nie przejdzie.
  - Brak otwartego portu, brak parowania tokenem w UI, a strony WWW nie mają dostępu do
    kanału.
  - Otwarty port natywny utrzymuje background przy życiu w Chrome (od 105) i w Firefoksie
    (event page nie jest usypiany, dopóki port jest otwarty).
  - Host jest spawnowany przez przeglądarkę, co 1 s próbuje połączyć się z aplikacją
    i **kończy działanie na EOF ze stdin**, czyli gdy przeglądarka zamyka port.
  - **Sesja jest przypięta do połączenia.** `ExtensionContext` pamięta id połączenia, na
    którym powstał. Gdy połączeń jest kilka (kilka przeglądarek), „ostatnio aktywna” wybierana
    jest tylko dla **nowych** sesji. Zerwanie połączenia kończy sesję błędem i nie przełącza
    się na inną przeglądarkę.
- **D3. Praca bez okien (rew. 3; zastępuje „jedno okno robocze”).** Spike R2 pokazał, że na
  GNOME/Wayland okna rozszerzenia nie da się ukryć (`focused:false` i `minimized` są
  ignorowane), więc **automat nigdy nie tworzy okna sam z siebie** (Z1):
  - gate'y Hypeddit: dokument offscreen (F4b), bez okna i bez karty;
  - koszyki: nieaktywne karty w istniejącym oknie przeglądarki użytkownika (sesja „w tle”,
    F10); na końcu karta koszyka staje się aktywna — to jedyny automatyczny, widoczny efekt;
  - okno robocze (widoczne) powstaje tylko na wyraźne polecenie z aplikacji („Otwórz
    w przeglądarce”) oraz dla logowania SoundCloud, gdy przeglądarka jest wylogowana (W3);
  - Firefox (brak `chrome.offscreen`): ukryte okno robocze jak dotąd — znane ograniczenie
    na Wayland, Firefox odłożony (decyzja 8).
  - Znika ponowne uruchamianie profilu, kopiowanie ciasteczek do „viewera”, retry blokady
    profilu i podmiana user agenta.
- **D4. Brak profili zarządzanych.**
  - Bandcamp i Beatport korzystają z sesji użytkownika.
  - Logowanie SoundCloud to odczyt `oauth_token` z przeglądarki, wywoływany wyłącznie
    z akcji „Zaloguj”. Jeśli ciasteczka nie ma, otwieramy `soundcloud.com/signin` w oknie
    roboczym i czekamy jak dziś.
  - Stare katalogi `store-browser/` i `soundcloud-browser/` **zostają na dysku nietknięte**,
    bo AGENTS.md zabrania usuwania profili. README podaje, jak usunąć je ręcznie.
- **D5. Pobrania przez `downloads` API, przypisane przez `tabId`, nie przez `referrer`.**
  - Chrome nie podaje `tabId` w `DownloadItem`, a `referrer` przy domyślnej polityce
    `strict-origin-when-cross-origin` i pobraniu z CDN to samo `https://hypeddit.com/`.
    Przypisanie po `referrer` mogłoby zapisać plik pod złym trackiem.
  - Przypisanie: `webRequest.onResponseStarted` (ma `tabId`, `requestId`, `url`) dla odpowiedzi
    z własnych kart lub popupów jest dopasowywane do `DownloadItem.url`/`finalUrl`
    z `downloads.onCreated`.
  - Dodatkowo w danej chwili trwa najwyżej jedno pobranie gate'u. Adapter blokuje
    `_click_gate_download` na innej karcie, dopóki poprzednie pobranie nie dostanie
    `download.created` albo nie minie 15 s.
  - Pobranie, którego nie da się jednoznacznie przypisać, **nie jest przypisywane**: wiersz
    dostaje błąd do ręcznej obsługi, plik zostaje w Pobranych.
  - Plik ląduje tam, gdzie przeglądarka zapisuje pobrania. Aplikacja przyjmuje
    `download.done` tylko dla `downloadId` wcześniej zgłoszonego i przypisanego. Sprawdza, że
    ścieżka to zwykły plik (nie symlink), kopiuje go przez `files._keep_download` (limit
    rozmiaru, typ, atomowa publikacja), a potem prosi rozszerzenie o `downloads.removeFile`
    i `downloads.erase`.
  - Pobranie oznaczone przez przeglądarkę jako niebezpieczne lub nietypowe (`danger` ≠ `safe`)
    albo czekające na dialog „gdzie zapisać” pokazuje okno robocze z komunikatem i nigdy nie
    jest akceptowane automatycznie.
- **D6. Jeden kod rozszerzenia, jeden `manifest.json`** z `background.service_worker`
  (Chrome) i `background.scripts` (Firefox). Chrome 121+ ignoruje `scripts`, Firefox 121+
  ignoruje `service_worker`. Minimalne wersje: `minimum_chrome_version: "121"`,
  `browser_specific_settings.gecko.strict_min_version: "128.0"` (`world: "MAIN"`
  w `scripting`). Bez bundlera, bez npm i bez TypeScriptu.
- **D7. Beatport: dopasowanie wyłącznie z kodu strony, bez wyszukiwania po nazwie.**
  - Link `/track/<slug>/<id>` jest dokładny od razu, bez otwierania strony (jak dziś
    `_split_direct_beatport`).
  - Link do release'u: otwieramy stronę w przeglądarce użytkownika i szukamy docelowego tracka
    w jej kodzie (`store_parse.products_from_html`: `ld+json` i anchory `/track/`;
    `store_match.match_product`: dokładny tytuł i wersja). Jeśli R4 pokaże, że ID tracków są
    tylko w `__NEXT_DATA__`, dochodzi ich parsowanie w tym samym miejscu.
  - Dokładne dopasowanie trafia do **prawdziwego koszyka** z taką samą rewalidacją
    i weryfikacją jak Bandcamp.
  - Gdy brak dokładnego dopasowania w kodzie strony, użytkownik jest niezalogowany albo pojawi
    się challenge, pozycja trafia do powiadomienia „nie dodano” z opcją otwarcia jej linku
    w przeglądarce (rew. 3, Z5). Playlista i Soundiiz zostają jako opcja dodatkowa.
  - **Nigdy** nie szukamy na Beatporcie po artyście i tytule.
- **D8. Playwright znika całkowicie, także z testów.**
  - Python testujemy offline jak dziś, z fałszywym rozszerzeniem po stronie protokołu (§7).
  - JS rozszerzenia testujemy ręcznie: checklista (§8) plus **strona `selftest.html`
    w rozszerzeniu** z syntetycznymi przypadkami dla `dom.js`, uruchamiana w obu
    przeglądarkach (W15).
  - Markery `bandcamp_dom` i `shop_mutate` oraz ich testy znikają; nagrane fixtures zostają
    dla testów offline.
- **D9. Akcje na prawdziwych kontach: bez dodatkowej zgody (decyzja właściciela, §10).**
  - W prawdziwej przeglądarce krok Connect (Spotify, Deezer, Apple Music, Threads) przejdzie
    sam, jeśli użytkownik kiedyś autoryzował aplikację Hypeddit. Hypeddit wykona wtedy
    follow/save na głównym koncie użytkownika.
  - Zachowanie zostaje jak dziś: przy `gate_social_actions=True` (domyślnie) driver klika
    Connect i czeka na powrót popupu. Przy `False` gate idzie do przeglądarki bez klikania,
    jak dziś (spec §9.2). Brak nowego ustawienia i brak dodatkowego pytania.
  - Skutek trzeba jawnie opisać: w spec §9.2, w opisie przełącznika `gate_social_actions`
    w Ustawieniach („akcje wykonują się na kontach zalogowanych w Twojej przeglądarce”),
    w README i w CHANGELOG 1.4.0.
- **D10. Rozszerzenie instalowane ze stałego katalogu (W10).** Aplikacja kopiuje
  `webext/extension/` do `data_dir()/browser-extension/` (przy starcie, gdy wersja się
  różni). Dialog „Połącz przeglądarkę” wskazuje ten katalog. Ścieżka przeżywa reinstalację
  pakietu i zmianę venv. Po aktualizacji kopii niezgodny `protocol` w `hello` daje komunikat
  „przeładuj rozszerzenie”.

### Nowe i zmienione pliki

| Plik | Rola |
|---|---|
| `dj_digger/webext/__init__.py` | puste |
| `dj_digger/webext/link.py` | `Listener` w aplikacji (wątek akceptujący, wiele połączeń, `hello`, request/response z `id`, zdarzenia per sesja), `ExtensionMissing(AutomationError)`, adres gniazda i `authkey`; tylko `send_bytes`/`recv_bytes` |
| `dj_digger/webext/host.py` | `main()` przekaźnika: framing NM ↔ `Client`, pętla oczekiwania na aplikację, wyjście na EOF ze stdin, limit 1 MB w stronę przeglądarki |
| `dj_digger/webext/register.py` | rejestracja i wyrejestrowanie manifestów hosta (Chrome, Chromium, Brave, Edge, Firefox; Linux, macOS, Windows przez `winreg`); kopia rozszerzenia do `data_dir()` (D10) |
| `dj_digger/webext/page.py` | `ExtensionContext`, `ExtensionPage`, `Locator`, `Download` w wersji sync oraz natywnej async (D1, K2) |
| `dj_digger/webext/extension/manifest.json` | MV3, uprawnienia z §4, minimalne wersje z D6 |
| `dj_digger/webext/extension/allowlist.json` | **jedno źródło prawdy** hostów i zabronionych ścieżek (§4); czytane przez `background.js` i przez test Pythona |
| `dj_digger/webext/extension/background.js` | port natywny, dispatcher, allowlista, rejestr własnych kart i okna w `storage.session`, kolejka komend per karta, zdarzenia tabs, webNavigation, webRequest i downloads |
| `dj_digger/webext/extension/dom.js` | funkcje wstrzykiwane przez `scripting.executeScript`: silnik lokatorów z czekaniem, klik (pełna sekwencja zdarzeń), fill (natywny setter), `dom.all`, odczyty; nazwane funkcje MAIN-world (`bandcampTralbum`, `bandcampSuggestedPrice`) |
| `dj_digger/webext/extension/welcome.html` + `.js` | onboarding i status: uprawnienia hostów (`permissions.contains` / `request`), status połączenia |
| `dj_digger/webext/extension/selftest.html` + `.js` | ręczny self-test `dom.js` na syntetycznym DOM (W15) |
| `pyproject.toml` | usunięty `playwright`; nowy skrypt `dj-digger-host = "dj_digger.webext.host:main"`; pliki rozszerzenia w wheelu; usunięte markery `bandcamp_dom` i `shop_mutate` |

Usuwane: `dj_digger/browser_session.py`, `gui/runtime_smoke.py` (albo smoke bez przeglądarki),
`tests/test_browser_session.py`, `tests/test_shop_mutate.py`, `tests/test_bandcamp_dom.py`,
`ChromiumMissing`.

## 3. Protokół

Ramki JSON. Aplikacja wysyła `{"id": n, "cmd": "...", "args": {...}}`, rozszerzenie odpowiada
`{"id": n, "ok": true, "result": ...}` albo `{"id": n, "ok": false, "error": {"code", "message"}}`.
Zdarzenia przychodzą bez `id`: `{"event": "...", ...}`. Pierwsza ramka po połączeniu to
`{"event": "hello", "browser": "firefox|chrome|...", "version": "...", "protocol": 1,
"instance": "<losowe id startu background>"}`. Zmiana `instance` przy tym samym połączeniu
oznacza restart background (W1). Przy niezgodności `protocol` aplikacja odpowiada błędem
„przeładuj/zaktualizuj rozszerzenie”.

**Kolejność:** rozszerzenie wykonuje komendy dotyczące jednej karty po kolei. Komenda
`cancel {id}` przerywa czekanie trwającej komendy (np. czekanie na element), a karta
pozostaje zajęta do czasu odpowiedzi na anulowaną komendę (K2).

| Komenda | Argumenty | Wynik / uwagi |
|---|---|---|
| `window.open` | `visible`, `offscreen?` | `{windowId, offscreen}`; zawsze NOWE okno, jedno na sesję (kilka sesji naraz). `offscreen:true` (F4b, tylko ukryta sesja w Chromium) daje wirtualne `windowId` < -1 bez okna przeglądarki; `offscreen:false` w odpowiedzi = zwykłe okno (Firefox, brak dokumentu offscreen). Na sesji offscreen `window.show/hide`, `tab.focus/capture`, `dom.call` kończą się błędem `offscreen` |
| `window.show` / `window.hide` | `windowId` | przejście ukryty ↔ widoczny (D3) |
| `window.close` | `windowId` | zamyka tylko karty tego okna: `tab.removed` dla każdej i `window.removed {windowId}` (tak samo, gdy użytkownik zamknie okno ręcznie) |
| `tab.new` | `windowId, url?` | `tabId` (karta staje się „własna”) |
| `tab.goto` | `tabId, url, timeoutMs` | `{url, status}`. Gotowość z `webNavigation.onDOMContentLoaded` (frameId 0), status z `webRequest.onCompleted` (`main_frame`, ostatni po przekierowaniach), błąd z `webNavigation.onErrorOccurred`. Listenery rejestrowane przed `tabs.update`. Odmowa, gdy URL spoza allowlisty |
| `tab.reload` | `tabId, timeoutMs` | jak `tab.goto` |
| `tab.close`, `tab.info` | `tabId` | działają na **każdej** własnej karcie i popupie, także spoza allowlisty; `info` dla hosta spoza allowlisty zwraca tylko host (W5, W6) |
| `tab.focus` | `tabId` | zastępuje `bring_to_front` (karta i okno na wierzch) |
| `tab.html` | `tabId, maxBytes=2_000_000` | `{html, truncated}`. Gdy strona jest większa, `html` jest pusty i `truncated: true`; adapter rzuca wtedy ten sam `AutomationError` co `store_parse` (W4) |
| `tab.capture` | `tabId` | PNG base64 (`captureVisibleTab`: aktywna karta w widocznym oknie; best effort, tylko diagnostyka) |
| `dom.count` / `dom.visible` | `tabId, locator` | natychmiast, bez czekania |
| `dom.attr` / `dom.text` / `dom.value` | `tabId, locator, [name], timeoutMs` | czekają, aż element istnieje (K1) |
| `dom.all` | `tabId, locator, attrs[], text, limit` | jedna odpowiedź z atrybutami i tekstem wszystkich dopasowań (do `limit`), bez czekania (W9) |
| `dom.click` / `dom.fill` | `tabId, locator, [value], timeoutMs` | czekają, aż element istnieje, jest widoczny i włączony; przewinięcie do widoku. Klik to `pointerdown`, `mousedown`, `pointerup`, `mouseup`, `click` w środku elementu. Fill to natywny setter `value` z prototypu oraz `input` i `change` z `bubbles` (W7). Odmowa przy denyliście (§4) |
| `dom.waitVisible` | `tabId, locator, timeoutMs` | odpowiednik `wait_for(state="visible")` |
| `dom.call` | `tabId, name` | tylko nazwane funkcje z `dom.js` (MAIN world, tylko odczyt); wynik niezaufany |
| `cookie.soundcloudToken` | | wartość `oauth_token` z `https://soundcloud.com` albo `null` |
| `cookie.clearGateDownload` | | usuwa `filedownloading` z hostów Hypeddit |
| `download.expect` | `tabId` | następne pobranie z CDN o tym samym origin co inne karty należy do tej karty (raz, 60 s) |
| `extension.reload` | – | tylko rozwój: `runtime.reload()` (rozpakowane rozszerzenie czyta pliki z dysku); `scripts/webext_spike.py reload` |
| `download.discard` | `downloadId` | `removeFile` + `erase` po skopiowaniu (D5) |
| `cancel` | `id` | przerywa czekanie komendy `id` |

`locator` to lista kroków: `{css}`, `{role, name, flags, exact}`, `{text, flags, exact}`,
`{placeholder}`, `{nth}`, `{first}`. Rozwiązywany od nowa przy każdej próbie, jak w Playwrighcie.
`:visible` w CSS oznacza niepusty bounding box i `visibility` ≠ `hidden`. Krok `role`
pomija ukryte elementy, a krok `text` ich nie filtruje (§1).

Zdarzenia:

- `tab.created {tabId, openerTabId, url}`: tylko gdy opener jest własną kartą; popup staje się
  własny.
- `tab.updated {tabId, url}` i `tab.removed {tabId}`.
- `download.created {downloadId, tabId, danger}`, `download.done {downloadId, path, bytes, mime}`
  i `download.failed {downloadId, error}`: tylko dla pobrań przypisanych do własnej karty (D5).
- `download.blocked {downloadId, tabId, reason}`: dialog „gdzie zapisać” albo `danger`.

**URL-e spoza allowlisty** (np. popup `accounts.spotify.com/authorize?...`) są w zdarzeniach
**zredukowane do `https://<host>/`**. Kod gate'ów potrzebuje tylko `is_hypeddit_url`
i `host_of`, a callbacki OAuth (`code=`, `state=`) nie przechodzą przez IPC ani do logów (W5).

Semantyka po stronie Pythona:

- **Sync** (gates, auth): zdarzenia są kolejkowane i dispatchowane na wątku wywołującym
  w `wait_for_timeout` i w każdej komendzie, jak w sync API Playwrighta. `_TabWatch.save`
  i generatory w `gates/browser.py` zachowują model jednego wątku.
- **Async** (koszyk): natywne `asyncio`, bez `to_thread`. Wątek czytający połączenie rozwiązuje
  `Future` przez `loop.call_soon_threadsafe`. Anulowanie (np. `asyncio.wait_for`) wysyła
  `cancel {id}` i oznacza kartę jako zajętą do czasu odpowiedzi. Kolejna komenda na tej
  karcie czeka, więc etapy weryfikacji nie nachodzą na siebie (K2).
- Timeout komendy wynosi domyślnie 15 s (`ACTION_TIMEOUT_MS` przenosimy do `webext/page.py`),
  a `tab.goto` ma 30 s jak dziś. Timeout czekania na element jest liczony w rozszerzeniu,
  a Python czeka o 2 s dłużej na odpowiedź.
- Limity Native Messaging: rozszerzenie → host do 64 MiB, host → rozszerzenie 1 MB. Duże są
  tylko `tab.html` i `tab.capture`, które idą w dozwolonym kierunku. Host odrzuca ramki od
  aplikacji większe niż 1 MB z błędem, zamiast je przekazać.

## 4. Granice bezpieczeństwa

Dziś automat ma dostęp tylko do pustego profilu. Po zmianie steruje prawdziwymi kontami
użytkownika. Zasady egzekwuje **rozszerzenie** jako granica zaufania, niezależnie od tego,
co i tak sprawdza Python:

1. **Allowlista hostów, jedno źródło prawdy** w `allowlist.json`:
   - Hosty dokładnie jak w Pythonie: `HYPEDDIT_HOSTS` (`hypeddit.com`, `hypd.it`, z `www.`),
     `STORE_HOSTS` (`bandcamp.com` i jego subdomeny, `beatport.com` i jego subdomeny) oraz
     `soundcloud.com` (tylko `/signin` i ciasteczko).
   - Tylko `https`, port domyślny, bez credentials w URL.
   - Test Pythona porównuje `allowlist.json` z `links.HYPEDDIT_HOSTS` i `store_urls.STORE_HOSTS`
     i wymusza, by `host_permissions` w manifeście im odpowiadały (K5).
   - `tab.goto` poza allowlistą kończy się błędem `refused`.
2. **Tylko własne karty.**
   - Komendy działają wyłącznie na kartach utworzonych przez rozszerzenie i na ich popupach.
     Karty użytkownika są niedostępne.
   - `dom.*`, `tab.html`, `tab.capture` i `tab.goto` wymagają dodatkowo, by bieżący URL karty
     był na allowliście.
   - `tab.close` i `tab.info` (sam host) działają na każdej własnej karcie, bo popupy
     providerów trzeba zamykać (W6). Popupy providerów nigdy nie są czytane ani klikane.
3. **Blokada checkoutu na poziomie nawigacji.** `webNavigation.onBeforeNavigate` dla własnych
   kart: nawigacja na ścieżki z `allowlist.json` → `forbiddenPaths` (np. `/checkout`,
   `/cart/checkout`, `/payment`; dokładna lista po R4/R6) zamyka kartę i zgłasza błąd. To jest
   główna bariera, a denylista klików jest uzupełnieniem.
4. **Denylista klików:** `dom.click` odmawia elementom, których dostępna nazwa pasuje do
   `/\b(check ?out|pay|payment|purchase|place order|buy now|complete order)\b/i`. Wyjątek to
   dokładnie „Buy Digital Track/Album” na Bandcampie (rozwinięcie formularza ceny,
   `_expand_buy_async`).
5. **Brak zdalnego kodu.** Nie ma `eval` ani `evaluate(string)`. `dom.call` wybiera tylko
   z nazwanych funkcji. W MAIN world wykonujemy wyłącznie odczyty, a ich wyniki są niezaufane,
   bo strona może podmienić globalne obiekty.
6. **Jeden kanał wejścia.** Komendy przychodzą wyłącznie przez port natywny. Manifest **nie**
   ma `externally_connectable`, background nie rejestruje `onMessageExternal`, a content
   scripty nie nasłuchują `window.postMessage`. `welcome.html` i `selftest.html` nie wystawiają
   komend.
7. **Ciasteczka:** dwie zaszyte komendy (§3), żadnego ogólnego `cookies.getAll`. Do
   `auth.json` trafia tylko `oauth_token`, jak dziś.
8. **Kanał do aplikacji:**
   - `allowed_origins` / `allowed_extensions` w manifeście hosta wskazują tylko nasze ID
     rozszerzenia.
   - `authkey` to 32 losowe bajty (hex) w `config_dir()/webext.json`, zapisane przez
     `write_private_json` (0600).
   - Tylko `send_bytes`/`recv_bytes` + JSON. Test pilnuje, że `.send(`/`.recv(` nie występują
     w `dj_digger/webext/` (K3).
   - Gniazdo: `$XDG_RUNTIME_DIR/dj-digger/webext.sock`. Fallback to dedykowany katalog
     z uprawnieniami 0700 w `data_dir()`. Na macOS ścieżka musi mieć poniżej 104 znaków, więc
     nazwa jest krótka, a przy przekroczeniu fallback na `/tmp/dj-digger-<uid>/` (0700).
   - Na Windows pipe `\\.\pipe\dj-digger-<user>`.
   - Stary plik gniazda po crashu jest usuwany **pod `InstanceLock`** (CLI i GUI dzielą lock
     w `data_dir()`, więc gniazdo ma najwyżej jednego właściciela, W14).
9. **Logi:** bez URL-i z query, bez HTML i bez wartości ciasteczek. Reużywamy istniejącego
   `redact_url`.
10. **Diagnostyka koszyka** zapisuje teraz strony zalogowanego konta, więc
    `redact_diagnostic_html` dodatkowo usuwa adresy e-mail.
11. **Zgoda na akcje:** `gate_social_actions` (spec §9.2) bez zmian (D9), z jawnym opisem,
    że akcje dzieją się na kontach zalogowanych w przeglądarce użytkownika. Reguła „nic poza stroną Hypeddit nie jest klikane” zostaje:
    zgodę OAuth (np. „Agree” w Spotify) klika człowiek.

Uprawnienia w manifeście: `nativeMessaging`, `tabs`, `scripting`, `downloads`, `cookies`,
`webRequest`, `webNavigation`, `storage`, `alarms` oraz `host_permissions` z pkt 1. W Firefoksie
127+ hosty są przyznawane przy instalacji, ale użytkownik może je odebrać w każdej chwili.
Dlatego `hello` zawiera wynik `permissions.contains`, Ustawienia pokazują brak uprawnień,
a `welcome.html` pozwala je przywrócić (W13).

## 5. Mapowanie Playwright → rozszerzenie

| Dziś | Po zmianie |
|---|---|
| `sync_browser_context(profile, headless=…)` | `webext.page.session(visible=…)`: context manager zwracający `ExtensionContext` (okno robocze); zamknięcie zamyka własne karty i okno |
| `launch_persistent_context` + `launch_viewer` | jeden async `ExtensionContext`; „viewer” to `window.show` + `tab.focus` na karcie koszyka |
| `context.pages`, `context.new_page()`, `context.on("page")` | rejestr własnych kart w adapterze oraz zdarzenie `tab.created` |
| `page.opener` | `openerTabId` z `tab.created` |
| `page.on("download")` + `download.save_as` | `Download` z `suggested_filename` (basename ścieżki) i `save_as(path)`, czyli walidowana kopia plus `download.discard` |
| `page.on("popup")` | `tab.created` z `openerTabId` = ta karta |
| `page.on("response"/"console"/"crash")` | usunięte (logowanie debug w `_instrument_page`); zostaje tylko log statusu `tab.goto` |
| `goto(wait_until="domcontentloaded")` | `tab.goto` (`webNavigation.onDOMContentLoaded` + status z `webRequest`) |
| `context.cookies(["https://soundcloud.com"])` | `cookie.soundcloudToken` |
| `context.clear_cookies(name="filedownloading")` | `cookie.clearGateDownload` |
| `page.evaluate(TralbumData…)` / sugerowana cena | `dom.call("bandcampTralbum")` / `dom.call("bandcampSuggestedPrice")` |
| `click(force=True)`, `fill` | `dom.click`, `dom.fill` z semantyką z §3 |
| pętle `anchors.nth(i).get_attribute/inner_text` | `dom.all` (jedna komenda) |
| `screenshot(path)` | `tab.capture` (best effort) |
| `ChromiumMissing` → dialog „Download Chromium” | `ExtensionMissing` → dialog „Połącz przeglądarkę” (F7) |
| `BROWSER_PROFILE_LOCK`, `PROFILE_LOCK_*`, `headed_user_agent*`, `require_display`, `install_chromium` | usunięte |

## 6. Fazy wdrożenia

Każda faza kończy się commitem na branchu i zielonym `ruff` + `pytest` (domyślne markery).

### F0. Spike i rozpoznanie (najpierw, 1–3 dni)

Wyrzucany prototyp, czyli minimalne rozszerzenie plus skrypt Pythona na stdin/stdout. Cel:
zamknąć ryzyka, zanim zaczniemy przepisywać kod. Wynik trafia do
`docs/implementation/browser-extension-spike.md`:

- [ ] **R1:** syntetyczna sekwencja zdarzeń na Hypeddit (follow, like, Next, Connect, Download).
      Czy slajdy przechodzą? Czy `a.undone` z `target=_blank` otwiera popup, czy blokuje go
      popup blocker?
- [ ] **R2:** okno robocze `minimized` vs normalne `focused:false` w Chrome i Firefoksie.
      Czy gate działa w zminimalizowanym oknie (throttling timerów, `visibilityState`)? Czy
      menedżer okien na Fedorze (GNOME/KDE) kradnie fokus?
- [ ] **R3:** Native Messaging na Fedorze (Chrome i Firefox z RPM): host, utrzymanie background,
      wyjście hosta po zamknięciu przeglądarki. Lokalizacja manifestu w Firefoksie 147+
      (`~/.mozilla/native-messaging-hosts` vs `~/.config/mozilla/native-messaging-hosts`).
- [ ] **R4:** Beatport.
  - Mechanizm „Add to cart” na stronie tracka i wymóg logowania.
  - Struktura `beatport.com/cart` i licznik koszyka.
  - Ścieżki checkoutu do `forbiddenPaths`.
  - Czy strona release'u (bez 403) ma ID tracków w `ld+json` lub anchorach, czy tylko
    w `__NEXT_DATA__`?
  - Działanie `dom.fill`/`dom.click` na komponentach React.
  - Fixtures do `tests/fixtures/beatport/` (bez danych konta).
- [ ] **R5:** pobranie z Hypeddit: czy dopasowanie `webRequest.onResponseStarted` (`tabId`)
      ↔ `downloads.onCreated` (`url`/`finalUrl`) działa w obu przeglądarkach? Jaki jest
      faktyczny `referrer`? Zachowanie przy „pytaj, gdzie zapisać”, przy `danger` dla `.zip`
      i limit „wiele pobrań” w Chrome.
- [ ] **R6:** Bandcamp `add to cart` przez syntetyczną sekwencję zdarzeń, weryfikacja side-cartu,
      ścieżki checkoutu do `forbiddenPaths`.

**Kryterium przejścia:** R1, R5 i R6 działają albo mają potwierdzone obejście (R1-fallback
w §9). W przeciwnym razie wracamy do decyzji.

### F1. Kanał: `link.py`, `host.py`, `register.py` i szkielet rozszerzenia

- [ ] `host.py`:
  - `main()` czyta ramki NM ze `sys.stdin.buffer` i pisze do `sys.stdout.buffer`;
  - łączy się do aplikacji (`Client(address, authkey=…)`) z retry co 1 s;
  - przekazuje ramki w obie strony (dwa wątki) wyłącznie przez `send_bytes`/`recv_bytes`;
  - po rozłączeniu aplikacji wraca do czekania;
  - **na EOF ze stdin kończy proces** niezależnie od stanu połączenia z aplikacją;
  - ramki od aplikacji > 1 MB odrzuca z błędem;
  - nic nie pisze na stdout poza ramkami, diagnostyka idzie na stderr bez danych.
- [ ] `link.py`:
  - `Listener` startuje z `ApplicationServices` (GUI) albo na czas komendy CLI, która go
    potrzebuje; przed `bind` usuwa stare gniazdo pod `InstanceLock`;
  - śledzi `hello` i `instance` per połączenie;
  - `request(conn, cmd, args, timeout)` korelowane po `id`; zdarzenia trafiają do sesji
    powiązanej z połączeniem;
  - `ExtensionMissing`, gdy nie ma połączenia albo w ciągu 3 s nie ma `hello`;
  - zerwanie połączenia albo nowe `instance` kończy wszystkie sesje tego połączenia błędem
    `AutomationError("browser extension restarted")`.
- [ ] `register.py`:
  - zapisuje manifest hosta `com.github.fbialogrecki.djdigger` dla każdej wykrytej przeglądarki:
    - Linux: `~/.config/{google-chrome,chromium,BraveSoftware/Brave-Browser,microsoft-edge}/NativeMessagingHosts`
      oraz Firefox w obu lokalizacjach z R3;
    - macOS: `~/Library/Application Support/<przeglądarka>/NativeMessagingHosts`;
    - Windows: `HKCU\Software\{Google\Chrome,Chromium,BraveSoftware\Brave-Browser,Microsoft\Edge,Mozilla}\NativeMessagingHosts`;
  - `path` to bezwzględna ścieżka do `dj-digger-host` (pip: `shutil.which` lub sąsiad
    `sys.executable`; frozen: sąsiedni plik wykonywalny, F8);
  - kopiuje rozszerzenie do `data_dir()/browser-extension/` (D10);
  - idempotentne, wywoływane przy starcie GUI i z dialogu „Połącz przeglądarkę”;
  - `unregister()` usuwa manifesty i wpisy rejestru; dostępne jako
    `dj-digger browser unregister` i przycisk w Ustawieniach.
- [ ] Rozszerzenie:
  - `manifest.json` ze stałym `key` (stałe ID dla „Load unpacked”; publiczny klucz w repo,
    prywatny nie jest potrzebny do rozpakowanej instalacji i nie trafia do repo),
    `browser_specific_settings.gecko.id = "dj-digger@fbialogrecki.github.io"` i minimalne
    wersje (D6);
  - `background.js`: `connectNative`, reconnect przez `alarms`, dispatcher, allowlista,
    rejestr własnych kart i okna w **`storage.session`** (odtwarzany po restarcie service
    workera, W1), kolejka komend per karta;
  - `dom.js`: silnik lokatorów z czekaniem.
- [ ] Testy offline (`tests/test_webext.py`):
  - framing NM na `BytesIO`, EOF kończy hosta, ramka > 1 MB odrzucona;
  - `Listener` + `Client` w procesie: round trip, timeout, zdarzenia, zły `authkey` odrzucony,
    zerwanie połączenia kończy sesję;
  - brak `.send(`/`.recv(` w `dj_digger/webext/` (test na źródle);
  - `allowlist.json` zgodny z `HYPEDDIT_HOSTS`/`STORE_HOSTS` i z `host_permissions`;
  - `register.py` na `tmp_path` (bez prawdziwego HOME i rejestru; `winreg` za fałszywką).

### F2. Adapter `webext/page.py`

- [ ] `ExtensionContext`, `ExtensionPage`, `Locator` i `Download` z dokładnie tymi metodami,
      które wylistowano w §1, w wersji sync i natywnej async (te same nazwy,
      `await page.goto(...)`).
- [ ] Semantyka z §1 i §3: czekanie na element w akcjach i odczytach, `count`/`is_visible`
      natychmiast, rola pomija ukryte, klik i fill jak w §3, anulowanie async przez `cancel`
      i zajętość karty.
- [ ] `Locator.all_texts_and_attrs(attrs)` → `dom.all`. Dwie pętle w `stores/bandcamp.py`
      (`_bandcamp_dom_products`, `_bandcamp_search_candidates_async`) przechodzą na jedno
      wywołanie (W9).
- [ ] `get_by_role(role, name=re.Pattern)` → krok `{role, name: pattern.pattern, flags: "i"
      jeśli IGNORECASE}`. Inne flagi `re` dają `ValueError`. Test sprawdza każdy wzorzec
      z `stores/bandcamp.py` i `gates/browser.py` (zgodność z JS RegExp).
- [ ] `FakeExtension` (Python, w procesie) mówi protokołem z §3. Test kontraktowy obejmuje:
  - timeout czekania;
  - `cancel` i zajętość karty (dwa `asyncio.wait_for` po sobie nie nachodzą na siebie);
  - kolejność zdarzeń;
  - `truncated`;
  - zredukowane URL-e popupów.

### F3. Logowanie SoundCloud (U1)

- [ ] `auth.login_with_chromium` → `auth.login_with_browser`:
  - `cookie.soundcloudToken`; jeśli token jest i przechodzi `verify_and_save`, koniec;
  - w przeciwnym razie `tab.new("https://soundcloud.com/signin")` w widocznym oknie i polling
    tokenu co 250 ms przez 5 min (logika `_wait_for_oauth_cookie` bez zmian, tylko źródło
    ciasteczka).
- [ ] Wywołania: `services/accounts.py:35`, `cli.py:399` (CLI hostuje `Listener` na czas
      komendy; GUI i CLI wykluczają się przez `InstanceLock`).
- [ ] Testy w `tests/test_auth.py`: fałszywy kontekst rozszerzenia zamiast fałszywego
      `sync_browser_context`. Zachowujemy przypadki odrzuconego tokenu, timeoutu i anulowania.
      Testy o instalacji Chromium zastępują testy `ExtensionMissing`.

### F4. Gate'y Hypeddit (U2)

- [ ] `_browser_pass`: `browser_session.sync_browser_context(...)` → `webext.page.session(...)`.
- [ ] **Przejście ukryty → widoczny w tym samym oknie (W3):**
  - `download_hypeddit_batch_in_browser` otwiera jedną sesję na całą partię;
  - po ukrytym przebiegu zamyka karty wierszy rozstrzygniętych (z ich popupami);
  - karty wierszy odroczonych **przeładowuje w miejscu** (`tab.goto` na URL gate'u), a nie
    otwiera nowych;
  - `_TabWatch` zachowuje przypisanie kart; `reset_tabs()` znika;
  - dopiero potem `window.show` i drugi przebieg driverów z `attended=True`.
- [ ] `_click_gate_download`: `page.context.clear_cookies(name=…)` → `cookie.clearGateDownload`,
      a pobranie przechodzi przez blokadę „jedno naraz” z D5.
- [ ] `_TabWatch.watch`: `page.on("download")` i `page.on("popup")` działają przez zdarzenia
      adaptera. `files.save_browser_download` przyjmuje `Download` z adaptera bez zmian
      interfejsu. `download.blocked` odracza wiersz do widocznego okna z komunikatem.
- [ ] `_wait_for_provider` korzysta tylko z hosta popupu (zredukowane URL-e, W5); bez zmian
      logiki.
- [ ] `auth.BROWSER_PROFILE_LOCK` → lokalny `threading.Lock` w `gates/browser.py` (nadal jedna
      partia gate'ów naraz).
- [ ] Jeśli R1 wymaga obejścia: wariant z §9 (R1-fallback).
- [ ] Testy w `tests/test_gates.py`: `_GateContext` i `_GatePage` zostają. Nowe przypadki:
  - ukryty → widoczny w tym samym kontekście (zamknięte rozstrzygnięte, odroczone
    przeładowane, brak duplikatów);
  - pobranie bez jednoznacznego przypisania daje błąd ręczny, a nie zły plik.

### F4b. Ukryty przebieg Hypeddit w dokumencie offscreen (decyzja właściciela 2026-10-07)

Powód: na GNOME/Wayland okno robocze nie daje się ukryć (`focused:false` i `minimized` są
ignorowane, spike R2). Spike R2b pokazał, że gate działa w ramce dokumentu offscreen: strona
jest `visible`, timery nie są dławione, `hasStorageAccess() = true`, pobranie przychodzi
z `referrer: https://hypeddit.com/`. Hypeddit nie wysyła `X-Frame-Options`/`frame-ancestors`.
Zakres: tylko gate'y Hypeddit w Chromium. Firefox (bez `chrome.offscreen`) i wszystko poza
Hypeddit zostaje przy oknie roboczym.

- [x] Rozszerzenie:
  - uprawnienie `offscreen`; `offscreen.html`/`offscreen.js` trzymają po jednej ramce na kartę;
  - `window.open {visible:false, offscreen:true}` tworzy sesję offscreen (wirtualne
    `windowId`) albo odpowiada `offscreen:false`, gdy przeglądarka tego nie umie;
  - `tab.new` w takiej sesji daje wirtualną kartę (ujemne `tabId`) = ramkę; obsługiwane
    `tab.goto`/`tab.reload`/`tab.info`/`tab.close`/`tab.html`/`dom.*`; `tab.focus`,
    `tab.capture` i `window.show` dają czytelny błąd `offscreen`;
  - `dom.js` w ramce jako content script rejestrowany dynamicznie tylko na czas sesji
    (`scripting.registerContentScripts`, `allFrames`, wyłącznie hosty Hypeddit), aktywny tylko
    wtedy, gdy `location.ancestorOrigins` wskazuje origin rozszerzenia; w kartach użytkownika
    nic nie robi;
  - kanał ramka → background: `runtime.onConnect` przyjmuje port tylko z ramki bez `tab`
    (dokument offscreen), z URL-em na allowliście Hypeddit i z nonce'em ramki nadanym przez
    `offscreen.js`; wszystko inne jest rozłączane. Dane z ramki pozostają niezaufane;
  - pobrania: przypisanie po `referrer` obejmuje wirtualne karty (URL z ostatniego raportu ramki);
  - restart service workera i `app-offline` zamykają dokument offscreen jak okna.
- [x] Aplikacja (`webext/page.py`, `gates/browser.py`):
  - `session(visible=False, offscreen=True)`; gdy odpowiedź mówi `offscreen:false`, zwykłe
    okno jak dziś;
  - ukryty przebieg partii gate'ów idzie w offscreen; wiersze odroczone (CAPTCHA, Connect/OAuth,
    zgoda Spotify, `download.blocked`) przechodzą do widocznego okna roboczego otwieranego
    dopiero wtedy, z kartą na URL gate'u;
  - w offscreen nie klika się przycisków Connect (popup OAuth byłby oknem) — taki wiersz od
    razu jest odroczony.
- [x] Testy: harness (sesja offscreen, wirtualne karty, odrzucenie portu spoza dokumentu
      offscreen i spoza allowlisty, przypisanie pobrania), testy adaptera i `tests/test_gates.py`
      (offscreen → widoczne okno dla odroczonych, brak offscreen → okno), test granic
      bezpieczeństwa zaktualizowany o jeden dozwolony kanał `onConnect`.
- [x] Spec §10.2, §11.4, §13.2; CHANGELOG.

### F5. Koszyk Bandcamp i lookup Beatport (U3, U4)

- [ ] `CartBrowserSession`:
  - `_playwright_handle`, `_ensure_context`, `_viewer_context` i `_close_viewer` zastępuje
    jeden async `ExtensionContext`;
  - `_work_pages` i `_replace_page` bez zmian logiki;
  - `_open_final_carts` i `_stage_manual_page` to `window.show` + nowa karta lub `tab.focus`;
  - `setup_logins` i `action_store_login` → **usunięte** (sesja użytkownika). W zamian, przy
    stwierdzeniu niezalogowania, otwieramy stronę logowania w widocznym oknie i czekamy jak
    dziś `_ensure_logins_async` (5 min).
- [ ] `stores/bandcamp.py`:
  - dwa `page.evaluate` → `dom.call` (adapter wystawia `evaluate` tylko dla tych dwóch nazw);
  - `_navigate_async` bierze status z `tab.goto`;
  - pętle po anchorach przez `dom.all` (F2);
  - `_page_products_async`: `truncated` daje ten sam błąd co dziś;
  - `save_cart_diagnostics`: screenshot best effort, HTML redagowany jak dziś plus e-maile;
  - `_dismiss_bandcamp_cookie_banner` zostaje.
- [ ] Usunięcie `_instrument_page` (zdarzenia `response`, `console` i `crash` nie istnieją).
- [ ] Testy w `tests/test_cart.py`: fałszywe strony zostają. Zamieniamy fałszywy
      `launch_persistent_context` i `launch_viewer` (`test_cart.py:797`, `:1716`) na fałszywy
      kontekst i aktualizujemy testy viewera (focus zamiast kopii ciasteczek). Nowy test:
      anulowany etap weryfikacji nie nachodzi na kolejny.

### F6. Koszyk Beatport (nowość, D7)

- [x] Rozwiązywanie produktu bez zmian w logice: direct link bez przeglądarki; link release'u
      to `_page_products_async` + `match_product` na stronie otwartej przez rozszerzenie.
      `__NEXT_DATA__` w `store_parse.py` tylko wtedy, gdy wymaga tego R4. Żadnej ścieżki
      wyszukiwania. Odstępstwo: direct link też otwiera stronę (zalogowanie i cena są znane
      dopiero z przeglądarki); bez logowania zostaje dokładny wpis playlisty.
- [x] `stores/beatport.py`, symetrycznie do Bandcampa, w minimalnym zakresie z R4:
  - `_beatport_cart_contains_async`: strona koszyka, anchor z tym samym numerycznym ID tracka;
  - `_add_to_cart_async`: rewalidacja ID, tytułu, wersji i ceny z preflightu, potem klik
    kontrolki „Add to cart” zidentyfikowanej w R4;
  - weryfikacja: licznik koszyka, potem strona koszyka, w budżecie czasowym jak Bandcamp.
    Niepewna weryfikacja nie powtarza kliknięcia (spec §3.6).
- [x] `services/purchases.py`:
  - Beatport przestaje być wycinany przez `_playlist_results` i `_split_direct_beatport`
    dla pozycji z dokładnym ID i zalogowanym użytkownikiem;
  - wykonanie seryjne per sklep, circuit breaker i diagnostyka jak dla Bandcampa;
  - `_preflight_failure` dla Beatportu: challenge, brak logowania lub brak dokładnego ID
    w kodzie strony daje istniejący `_beatport_playlist_result` (fallback Soundiiz bez zmian).
- [x] `allowlist.json`: `forbiddenPaths` dla Beatportu z R4.
- [x] GUI: wynik pokazuje „w koszyku Beatport” oraz osobno „do playlisty” dla reszty. Akcja
      `P` (otwieranie stron Beatport) zostaje.
- [x] Testy: fałszywe strony z fixtures R4 (`tests/fixtures/beatport/`), w tym zmiana ceny,
      brak logowania, niepewna weryfikacja i fallback do playlisty.

### F7. GUI: onboarding, zgoda i sprzątanie

- [ ] `with_chromium` → `with_extension`: przy `ExtensionMissing` dialog ze statusem i krokami:
  - Chrome, Edge i Brave: „Załaduj rozpakowane” z `data_dir()/browser-extension/` (D10) plus
    przycisk „Kopiuj ścieżkę”;
  - Firefox: link do podpisanego XPI (F8) lub `about:debugging` w trybie dev;
  - przycisk „Sprawdź połączenie”, który ponawia komendę po `hello`.
- [ ] Ustawienia:
  - wiersz „Przeglądarka automatyzacji: <nazwa, wersja rozszerzenia> / niepołączona / brak
    uprawnień do hostów”;
  - opis przełącznika `gate_social_actions` uzupełniony o skutek na prawdziwych kontach (D9);
  - przycisk „Odłącz przeglądarki” (`unregister`);
  - usunąć logowanie Bandcamp i reset profilu sklepu.
- [ ] Komunikat „przeładuj rozszerzenie” przy niezgodnym `protocol`.
- [ ] `translations/pl.ts`: nowe i usunięte stringi.
- [ ] Usunąć `gui/runtime_smoke.py` lub zastąpić smoke'iem bez przeglądarki (wywołanie
      `--runtime-test` w `gui/__init__.py` i w buildach).

### F8. Usunięcie Playwrighta i paczkowanie

- [ ] Usunąć `browser_session.py`, `ChromiumMissing`, `install_chromium`, `with_chromium`,
      `PLAYWRIGHT_BROWSERS_PATH` i `playwright` z zależności (całkowicie, D8). Odświeżyć
      `uv.lock`. Usunąć markery `bandcamp_dom` i `shop_mutate` z `pyproject.toml`.
- [ ] `packaging/desktop.spec`:
  - usunąć `playwright` z kolekcji;
  - dodać pliki `webext/extension/*` jako dane;
  - zbudować drugi plik wykonywalny **konsolowy** `dj-digger-host` we wspólnym `COLLECT`
    (windowed exe nie ma stdio na Windows).
- [ ] `packaging/{windows,macos}/build.py`: usunąć `playwright install chromium`. Bundel
      schudnie o ~150 MB.
- [ ] `.github/workflows/desktop.yml`: sprawdzić, czy smoke nie zakłada Chromium.
- [ ] Firefox: XPI podpisany jako *unlisted* (`npx web-ext sign --channel=unlisted`). Wymaga
      konta AMO i kluczy API właściciela, więc to krok ręczny. Przed podpisem lokalnie
      `npx web-ext lint`. Bez Node w CI. Pole `update_url` zależy od decyzji W11 (§10).
- [ ] `docs/installation.md`: komendy dla właściciela (`npx web-ext lint`,
      `npx web-ext sign --channel=unlisted`) z katalogu `dj_digger/webext/extension`.

### F9. Specyfikacja i dokumentacja

Zmiany w `PROJECT-SPECIFICATION.md` (zachowanie się zmienia, więc w tym samym branchu,
zgodnie z AGENTS.md):

- §3.6: koszyk Beatport, okno robocze zamiast headless, brak logowania w Ustawieniach.
- §5.2: nowy komponent `webext/`; usunięte `browser_session.py`.
- §6.1: zależności (bez `playwright`), wymagania (rozszerzenie zamiast Chromium i wyświetlacza),
  minimalne wersje przeglądarek, WSL.
- §6.3: gniazdo, `webext.json`, `data_dir()/browser-extension/`.
- §7.4: `webext.json`; stare profile nieużywane.
- §9.1: logowanie SoundCloud z przeglądarki.
- §9.2: akcje (w tym Connect) wykonują się na kontach zalogowanych w przeglądarce użytkownika.
- §10.2 (Hypeddit): ukryty i widoczny przebieg w jednym oknie, przypisywanie pobrań, pobrania
  zablokowane.
- §10.3 i §10.4: przeglądarki i koszyki.
- §11: nowa granica zaufania (§4 tego planu).
- §11.3: blokada checkoutu na poziomie nawigacji.
- §12.1 i §12.2: brak nowych transferów do stron trzecich; rozszerzenie nie wysyła nic do sieci;
  diagnostyka z kont zalogowanych.
- §12.3: `dj-digger browser unregister`.
- §13.2: ograniczenia (Flatpak, Snap, incognito, kontenery, „pytaj, gdzie zapisać”,
  niebezpieczne pobrania, Safari, tryb deweloperski w Chrome przy instalacji spoza sklepu).
- §14: markery testów i self-test.

Następnie `python3 scripts/spec_section_map.py` i `--check`. Poza tym README (instalacja,
pierwsze uruchomienie, ręczne usunięcie starych profili), `docs/installation.md`,
`CHANGELOG.md` (1.4.0; uwaga, że koszyk gościa ze starego profilu `store-browser` nie będzie
widoczny w przeglądarce użytkownika) oraz `docs/gui-e2e-checklist-pl.md` (nowe kroki ręczne).

### F10. Praca bez okien i wyniki w aplikacji (rew. 3, doprecyzowanie właściciela 2026-10-07)

Zasady (nadrzędne; Z = zasada):

- **Z1.** Automat nie pokazuje okna, karty ani popupu przeglądarki sam z siebie. Wyjątki:
  aktywna karta koszyka na końcu partii koszyka oraz to, co użytkownik wybierze w aplikacji
  („Otwórz w przeglądarce”, „Pokaż koszyki”, logowanie).
- **Z2.** Gate wymagający człowieka (Connect/OAuth, CAPTCHA, zgoda Spotify, pobranie
  zatrzymane przez przeglądarkę) nie otwiera okna. Po partii aplikacja pyta: „N gate'ów
  wymaga Ciebie (powody): Otwórz w przeglądarce / Pomiń”. Pominięte trafiają do wyników.
- **Z3.** Każda partia gate'ów z nieudanymi wierszami kończy się oknem „Wyniki pobierania”
  w aplikacji: lista z powodami i wybory Zamknij / Ponów nieudane / Otwórz nieudane
  w przeglądarce / (gdy brak rozszerzenia) Połącz przeglądarkę i ponów.
- **Z4.** Koszyk nie pyta o akceptację. Cena „name your price” wynika z ustawienia
  `nyp_price` (domyślnie minimalna, do zmiany w Ustawieniach na sugerowaną). Koszyk to nie
  zakup: przegląd i checkout zostają po stronie użytkownika w karcie koszyka.
- **Z5.** Wyniki koszyka wymieniają każdy zaznaczony utwór, którego nie dodano: brak linku do
  sklepu, nie znaleziono, niejednoznaczny, błąd — i oferują „Otwórz N linków w przeglądarce”.
- **Z6.** Stan wiersza w aplikacji jest aktualny w trakcie: postęp HTTP, „w trakcie” dla
  przebiegu w przeglądarce, „pobrane” od razu po pliku, nie dopiero na końcu partii.

Zadania:

- [x] Gate'y (`gates/browser.py`, `services/downloads.py`):
  - po przebiegu offscreen wiersze odroczone nie idą automatycznie do widocznego okna:
    callback `attend(reasons) -> bool` z GUI decyduje (Z2); bez callbacku (CLI) zachowanie
    jak dotąd;
  - krok z polem komentarza (`textarea` na slajdzie click-through) wypełniany
    `config.random_comment()` (dziś tylko ścieżka HTTP);
  - Firefox bez offscreen: ukryte okno jak dotąd, ale bez automatycznego `show()` (Z2).
- [x] GUI pobierania (`gui/backend.py`): obsługa zdarzeń `waiting`, `downloaded`, `status`,
      `browser_started`, `browser_finished`, `summary` (Z6); okno „Wyniki pobierania” (Z3);
      pytanie Z2.
- [x] Koszyk (`services/purchases.py`, `webext/page.py`, `background.js`):
  - sesja „w tle”: `window.open {background:true}` przypina sesję do ostatnio aktywnego
    zwykłego okna użytkownika, `tab.new` tworzy kartę `active:false` w tym oknie; gdy
    przeglądarka nie ma okna, powstaje nowe (nieuniknione);
  - `window.close` takiej sesji zamyka tylko jej karty, nigdy okna użytkownika;
  - koniec partii: karta koszyka każdego sklepu aktywowana w oknie użytkownika, karty
    robocze zamknięte; „Pokaż koszyki” aktywuje je ponownie;
  - bez formularza „Review cart” (Z4); `purchase_price` respektuje `nyp_price`;
  - wiersze bez linku Bandcamp/Beatport nie znikają: wynik „brak linku do zakupu” (Z5);
  - `CartResult.url` wypełnione dla każdej pozycji niedodanej; wybór „Otwórz linki”.
- [x] Ustawienia i konfiguracja: `nyp_price` (`minimum`/`suggested`) w `config.py`
      i w dialogu Ustawień; tłumaczenia.
- [x] Testy: harness (sesja w tle nie tworzy okna, nie zamyka okna użytkownika), testy
      `test_gates.py` (attend: pomiń/otwórz, komentarz), `test_cart.py` (bez akceptacji,
      `nyp_price`, brak linku, URL-e niedodanych), testy GUI zdarzeń i wyników.
- [x] Spec §3.5, §3.6, §3.8, §7.4, §10.2, §10.3; CHANGELOG; checklista §21.
- [x] Pomiar na żywo: throttling timerów w nieaktywnej karcie (`timers`) przy partii
      koszyka > 5 min; jeśli przeszkadza, Bandcamp przechodzi do offscreen (nie wysyła
      `X-Frame-Options`), Beatport zostaje w karcie (wysyła `deny`). **Wynik 2026-10-08:** tylko
      wyrównanie do 1 s (≤ 1 s na czekanie), bez intensywnego throttlingu przez 6,6 min — plan B
      niepotrzebny.

## 7. Testy

| Poziom | Co | Gdzie / marker |
|---|---|---|
| Offline (domyślnie) | framing NM i EOF, `Listener` (auth, zerwanie, restart instancji), zakaz pickle, zgodność allowlisty, `register.py`, adapter + `FakeExtension` (czekanie, cancel, kolejność, `truncated`), przeniesione testy gates, cart i auth, nowe testy Beatport, przypisywanie pobrań | `tests/test_webext.py`, istniejące pliki |
| Self-test `dom.js` | `selftest.html` w obu przeglądarkach: role, tekst, placeholder, `:visible`, `nth`, czekanie, klik (sekwencja zdarzeń), fill na kontrolowanym inpucie React-like, `dom.all` | ręcznie, wiersz W11 macierzy |
| Lint rozszerzenia | `npx web-ext lint` | lokalnie, przed podpisem |
| Live, tylko odczyt | jak dziś `shop_live` / `hypeddit_live`, przez rozszerzenie | istniejące markery, ręcznie |
| Ręczne | macierz §8 | `docs/gui-e2e-checklist-pl.md` |

Minimum na koniec każdej fazy:

```bash
python3 scripts/spec_section_map.py --check
uv run --extra dev ruff check .
uv run --extra dev pytest
```

## 8. Macierz akceptacji (warunek merge)

**Priorytet: przeglądarki z rodziny Chromium (decyzja właściciela 2026-10-06).** Na Fedorze
(Chrome) wszystkie wiersze. Na Windows 10/11 (Chrome i Edge) i macOS (Chrome) co najmniej W1,
W2, W4, W8 i W11. Firefox zostaje w kodzie (jedna baza kodu), ale jego testy są odłożone
i nie blokują merge; sprawdzono tylko W11 (`selftest.html`: ALL PASS) i ładowanie przez
`web-ext run`.

| # | Scenariusz | Oczekiwane |
|---|---|---|
| W1 | Pierwsze uruchomienie bez rozszerzenia | dialog „Połącz przeglądarkę”; po instalacji `hello` w ≤ 3 s; Ustawienia pokazują przeglądarkę i uprawnienia |
| W2 | Logowanie SoundCloud przy zalogowanej przeglądarce | token zapisany bez okna logowania |
| W3 | Logowanie SoundCloud przy wylogowanej przeglądarce | karta `/signin` w oknie roboczym; po zalogowaniu token zapisany, okno zamknięte |
| W4 | Partia 5 gate'ów Hypeddit (email, click-through, komentarz) | **żadne okno, karta ani popup przeglądarki w trakcie**; w aplikacji postęp i stan każdego wiersza na bieżąco; wszystkie pliki pobrane, każdy pod właściwym trackiem; pliki w folderze playlisty; brak kopii w Pobranych |
| W5 | Gate z CAPTCHA i z pierwszą zgodą Spotify | **przed jakimkolwiek oknem** pytanie w aplikacji „N gate'ów wymaga Ciebie”; „Pomiń” → wiersze w wynikach; „Otwórz” → okno tylko z tymi gate'ami, po akcji człowieka plik pobrany |
| W6 | Koszyk Bandcamp: 3 pozycje, w tym jedna NYP | **bez okna i bez pytania o akceptację**; karty robocze nieaktywne w oknie użytkownika; na końcu aktywna karta koszyka z 3 pozycjami; NYP po cenie minimalnej, po zmianie ustawienia — po sugerowanej; nic nie kliknięte poza add-to-cart; checkout zablokowany |
| W7 | Koszyk Beatport: 3 pozycje z ID + 1 bez ID + 1 utwór bez linku do sklepu | jak W6; 3 w koszyku Beatport; powiadomienie wymienia 2 niedodane z powodem; „Otwórz linki” otwiera je w przeglądarce |
| W8 | Zamknięcie aplikacji w trakcie / zamknięcie przeglądarki w trakcie / przeładowanie rozszerzenia w trakcie | brak zawieszenia; wiersze z jasnym błędem; host kończy się po zamknięciu przeglądarki; brak osieroconych procesów `dj-digger-host` |
| W9 | Anulowanie partii gate'ów i koszyka (także w trakcie etapu weryfikacji) | jak dziś (spec §10.2, §3.6); brak nakładających się komend na karcie |
| W10 | Instalacja z wheela na czystym systemie bez Chromium | brak pobierania przeglądarki; brak `playwright` w jakichkolwiek zależnościach i w `uv.lock` |
| W11 | `selftest.html` w Chrome i Firefoksie | wszystkie przypadki zielone |
| W12 | Dwie przeglądarki z rozszerzeniem jednocześnie | partia zostaje w przeglądarce, w której się zaczęła; Ustawienia pokazują aktywną |
| W13 | Odebranie uprawnień do hostów w Firefoksie (`about:addons`) | Ustawienia pokazują brak uprawnień; komendy dają czytelny błąd zamiast zawieszenia |
| W14 | `dj-digger browser unregister` | manifesty i wpisy rejestru usunięte; rozszerzenie pokazuje „brak aplikacji” |
| W15 | Partia gate'ów z nieudanymi wierszami (np. gate usunięty, brak rozszerzenia) | okno „Wyniki pobierania” z powodami; „Ponów nieudane” ponawia tylko je; „Otwórz w przeglądarce” otwiera ich gate'y; przy braku rozszerzenia „Połącz przeglądarkę i ponów” |

Dodatkowo: `npx web-ext lint` bez błędów, a domyślny pytest zielony w CI na trzech systemach.

## 9. Ryzyka

| # | Ryzyko | Skutek | Mitygacja |
|---|---|---|---|
| R1 | Syntetyczne zdarzenia (`isTrusted=false`) nie otwierają popupu (popup blocker) albo Hypeddit je ignoruje | gate'y nie przechodzą bez człowieka | **R1-fallback:** skrypt MAIN-world w karcie gate'u opakowuje `window.open`; zablokowany URL trafia do `tabs.create({openerTabId})`, a callback OAuth Hypeddit idzie przez storage strony (spec §10.2), więc `opener` nie jest potrzebny. W ostateczności `chrome.debugger` (`Input.dispatchMouseEvent`) tylko w Chromium: nowe, groźnie wyglądające uprawnienie i żółty pasek; Firefox wtedy przez widoczny przebieg |
| R2 | Okna rozszerzenia nie da się ukryć na GNOME/Wayland (potwierdzone w spike'u) | okno wyskakuje | D3/Z1: gate'y w offscreen, koszyki w nieaktywnych kartach okna użytkownika, okno tylko na polecenie |
| R11 | Nieaktywna karta jest throttlowana (po 5 min: timery raz na minutę) albo zamrażana (Energy Saver) | wolny koszyk przy dużej partii | pomiar w F10; plan B: Bandcamp w offscreen; `autoDiscardable:false` dla kart roboczych |
| R3 | Native Messaging nie działa w przeglądarkach z Flatpaka i Snapa; Firefox 147+ zmienia katalog konfiguracji | brak połączenia | wykrycie w `register.py` (`~/.var/app/...`, `/snap/...`), komunikat w dialogu; zapis manifestu w obu lokalizacjach Firefoksa |
| R4 | Beatport wymaga logowania lub antybot blokuje także prawdziwą przeglądarkę przy automatycznym klikaniu | brak koszyka Beatport | fallback playlista/Soundiiz już istnieje; F6 można odłożyć bez blokowania reszty |
| R5 | „Pytaj, gdzie zapisać”, `danger` dla `.zip`, limit wielu pobrań w Chrome, niejednoznaczne przypisanie pobrania | zatrzymany ukryty przebieg albo plik bez właściciela | `download.blocked` → widoczne okno i komunikat; jedno pobranie naraz; brak zgadywania przypisania (D5) |
| R6 | Restart background/service workera w trakcie partii | zgubione komendy, karty bez właściciela | stan w `storage.session`; nowe `instance` kończy sesje czystym błędem; reconnect przez `alarms` |
| R7 | Użytkownik ma kilka przeglądarek z rozszerzeniem | nieprzewidywalny wybór | sesja przypięta do połączenia (D2); Ustawienia pokazują aktywną |
| R8 | Zmiana ID rozszerzenia po publikacji w Chrome Web Store | host odrzuca połączenie | `allowed_origins` zawiera ID dev (z `key`) i ID sklepu |
| R9 | `dom.js` (nowy silnik lokatorów) bez testów automatycznych | subtelne różnice względem Playwrighta | silnik mały, semantyka spisana w §1/§3, `selftest.html` w macierzy (W11), F0 sprawdza na żywych stronach |
| R10 | Rozszerzenie spoza sklepu w Chrome/Edge na Windows/macOS | ostrzeżenia trybu deweloperskiego, gorszy onboarding dla użytkowników 1.4.0 | decyzja W11 w §10 przed wydaniem |

## 10. Decyzje

### Potwierdzone (2026-10-05)

1. **Beatport:** bez wyszukiwania po nazwie, nigdy. Dokładny track z linku bezpośredniego albo
   z kodu strony release'u. Reszta to fallback playlisty i Soundiiz (D7).
2. **Testy:** Playwright usunięty całkowicie, także z `dev`. JS rozszerzenia testujemy ręcznie,
   z `selftest.html` (D8).
3. **`web-ext lint`:** tylko lokalnie, przed podpisem XPI. Bez Node w CI.
4. **Identyfikatory:**
   - Firefox: `dj-digger@fbialogrecki.github.io` (niezmienne po pierwszym podpisie).
   - Chrome: ID z publicznego `key` w manifeście; po publikacji w CWS drugie ID
     w `allowed_origins`.
   - Host Native Messaging: `com.github.fbialogrecki.djdigger`.
5. **Wersja:** 1.4.0.

6. **Akcje na prawdziwych kontach (K6, D9):** bez nowego ustawienia i bez dodatkowego pytania.
   Zachowanie jak dziś, sterowane `gate_social_actions`; skutek jawnie opisany w Ustawieniach,
   spec, README i CHANGELOG.

8. **Priorytet przeglądarek (2026-10-06):** rodzina Chromium (Chrome, Edge, Brave). Firefox
   pozostaje wspierany w kodzie, ale bez testów akceptacyjnych na tym etapie; spike F0 robimy
   tylko w Chrome.

9. **Bez okien (2026-10-07):** obowiązuje efekt z §0 i zasady Z1–Z6 (F10). Rozszerzenie
   zostaje mechanizmem; alternatywy odrzucone (prawdziwe sesje, antybot, koszyk Bandcamp
   w przeglądarce użytkownika).
10. **Cena „name your price” (2026-10-07):** domyślnie minimalna, ustawienie `nyp_price`
    pozwala wybrać sugerowaną. Koszyk bez formularza akceptacji.

### Otwarte

7. **W11, dystrybucja przed wydaniem 1.4.0** (nie blokuje merge, decyzja odłożona).
   Rekomendacja:
   - Chrome Web Store jako *unlisted* (link tylko dla użytkowników aplikacji). Działa na
     Windows/macOS bez trybu deweloperskiego i daje automatyczne aktualizacje. Wymaga konta
     dewelopera (jednorazowa opłata) i przeglądu.
   - AMO *unlisted* z `update_url` wskazującym plik aktualizacji w GitHub Releases.

   Alternatywa: wydać 1.4.0 z instalacją w trybie deweloperskim i zaakceptować ostrzeżenia.

## 11. Kryteria merge do `main`

- [ ] F0–F10 zamknięte; `docs/implementation/browser-extension-spike.md` opisuje wyniki R1–R6.
- [ ] Macierz §8 przeszła; wyniki dopisane do `docs/gui-e2e-checklist-pl.md`.
- [ ] `rg -i 'playwright|chromium' dj_digger/` zwraca tylko uzasadnione wystąpienia
      (np. nazwy przeglądarek w `browser.py`).
- [ ] Specyfikacja zaktualizowana, `spec_section_map.py --check` zielone.
- [ ] Domyślny pytest i ruff zielone w CI.
