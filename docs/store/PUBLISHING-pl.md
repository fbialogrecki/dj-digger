# Publikacja rozszerzenia w Chrome Web Store (niepubliczne / Unlisted)

Wszystkie teksty do wklejenia są w [`CHROMEWEBSTORE.md`](../../CHROMEWEBSTORE.md),
polityka prywatności w [`PRIVACY.md`](../../PRIVACY.md), grafiki w
[`screenshots/`](screenshots/).

## 0. Zanim zaczniesz

- `PRIVACY.md` musi być na `main` w publicznym repo (wypchnij commit). Sprawdź w
  oknie incognito, że https://github.com/fbialogrecki/dj-digger/blob/main/PRIVACY.md
  się otwiera.
- Przeczytaj sekcję „Review risk notes” w `CHROMEWEBSTORE.md`. Najpoważniejsze
  ryzyko to „social actions” (komentarze/follow na bramkach, domyślnie włączone).
  Ewentualną zmianę zachowania (np. domyślnie wyłączone) zrób **przed** wysłaniem
  do recenzji.

## 1. Konto dewelopera (jednorazowo)

1. Wejdź na https://chrome.google.com/webstore/devconsole i zaloguj się kontem
   Google, które ma być właścicielem rozszerzenia (najlepiej osobnym, nie
   firmowym 3XPERTS/b3yond).
2. Na koncie Google włącz **weryfikację dwuetapową** (bez niej nie da się
   publikować).
3. Zaakceptuj umowę dewelopera i zapłać **jednorazową opłatę rejestracyjną**
   (5 USD, kartą).
4. Uzupełnij profil wydawcy: nazwa wydawcy (`Filip Białogrecki`), **adres e-mail
   kontaktowy** (będzie widoczny publicznie, trzeba go zweryfikować linkiem z
   maila).
5. **Status przedsiębiorcy (EU / DSA)**: dashboard zapyta, czy jesteś „trader”
   (przedsiębiorcą). Darmowy projekt hobbystyczny bez działalności gospodarczej →
   „non-trader”. Jeśli wydajesz to w ramach działalności, wybierz „trader” i podaj
   dane firmy (adres, telefon, e-mail) — będą widoczne publicznie. Google może też
   poprosić o weryfikację tożsamości; wtedy postępuj wg instrukcji w dashboardzie.

## 2. Paczka

```bash
python3 scripts/package_extension.py
```

Powstaje `dist/extension/dj-digger-chrome-<wersja>.zip` (oraz
`dj-digger-firefox-<wersja>.zip` na później). Paczka dla Chrome nie ma pola
`key` (sklep go odrzuca i nadaje własne ID), nie ma ustawień Firefoksa ani
`background.scripts`, nie ma strony testowej `selftest.html`.

## 3. Pierwszy upload (jako szkic) i ID sklepu

1. Dashboard → **New item** → wybierz `dj-digger-chrome-1.4.0.zip`.
2. Po uploadzie dashboard pokazuje **Item ID** (32 litery a–p) — od razu, jeszcze
   przed recenzją. Skopiuj je.
3. W repo ustaw w `dj_digger/webext/register.py`:
   `CHROME_STORE_ID = "<item id>"`, uruchom testy, commit.
4. Wydaj nową wersję aplikacji (`CHANGELOG.md`, tag, release — zwykła ścieżka).
   Dopiero aplikacja z tym ID wpisuje je do `allowed_origins` hosta Native
   Messaging; bez tego zainstalowane ze sklepu rozszerzenie pokazuje „not
   connected”. Aplikacja rejestruje hosta przy każdym starcie, więc wystarczy, że
   użytkownik uruchomi nową wersję.

(Kolejność ma znaczenie: recenzent powinien móc połączyć rozszerzenie ze
skopiowaną aplikacją, więc najlepiej wysłać do recenzji dopiero po wydaniu
aplikacji z `CHROME_STORE_ID`.)

## 4. Wypełnienie karty

Wszystko kopiuj z `CHROMEWEBSTORE.md`:

- **Store listing**: opis szczegółowy, kategoria (Productivity → Tools), język
  (English), ikona 128 px (`dj_digger/webext/extension/icons/icon-128.png`),
  4 zrzuty 1280×800 i mały kafelek 440×280 z `docs/store/screenshots/`, strona
  domowa i wsparcie (GitHub, Issues). Opis krótki pochodzi z `manifest.json`.
- **Privacy practices**: single purpose, uzasadnienie każdego uprawnienia
  (12 pól) i host permissions (jedno pole), „Are you using remote code?” → **No**,
  zaznacz typy danych z tabeli (PII, Authentication info, Personal communications,
  Website content), trzy certyfikaty, URL polityki prywatności.
- **Test instructions** (jeśli dashboard pokazuje to pole): tekst z
  `CHROMEWEBSTORE.md`.
- **Distribution**: Visibility → **Unlisted**, regiony: wszystkie, za darmo.

## 5. Wysłanie

**Submit for review**. Przy tylu wrażliwych uprawnieniach spodziewaj się
dokładnej recenzji (od kilku dni do kilku tygodni). Odpowiedź przychodzi na
e-mail konta. Odrzucenie: zapisz powód w „Rejection History” w
`CHROMEWEBSTORE.md`, popraw, wyślij ponownie. Po akceptacji (jeśli wybrałeś
publikację ręczną) kliknij **Publish** w ciągu 30 dni.

## 6. Jak użytkownicy instalują

Link do strony rozszerzenia: `https://chromewebstore.google.com/detail/<item id>`
(Unlisted: nie da się go wyszukać, tylko z linku). Działa w Chrome, Chromium,
Brave i Edge (Edge: „Allow extensions from other stores”). Kolejność dla
użytkownika: zainstaluj i uruchom aplikację dj-digger (wersję z `CHROME_STORE_ID`),
zainstaluj rozszerzenie z linku, otwórz jego stronę statusu → „Desktop app link”.
Instalacja przez „Load unpacked” dalej działa (inne ID, z `key`) — to ścieżka dla
deweloperów. Okno „Connect browser” w aplikacji otwiera stronę w sklepie
(`register.CHROME_STORE_URL`), a „Load unpacked” zostało w zwiniętej sekcji „Developer install”.

## 7. Aktualizacje

1. Podbij `"version"` w `dj_digger/webext/extension/manifest.json` (sklep odrzuca
   wersję ≤ opublikowanej).
2. `python3 scripts/package_extension.py`.
3. Dashboard → item → **Package → Upload new package** → nowy zip → uzupełnij
   „Version History” w `CHROMEWEBSTORE.md`, zaktualizuj uzasadnienia, jeśli zmieniły
   się uprawnienia (nowe uprawnienie wyłącza rozszerzenie użytkownikom do czasu
   akceptacji), odśwież zrzuty, jeśli zmienił się wygląd
   (`uv run --extra gui python scripts/store_screenshots.py dj_digger/webext/extension docs/store/screenshots`).
4. **Submit for review**. Chrome aktualizuje zainstalowane rozszerzenia sam (w ciągu
   kilku godzin).

ID sklepu się nie zmienia, więc `CHROME_STORE_ID` ustawia się tylko raz.
Kroki 2–4 robi też automatycznie GitHub Actions przy każdym wydaniu (sekcja 8).

## 8. Automatyczne wydawanie przez GitHub Actions

Workflow `.github/workflows/chrome-web-store.yml` robi kroki 2–4 z sekcji 7 sam:
buduje zip (`scripts/package_extension.py`), pyta sklep o stan (Chrome Web Store
API v2, `fetchStatus`), wgrywa paczkę (`upload`) i wysyła ją do recenzji
(`publish`). Logika jest w `scripts/chrome_web_store.py` (sama biblioteka
standardowa + `openssl` do podpisu JWT). API V1/v1.1 jest wycofane (wsparcie do
15.10.2026), więc używamy V2 i konta usługi (service account), nie osobistego
refresh tokena.

### Jednorazowa konfiguracja

1. **Projekt Google Cloud**: https://console.cloud.google.com → nowy projekt
   (np. `dj-digger-cws`).
2. **Włącz API**: APIs & Services → Library → „Chrome Web Store API” → Enable.
3. **Konto usługi**: IAM & Admin → Service Accounts → Create service account
   (np. `cws-publisher`). Ról IAM nie trzeba nadawać. Na koncie usługi:
   Keys → Add key → Create new key → JSON. Pobrany plik to sekret — nie
   commituj go, po wklejeniu do GitHuba usuń z dysku.
   (Jeśli organizacja blokuje klucze kont usług — polityka
   `iam.disableServiceAccountKeyCreation` — użyj prywatnego projektu.)
4. **Powiąż konto usługi ze sklepem**: Developer Dashboard
   (https://chrome.google.com/webstore/devconsole) → **Account** → sekcja kont
   usług → dodaj e-mail konta usługi
   (`cws-publisher@<projekt>.iam.gserviceaccount.com`). Na wydawcę przypada
   obecnie jedno konto usługi.
5. **Publisher ID**: Developer Dashboard → **Publisher → Settings** → skopiuj
   „Publisher ID”.
6. **GitHub**: repo → Settings → Environments → **New environment**
   `chrome-web-store`. Zalecane: „Required reviewers” = Ty (każdy upload czeka na
   Twoje zatwierdzenie) i „Deployment branches and tags” ograniczone do tagów
   `v*`/`main`. W tym środowisku dodaj **Environment secrets**:
   - `CWS_SERVICE_ACCOUNT_JSON` — cała zawartość pliku JSON z kroku 3,
   - `CWS_PUBLISHER_ID` — ID z kroku 5.

Bez tych sekretów job kończy się na zielono z komunikatem „skipped” — wydanie
nigdy przez to nie pada. ID rozszerzenia bierze się z
`dj_digger/webext/register.py` (`CHROME_STORE_ID`).

### Jak wydawać

1. Zmieniłeś pliki w `dj_digger/webext/extension/`? Podbij `"version"` w
   `manifest.json` (sklep odrzuca wersję ≤ tej, którą już ma) i dopisz wiersz w
   „Version History” w `CHROMEWEBSTORE.md`.
2. Opublikuj wydanie na GitHubie (zwykła ścieżka, nie prerelease). Workflow
   rusza razem z `publish.yml`: wgrywa zip i wysyła go do recenzji.
3. Workflow nic nie robi (komunikat, status zielony), gdy sklep ma już tę wersję
   lub nowszą (np. wydanie bez zmian w rozszerzeniu) albo gdy poprzednia wersja
   wciąż czeka na recenzję — wtedy poczekaj albo anuluj recenzję w dashboardzie
   i uruchom workflow ręcznie.
4. Nowe uprawnienia, zrzuty ekranu i uzasadnienia nadal zmieniasz ręcznie w
   dashboardzie (API wgrywa tylko paczkę). Zmiana widoczności w dashboardzie
   wymaga jednej ręcznej publikacji, zanim API znów opublikuje.

### Ręczne uruchomienie

Actions → **Publish to Chrome Web Store** → **Run workflow** (gałąź `main`):

- `publish` odznaczone (domyślnie) — tylko upload jako szkic; wyślij do recenzji
  w dashboardzie albo uruchom ponownie z zaznaczonym,
- `publish` zaznaczone — upload i wysłanie do recenzji.

Z terminala: `gh workflow run chrome-web-store.yml -f publish=true`.

## Firefox (później): addons.mozilla.org

Odpowiednik to AMO (https://addons.mozilla.org/developers/): konto Firefox,
bez opłaty. Upload `dist/extension/dj-digger-firefox-<wersja>.zip`; można wybrać
**„On your own”** (niepubliczne: Mozilla tylko podpisuje plik `.xpi`, który
rozdajesz sam) albo listing na AMO. ID zostaje `dj-digger@fbialogrecki.github.io`
(z `browser_specific_settings`), więc host Native Messaging już je akceptuje —
żadna zmiana w aplikacji nie jest potrzebna. AMO wymaga kodu źródłowego tylko dla
kodu minifikowanego/budowanego (tu go nie ma) i ostrzeże o nieznanym w Firefoksie
uprawnieniu `offscreen` (ostrzeżenie, nie błąd).
