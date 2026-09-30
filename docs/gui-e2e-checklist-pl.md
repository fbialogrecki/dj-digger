# GUI dj-digger — ręczna checklista E2E bez zakupów

Stan opracowania: **24.09.2026**, wersja **1.2.0**, `main` **1a7f54f79b0ec30e66ccedfb454a9f2e161677c0**.

To instrukcja testów, **nie raport z ich wykonania**. Checkbox zaznaczaj dopiero po uzyskaniu opisanego wyniku. Przy niepowodzeniu dopisz `FAIL`, przy braku danych/konta/sprzętu `NIE TESTOWANO`, a przy przeszkodzie po stronie serwisu `BLOKADA: powód`. Samo otwarcie okna nie zalicza pobierania, logowania ani eksportu.

Podstawa: odpowiednie sekcje `PROJECT-SPECIFICATION.md`, zapytania do graphify oraz bieżący kod QML, bridge, backendu i wspólnych usług. Graf ma 3723 węzły i pochodzi z 09.09.2026; służył do nawigacji, nie do potwierdzania działania ani kompletności nowszego GUI. Zapytania obejmowały słownik `bridge backend settings download playback export cart local analysis playlist gate soundcloud`. Istotne węzły: `Bridge` (`dj_digger/gui/bridge.py`, graf: `L28`), `Backend` (`dj_digger/gui/backend.py`, graf: `L19`), `ApplicationServices`, `DownloadWorkflow`, `LocalLibrary`, `CartRequest` i `CartPlan`. Bieżące lokalizacje są w źródłach na końcu dokumentu.

Pliki GUI oraz sprawdzone usługi w zainstalowanym pakiecie 1.2.0 są identyczne z tym checkoutem. Opisy działania poniżej wynikają z kodu; nie potwierdzają aktualnej dostępności SoundCloud, bramek ani sklepów.

## Jak przeprowadzić testy

Najwygodniej wykonuj sekcje po kolei. Nazwy menu podaję po angielsku, tak jak w kodzie: `Library`, `Tracks`, `Playback`, `Tools`, `View`, `Settings`, `Help`. Na czas pierwszego przebiegu wybierz `View → Language → English`; polski interfejs sprawdź osobno.

**Granica „bez zakupu”:** wolno sprawdzić produkt, przygotować plan, wpisać cenę i dodać testowy produkt do koszyka Bandcamp. Zatrzymaj się przed `Checkout`, zatwierdzeniem zamówienia, płatnością, zakupem subskrypcji lub podobną akcją. W obecnym GUI `C` pokazuje `Review cart` przed automatycznym dodaniem pozycji Bandcamp, również pojedynczej; dopiero Continue zatwierdza plan. Nawet pozycję za 0 traktuj tak samo: nie finalizuj zamówienia. Po próbach usuń z koszyka wyłącznie pozycje dodane w ramach testu.

Pobieranie darmowego pliku przez bramkę może wysłać jej imię, e-mail lub komentarz. Testy takich bramek wykonuj na własnym adresie i tylko dla akceptowanych przez Ciebie warunków. Test Soundiiz obejmuje wysłanie metadanych i ekran podglądu; zakończ przed płatnym planem lub końcowym transferem, jeżeli nie chcesz modyfikować konta.

Usuwanie i zastępowanie plików testuj **wyłącznie na kopiach przeznaczonych do zniszczenia**. Testy zapisujące statusy i usuwające playlisty wykonuj w profilu testowym.

### Profil testowy na obecnym Linuksie

Zamknij działające okna dj-digger. Uruchamiaj testy poniższą komendą, zawsze z tymi samymi ścieżkami. Zwykły skrót w menu aplikacji otwiera Twój normalny profil.

```bash
mkdir -p "$HOME/dj-digger-e2e"/{profile,media,downloads,exports,summaries}
XDG_DATA_HOME="$HOME/dj-digger-e2e/profile/data" \
XDG_CONFIG_HOME="$HOME/dj-digger-e2e/profile/config" \
XDG_CACHE_HOME="$HOME/dj-digger-e2e/profile/cache" \
XDG_STATE_HOME="$HOME/dj-digger-e2e/profile/state" \
dj-digger-gui
```

W `Settings → Preferences…` ustaw folder pobierania na `~/dj-digger-e2e/downloads`, a foldery skanowania i przypięte na `~/dj-digger-e2e/media`. **Zastąp domyślne foldery skanowania**, bo profil testowy nadal domyślnie wskazuje rzeczywiste Music i Downloads. Eksplorator też może je pokazywać; do prób wybieraj dodany folder testowy. Izolacja XDG nie izoluje kont w Twojej zwykłej przeglądarce ani koszyków na serwerach sklepów.

W polu e-mail domyślne `dj-digger@example.invalid` jest celowo nieprawidłowe. Do zapisu preferencji bez bramek wyczyść to pole; do testów wymagających e-maila podaj własny prawidłowy adres.

### Dane do przygotowania

| Symbol | Co przygotować | Do czego |
| --- | --- | --- |
| P1 | Publiczna playlista SoundCloud z co najmniej 25 dostępnymi utworami, kilkoma typami linków, różnymi długościami i gatunkami | Import, tabela, filtry, otwieranie, odtwarzanie |
| P2 | Druga playlista, zawierająca przynajmniej jeden ten sam utwór co P1 | Wspólny status, zmiana widoku |
| OWN | Własny profil SoundCloud, publiczna i prywatna playlista oraz osobna playlista do zmian | Import profilu, uwierzytelnienie, odświeżanie |
| FREE | Utwór z oficjalnym darmowym pobieraniem SoundCloud; osobny link do rzeczywistego pliku udostępnionego przez autora | Pobieranie przez konto i bez konta |
| GATE | Po jednym dostępnym przykładzie bramki Hypeddit, bramki e-mail, bramki z ręcznym krokiem/CAPTCHA oraz strony będącej tylko hubem sklepów | Automatyczna i ręczna obsługa bramek |
| SHOP | Bandcamp: pojedynczy produkt o stałej cenie, name-your-price, produkt już w koszyku; Beatport: link do utworu i wydania | Koszyk, ceny, ponowienia, playlisty |
| L1 | Kopie 8–15 własnych plików: WAV, AIFF/AIF, FLAC, MP3, M4A z AAC/ALAC, AAC; różne długości, mono/stereo, 44,1/48/96 kHz, 16/24 bit | Lokalne audio, analiza i eksport |
| L2 | Foldery zagnieżdżone, pusty folder, folder ze spacjami i polskimi znakami, ukryty podfolder, plik bez tagów | Eksplorator i nazwy |
| BIG | Folder z ponad 250 poprawnymi plikami audio; mogą to być kopie krótkiego własnego nagrania | Stronicowanie i zakres operacji |
| BAD | Kopia uszkodzonego pliku audio; tekst zapisany jako `.mp3`; folder chwilowo niedostępny | Obsługa błędów |
| DESTROY | Osobny podfolder z kopiami do usunięcia i zastępowania, bez jedynych egzemplarzy nagrań | Destrukcyjne operacje |
| REF | Własne nagranie o znanym tempie i tonacji, krótka cisza oraz utwór z tagami BPM/Key | Analiza, brak wyniku, priorytet metadanych |

Nie każda bramka lub awaria da się znaleźć na żądanie. Brak przykładu oznacz `NIE TESTOWANO`, zamiast uznawać daną ścieżkę za działającą. Nie traktuj HTML w `tests/fixtures/` jako gotowej strony do żywego logowania lub pobierania; wiele fixture wymaga atrap testowych.

Do TABLE-17 możesz zapisać poniższy tekst jako `~/dj-digger-e2e/summaries/literal.json` i zaimportować przez Library → Import saved summary. To sztuczny rekord wyłącznie do sprawdzenia tekstu i importu; adres `example.com` nie jest źródłem audio ani sklepem.

```json
{
  "others": [
    {
      "artist": "Zażółć & TEST",
      "title": "<b>TEST</b> — \"wersja, próbna\"",
      "track_url": "https://example.com/e2e-track",
      "shop_link": "https://example.com/e2e-link"
    }
  ]
}
```

## 1. Uruchomienie i podstawowa obsługa okna

- [ ] **START-01 — Pierwszy start.** Uruchom profil testowy. Poczekaj na koniec `Loading library…`. Oczekiwane: działające menu, pusty stan biblioteki i czytelne wskazanie, jak zacząć; brak zawieszenia.
- [ ] **START-02 — Ponowny start.** Zamknij przez `Library → Quit`, uruchom ponownie tą samą komendą. Oczekiwane: profil otwiera się bez błędu; później powtórz po zapisaniu playlist i ustawień. Nie wymagaj automatycznego odtworzenia ostatnio otwartego widoku.
- [ ] **START-03 — Małe okno.** Zmniejsz do około 760×520, rozwiń menu i otwórz formularz. Oczekiwane: dostępne przyciski zatwierdzania/anulowania, transport i poziome przewijanie tabeli; brak nachodzenia elementów uniemożliwiającego użycie.
- [ ] **START-04 — Rozmiar i podział.** Zmień szerokość panelu bocznego, rozmiar okna, ukryj panel `Ctrl+B`, uruchom ponownie. Oczekiwane: rozmiary i widoczność panelu są zapamiętane.
- [ ] **START-05 — Pusty wybór.** Wczytaj dane, usuń zaznaczenie `Esc`. Oczekiwane: przyciski wymagające utworów są wyłączone; komendy „all visible” mają odrębne pozycje. Sprawdź osobno pustą playlistę i pusty folder.
- [ ] **START-06 — Zamknięcie okna.** Sprawdź przycisk systemowy zamknięcia i `Ctrl+Q`. Oczekiwane: aplikacja kończy pracę, a ponowne uruchomienie działa. Test podczas operacji jest w sekcji 18.

## 2. Preferencje, język, motywy i formularze

- [ ] **SET-01 — Zapis ustawień.** `Settings → Preferences…`: ustaw testowe pobieranie, skanowanie, przypięte foldery, przeglądarkę i komentarze. Zapisz, otwórz ponownie, zrestartuj. Oczekiwane: wartości zachowane; pola wielowierszowe mają po jednej pozycji w wierszu.
- [ ] **SET-02 — Anulowanie.** Zmień kilka pól, wybierz Cancel. Oczekiwane: poprzednie wartości pozostają aktywne także po restarcie.
- [ ] **SET-03 — Błędny e-mail.** Wpisz `abc` lub adres z `.invalid`, spróbuj zapisać. Oczekiwane: błąd w formularzu, zachowane pozostałe wpisane wartości; po poprawieniu zapis działa.
- [ ] **SET-04 — Pusty folder pobierania.** Wyczyść pole i zapisz. Oczekiwane: odmowa zapisu z komunikatem, bez utraty pozostałych pól.
- [ ] **SET-05 — Wybór pliku/folderu.** Sprawdź Browse w preferencjach, imporcie i eksporcie, także z polskimi znakami/spacjami. Oczekiwane: właściwy typ systemowego okna, poprawna ścieżka po zatwierdzeniu, brak zmiany po anulowaniu.
- [ ] **SET-06 — Polski i angielski.** Przełączaj `View → Language`, otwórz wszystkie menu, kontekstowe menu wiersza, formularz, pomoc i błędy. Oczekiwane: tłumaczą się etykiety aplikacji i przyciski; menu nie ucina dłuższych polskich nazw. Teksty dostawców i część raportów usług mogą pozostać angielskie.
- [ ] **SET-07 — Trzy motywy.** W `View → Theme` sprawdź Light, Dark i System. Oczekiwane: czytelne zaznaczenia, pola, placeholdery, checkboxy, nieaktywne pozycje, tooltipy, postęp pobierania i błędy. Przy System zmień motyw pulpitu i zanotuj reakcję.
- [ ] **SET-08 — Trwałość wyglądu.** Ustaw język i motyw, zamknij i otwórz. Oczekiwane: wybór zachowany i oznaczony w menu.
- [ ] **SET-09 — Skróty podczas pisania.** W polu tekstowym wpisz litery `a d g k u s e n p m`, spacje i znaki `[]`. Oczekiwane: wpisywanie nie uruchamia akcji na utworach. Powtórz wewnątrz modalnego formularza.
- [ ] **SET-10 — Zgoda bramek.** Wyłącz i włącz `Allow social actions`, każdorazowo zapisując i otwierając ustawienia ponownie. Oczekiwane: wybór jest zachowany; wpływ na rzeczywistą bramkę sprawdź w GATE-04. Sam checkbox nie dowodzi wykonania ani niewykonania akcji w serwisie.

## 3. Dodawanie źródeł SoundCloud i odświeżanie

- [ ] **SC-01 — Publiczna playlista.** `Library → Add playlist…` / `A` / plus przy PLAYLISTS → wklej P1 → Add. Oczekiwane: zapis w panelu bocznym, tytuł i dostępne utwory; porównaj liczbę i dane z serwisem. Brakujące niedostępne pozycje zanotuj osobno.
- [ ] **SC-02 — Pojedynczy utwór.** Dodaj URL utworu. Oczekiwane: widok zawiera ten utwór i jego linki; można odtworzyć dostępny podgląd.
- [ ] **SC-03 — Różne kolekcje profilu.** Osobno dodaj URL profilu, `/tracks`, `/likes` i `/reposts`. Oczekiwane: każda kolekcja ładuje właściwe utwory; reposty nie są mylone z własnymi uploadami. Zapisz wynik każdej odmiany.
- [ ] **SC-04 — Ponowne dodanie.** Dodaj P1 drugi raz. Oczekiwane: aktualizacja tej samej zapisanej playlisty, bez niezamierzonego nowego duplikatu źródła i bez utraty statusów.
- [ ] **SC-05 — Przełączanie.** Dodaj P2, przechodź P1 → P2 → P1, także szybko podczas ładowania. Oczekiwane: tabela i nagłówek należą do ostatnio wybranego źródła.
- [ ] **SC-06 — Błędny adres.** Spróbuj pustego pola, tekstu niebędącego źródłem, niedostępnego URL i linku spoza obsługiwanych źródeł. Oczekiwane: walidacja lub czytelny błąd, możliwość poprawy, brak uszkodzenia wcześniej zapisanych playlist.
- [ ] **SC-07 — Odświeżenie.** Na własnej testowej playliście zmień zawartość w SoundCloud, wróć do GUI i naciśnij `R`. Oczekiwane: aktualna zawartość i zachowane statusy istniejących utworów.
- [ ] **SC-08 — Usunięcia lokalne a odświeżenie.** Usuń utwór z widoku przez `X`, potem odśwież źródło. Oczekiwane: lokalnie usunięty utwór nie wraca sam; przywróć go osobną komendą z sekcji 6.
- [ ] **SC-09 — Anulowanie importu.** Zacznij dłuższe zbieranie, kliknij Cancel. Oczekiwane: operacja kończy się po zatrzymaniu trwających zadań, GUI odzyskuje gotowość, nie zapisuje niepełnej nowej playlisty jako ukończonej.
- [ ] **SC-10 — Zapisane HTML.** Zapisz stronę playlisty w przeglądarce, w Add playlist wpisz pełną ścieżkę do HTML. Oczekiwane: odczyt utworów obecnych w zapisanym materiale albo wyjaśniony brak danych. HTML może nadal wymagać sieci do uzupełnienia utworów; nie jest gwarancją importu całej prywatnej playlisty.

## 4. Import playlist całego profilu i konto SoundCloud

- [ ] **AUTH-01 — Logowanie.** `Settings → Sign in to SoundCloud` → zaloguj się w otwartej dedykowanej przeglądarce. Oczekiwane: powrót do gotowego GUI i potwierdzenie zalogowania; zweryfikuj sesję przez import własnej prywatnej playlisty lub oficjalny download.
- [ ] **AUTH-02 — Przerwane logowanie.** Rozpocznij logowanie, zamknij jego okno lub anuluj operację w GUI. Oczekiwane: aplikacja nie pozostaje zajęta bez końca i można spróbować ponownie.
- [ ] **AUTH-03 — Publiczny import profilu.** `Library → Import profile playlists…` → własny lub dostępny profil, private wyłączone. Oczekiwane: osobne playlisty w panelu i komunikat z liczbą oraz ścieżką raportu `profile-import-*.json`.
- [ ] **AUTH-04 — Porównanie profilu.** Porównaj zaimportowane publiczne playlisty, kolejność i liczby utworów z profilem. Powtórz import. Oczekiwane: brak powielania tych samych playlist; raport odróżnia sukces od niepełnej odpowiedzi.
- [ ] **AUTH-05 — Prywatne playlisty właściciela.** Zaloguj się jako właściciel OWN i zaznacz `Include private playlists`. Oczekiwane: import własnych prywatnych playlist, o ile API udostępnia komplet danych. Wynik wymaga rzeczywistego konta właściciela.
- [ ] **AUTH-06 — Odmowa prywatnego importu.** Powtórz bez logowania, a potem na cudzym profilu będąc zalogowanym na swoje konto. Oczekiwane: odmowa wymagająca sesji właściciela; brak deklaracji udanego importu prywatnych danych.
- [ ] **AUTH-07 — Nieprawidłowy profil.** W imporcie profilu podaj URL pojedynczego utworu. Oczekiwane: informacja, że źródło nie jest profilem; dotychczasowe playlisty pozostają.
- [ ] **AUTH-08 — Częściowy import i anulowanie.** Anuluj import wielu playlist. Oczekiwane: już zapisane kompletne playlisty mogą pozostać, a stare dane nie są zastępowane niekompletnym zestawem; można uruchomić import ponownie. Nie wymagaj końcowego raportu, jeśli przerwanie nastąpiło przed jego zapisem.
- [ ] **AUTH-09 — Wylogowanie.** `Settings → Sign out…`: raz anuluj, drugi raz potwierdź. Oczekiwane: po anulowaniu sesja działa; po potwierdzeniu chroniona akcja wymaga konta. Przeglądarka może zachować swoje cookies, a token w zmiennej środowiskowej może nadal obowiązywać — odnotuj taki przypadek.
- [ ] **AUTH-10 — Publiczne użycie bez konta.** Po wylogowaniu ponownie dodaj publiczne źródło. Oczekiwane: publiczne zbieranie nie wymaga konta użytkownika.

## 5. Tabela, filtry, zaznaczanie i układ kolumn

- [ ] **TABLE-01 — Pola i oznaczenia.** Na P1 porównaj Artist, Title, Genre, Year, Label, Time i Stores z dostępnymi danymi źródła. Oczekiwane: czas `m:ss`, status w pierwszej kolumnie, sklepy jako etykiety; brak wartości jest dozwolony, gdy źródło jej nie dostarcza.
- [ ] **TABLE-02 — Wyszukiwanie.** `Ctrl+F` lub `/`: wyszukaj wykonawcę, fragment tytułu, dwa słowa, zmień wielkość liter. Oczekiwane: uwzględnione wszystkie słowa, brak rozróżniania wielkości liter; licznik visible/total reaguje.
- [ ] **TABLE-03 — Brak dopasowań.** Wpisz tekst nieobecny w danych. Oczekiwane: komunikat o filtrach, brak starych wierszy; wyczyszczenie przywraca listę.
- [ ] **TABLE-04 — Filtr sklepu.** Wybierz kolejno dostępne sklepy i All stores. Oczekiwane: widoczne tylko pasujące wiersze. Liczby przy sklepach odnoszą się do załadowanego zestawu, nie muszą maleć po samym wpisaniu tekstu.
- [ ] **TABLE-05 — Łączenie filtrów.** Włącz sklep, wyszukiwanie i Hide handled. Oczekiwane: spełnione wszystkie trzy warunki; wyłączenie jednego nie kasuje pozostałych.
- [ ] **TABLE-06 — Esc krok po kroku.** Przy zaznaczeniu, tekście wyszukiwania i filtrach naciskaj Esc. Oczekiwane: kolejno znika zaznaczenie, tekst wyszukiwania, następnie filtr sklepu i Hide handled.
- [ ] **TABLE-07 — Sortowanie.** Kliknij nagłówki Artist, Title, Time, Year, a w lokalnym widoku BPM; kliknij ponownie. Oczekiwane: odwrócony kierunek i strzałka; czas/rok/BPM sortują liczbowo, nie leksykograficznie.
- [ ] **TABLE-08 — Wybór myszą.** Kliknij jeden wiersz, Ctrl+klik inne, Shift+klik dalszy. Oczekiwane: pojedynczy wybór, przełączanie elementów, rozszerzenie zakresu; akcje dotyczą rzeczywiście zaznaczonych utworów.
- [ ] **TABLE-09 — Wybór klawiaturą.** Przesuwaj się strzałkami, rozszerzaj Shift+↑/↓, użyj Ctrl+A przy aktywnym filtrze. Oczekiwane: zaznaczenie widocznych wierszy, bez objęcia ukrytych utworów.
- [ ] **TABLE-10 — Stabilne zaznaczenie.** Zaznacz rozpoznawalny utwór, zmień sortowanie, następnie status. Oczekiwane: operacja nadal dotyczy tego samego utworu, a nie dawnego numeru wiersza. Sprawdź też odfiltrowanie zaznaczonej pozycji.
- [ ] **TABLE-11 — Kopiowanie.** Zaznacz kilka pozycji, `Ctrl+C`, wklej do edytora. Oczekiwane: wykonawca i tytuł odpowiednich utworów, bez utraty polskich znaków. To nie jest skrót kopiowania ścieżki do audio.
- [ ] **TABLE-12 — Szerokości kolumn.** Przeciągnij separator, dwukliknij separator, użyj PPM nagłówka → Fit column / Fit all columns. Oczekiwane: zmiana szerokości bez przypadkowego sortowania; tekst i znaczniki mieszczą się lub można przewijać.
- [ ] **TABLE-13 — Ukrywanie i kolejność.** PPM nagłówka → ukryj kolumnę; przeciągnij nagłówek w inne miejsce. Oczekiwane: dane, sortowanie i szerokości należą do właściwej kolumny. Title nie można ukryć; BPM/Key są dostępne w lokalnym widoku.
- [ ] **TABLE-14 — Reset i restart.** Sprawdź Reset column widths i Reset column order; potem ustaw własny układ i zrestartuj. Oczekiwane: reset działa, a własne szerokości, kolejność i ukryte kolumny są zapamiętane.
- [ ] **TABLE-15 — Menu kontekstowe.** PPM na niezaznaczonym wierszu, potem na wierszu należącym do wielokrotnego zaznaczenia. Oczekiwane: właściwy cel akcji; PPM na zaznaczonym wierszu zachowuje grupę. More actions nie powiela podstawowych przycisków paska.
- [ ] **TABLE-16 — Kompaktowy pasek.** Zmniejsz szerokość panelu utworów. Oczekiwane: ikony zamiast długich etykiet, tooltipy z nazwą i skrótem, dostęp do More actions i filtrów.
- [ ] **TABLE-17 — Tekst specjalny.** Zaimportuj testowy JSON z tytułem zawierającym `<b>TEST</b>`, `&`, cudzysłowy i polskie znaki. Oczekiwane: dosłowny tekst, bez interpretacji HTML, uszkodzenia menu lub niezamierzonej nawigacji.

## 6. Statusy, usuwanie z playlisty i przywracanie

- [ ] **STATE-01 — Cztery stany.** Na testowym utworze użyj U, O, G, K i ponownie U. Oczekiwane: untouched/new, opened po otwarciu linku, got/owned, skipped, ponownie new; liczniki są spójne. O faktycznie otwiera przeglądarkę.
- [ ] **STATE-02 — Status grupy.** Zaznacz kilka utworów o różnych statusach, nadaj im G lub K. Oczekiwane: zmieniają się tylko wybrane, widoczne cele.
- [ ] **STATE-03 — Cofanie.** Po zmianie pojedynczej i grupowej użyj Ctrl+Z. Oczekiwane: przywrócone wcześniejsze statusy wszystkich elementów danej operacji, także gdy wcześniej były różne. Historia cofania dotyczy bieżącej sesji.
- [ ] **STATE-04 — Hide handled.** Włącz H, nadaj G/K widocznej pozycji. Oczekiwane: znika z widoku; U po wyłączeniu H i ponowne H pokazuje ją jako nieobsłużoną.
- [ ] **STATE-05 — Wspólny utwór.** Nadaj status utworowi obecnemu w P1 i P2, przejdź do drugiej playlisty, potem zrestartuj. Oczekiwane: status wspólny dla tożsamości utworu i zachowany na dysku.
- [ ] **STATE-06 — Anulowane usunięcie wiersza.** W playliście SoundCloud naciśnij X lub Delete, anuluj. Oczekiwane: lista bez zmian.
- [ ] **STATE-07 — Usunięcie i przywrócenie.** Potwierdź usunięcie kilku wierszy, zrestartuj, potem `Library → Restore removed tracks`. Oczekiwane: usunięcie dotyczy lokalnego zapisu playlisty, a przywrócenie odzyskuje usunięte pozycje. Ctrl+Z nie służy tu do przywracania.
- [ ] **STATE-08 — Usunięcie całej playlisty.** PPM playlisty → Delete playlist lub Shift+X: najpierw Cancel, potem Delete na playliście testowej. Oczekiwane: po potwierdzeniu znika zapis w panelu; audio na dysku i oryginalna playlista w serwisie nie są kasowane.

## 7. Eksplorator i lokalne playlisty

- [ ] **LOCAL-01 — Dodanie folderu.** Plus przy FOLDERS / Ctrl+O → wybierz L1. Oczekiwane: folder zostaje przypięty i otwarty, nazwy pojawiają się przed zakończeniem uzupełniania metadanych.
- [ ] **LOCAL-02 — Ten sam folder ponownie.** Dodaj L1 drugi raz i użyj Pin folder. Oczekiwane: brak zbędnych duplikatów przypięcia; wpis pozostaje po restarcie.
- [ ] **LOCAL-03 — Drzewo.** Rozwijaj i zwijaj L2. Oczekiwane: wcięcia, strzałki tylko dla widocznych podfolderów, możliwość wyboru folderu zawierającego tylko pliki; ukryte podfoldery nie są wyświetlane.
- [ ] **LOCAL-04 — Pusty/niedostępny folder.** Otwórz pusty folder, potem odłączony lub pozbawiony dostępu testowy folder. Oczekiwane: pusty stan albo jawny błąd dostępu, bez podstawiania zawartości innego folderu.
- [ ] **LOCAL-05 — Właściwe podświetlenie.** Wybierz podfolder, następnie playlistę SoundCloud. Oczekiwane: podświetlony folder odpowiada aktualnemu widokowi, a po wejściu na playlistę jego wybór znika.
- [ ] **LOCAL-06 — Typy plików.** Otwórz L1. Oczekiwane: widoczne rozpoznawane rozszerzenia WAV/AIF/AIFF/MP3/FLAC/FLA/M4A/AAC/MP4; pliki nieaudio nie stają się utworami. OGG i samodzielne `.alac` nie należą do obecnej listy rozszerzeń tego eksploratora.
- [ ] **LOCAL-07 — Stronicowanie.** Otwórz BIG, przejdź Next page i Previous page. Oczekiwane: zakresy po maksymalnie 250 plików, poprawny licznik i wyłączone przyciski na końcach. Wyszukiwanie tabeli obejmuje wczytaną stronę, nie cały folder.
- [ ] **LOCAL-08 — Odświeżenie folderu.** Dodaj lub przenieś testowy plik w menedżerze plików, wróć i naciśnij R. Oczekiwane: tabela odzwierciedla rzeczywistą zawartość.
- [ ] **LOCAL-09 — Zapis lokalnej playlisty.** Zaznacz 2–3 pliki → `Library → Save local playlist…` → nowa nazwa. Oczekiwane: playlista w panelu, możliwość odtworzenia i zachowane BPM/Key po ponownym otwarciu.
- [ ] **LOCAL-10 — Dopisanie do lokalnej playlisty.** Z innego folderu wybierz kolejny plik i zapisz pod tą samą nazwą. Oczekiwane: dopisanie do istniejącej lokalnej playlisty, nie zastąpienie jej zawartości. Powtórne dodanie tego samego pliku może tworzyć kolejne wystąpienie — nie zakładaj deduplikacji.
- [ ] **LOCAL-11 — Kasowanie lokalnej playlisty.** Usuń testową lokalną playlistę przez menu Library lub PPM w panelu. Oczekiwane: znika playlista, pliki pozostają. GUI nie udostępnia obecnie zwykłego Remove from playlist dla pojedynczych pozycji lokalnej playlisty; Delete files to inna, trwała operacja.
- [ ] **LOCAL-12 — Odpięcie.** W Preferences usuń testowy katalog z Pinned folders i zapisz. Oczekiwane: znika dodatkowe przypięcie, folder i pliki pozostają; domyślne korzenie Music/Downloads mogą nadal być widoczne.

## 8. Skanowanie i dopasowanie posiadanych plików

- [ ] **SCAN-01 — Pełne dopasowanie.** W testowym katalogu skanowania umieść kopię nazwaną `Wykonawca - Tytuł.mp3`, zgodną z P1. Otwórz P1 → `Library → Scan local library`. Oczekiwane: pewne dopasowanie przypisuje plik i może oznaczyć got; odtwarzanie korzysta z pliku lokalnego.
- [ ] **SCAN-02 — Sam tytuł.** Użyj unikalnej nazwy zgodnej tylko z tytułem, co najmniej sześć znormalizowanych znaków. Oczekiwane: brak `got`; ścieżka widoczna, a Play odtwarza znaleziony plik lokalnie. Download nadal pobiera źródło zdalne i nie oznacza utworu jako posiadanego.
- [ ] **SCAN-03 — Niejednoznaczność.** Umieść dwie podobnie nazwane wersje utworu. Oczekiwane: brak arbitralnego wyboru niejednoznacznej wersji jako pewnego dopasowania.
- [ ] **SCAN-04 — Skasowany plik testowy.** Po poprawnym dopasowaniu usuń kopię i wykonaj pełny skan czytelnego folderu. Oczekiwane: nieaktualne powiązanie zostaje wycofane; ręcznie nadany got nie powinien być kasowany jak status pochodzący wyłącznie z pliku.
- [ ] **SCAN-05 — Niedostępny nośnik.** Po dopasowaniu odłącz testowy nośnik lub zablokuj dostęp do folderu i skanuj. Oczekiwane: brak traktowania samej niedostępności jako dowodu usunięcia wszystkich plików. Przywróć dostęp i sprawdź ponownie.
- [ ] **SCAN-06 — Anulowanie i UI.** Zacznij skan dużego testowego drzewa, zmieniaj widoki i anuluj. Oczekiwane: responsywne okno, zachowane już zapisane wyniki; późniejszy skan może dokończyć pracę.

## 9. Odtwarzanie, waveform i sterowanie

- [ ] **PLAY-01 — Lokalny plik.** Zaznacz plik L1, Space. Oczekiwane: słyszalny dźwięk, tytuł, czas, znacznik odtwarzanego wiersza i waveform. Sam ruch licznika nie wystarcza.
- [ ] **PLAY-02 — Podgląd SoundCloud.** Zaznacz dostępny zdalny utwór bez lokalnego dopasowania, uruchom Space. Oczekiwane: słyszalny podgląd; test nie powinien tworzyć pełnego pobrania i oznaczać got.
- [ ] **PLAY-03 — Wszystkie wejścia.** Powtórz play/pause przez Space, przycisk transportu, Playback i dwuklik wiersza. Oczekiwane: spójny cel i stan, bez nakładających się strumieni.
- [ ] **PLAY-04 — Inny zaznaczony utwór.** Podczas A zaznacz B i naciśnij Space, potem ponownie. Oczekiwane: najpierw start B, potem jego pauza; podpis/ikona przycisku odpowiadają celowi.
- [ ] **PLAY-05 — Bez zaznaczenia.** Podczas odtwarzania wyczyść wybór Esc i użyj Space. Oczekiwane: pauza/wznowienie już załadowanego utworu. Sprawdź również po kliknięciu panelu bocznego.
- [ ] **PLAY-06 — Pauza przed waveformem.** Otwórz dłuższy lokalny plik, szybko zapauzuj. Oczekiwane: waveform może dokończyć generowanie także w pauzie; dźwięk nie wznawia się sam.
- [ ] **PLAY-07 — Przewijanie.** Użyj `[` i `]`, kliknij waveform, przeciągnij do nowego miejsca. Oczekiwane: dźwięk i licznik odpowiadają pozycji; hover pokazuje czas; cel przeciągania nie skacze od razu do starej pozycji.
- [ ] **PLAY-08 — Szybkie seeki i granice.** Kilka razy szybko przewiń, także przed początek i poza koniec. Oczekiwane: ostatecznie liczy się ostatni cel, przesunięcia sumują się, pozycja pozostaje w granicach nagrania i UI reaguje.
- [ ] **PLAY-09 — Następny/poprzedni.** Na krótkiej liście użyj N/P i przycisków; pozwól jednemu utworowi skończyć się naturalnie. Oczekiwane: poprawne przejście w kolejności widoku z chwili uruchomienia; na końcu brak zapętlenia nieistniejącego utworu.
- [ ] **PLAY-10 — Głośność i mute.** Suwak, `-`, `=`, M, ponownie M. Oczekiwane: słyszalna zmiana, wyciszenie i powrót; poziom nie wychodzi poza 0–100%. Po restarcie poziom zachowany, samo mute nie musi być zachowane.
- [ ] **PLAY-11 — Stop.** Ctrl+W lub Stop podczas grania i generowania waveformu. Oczekiwane: cisza, zwolniony utwór, kompaktowy transport; opóźniony waveform nie przywraca zakończonego odtwarzania.
- [ ] **PLAY-12 — Szybka zmiana utworu/widoku.** Uruchamiaj kolejno kilka plików, przełącz playlistę/folder. Oczekiwane: brak nakładania audio i waveformu innego utworu; błędy nowego źródła nie niszczą stanu nadal działającego odtwarzacza.
- [ ] **PLAY-13 — Uszkodzone/niedostępne audio.** Spróbuj BAD oraz zdalnego utworu bez obsługiwanego podglądu. Oczekiwane: informacja o błędzie, brak fałszywego sukcesu; następny poprawny plik daje się odtworzyć. AAC/Opus HLS, zaszyfrowane streamy i snippet-only nie są gwarantowanym pełnym preview.
- [ ] **PLAY-14 — Praca równoległa.** Podczas grania filtruj, zmieniaj motyw, statusy, otwieraj menu, rozpocznij analizę lub download. Oczekiwane: sterowanie pozostaje dostępne, brak niezamierzonych przerw.
- [ ] **PLAY-15 — Urządzenie audio.** Zmień wyjście w ustawieniach systemu, podłącz/odłącz słuchawki. Oczekiwane do oceny: sensowne odtwarzanie albo czytelny błąd i możliwość odzyskania działania. GUI nie ma osobnego wyboru urządzenia; zachowanie sprzętu wymaga ręcznej weryfikacji.

## 10. Otwieranie linków

- [ ] **LINK-01 — Pojedynczy link.** Na P1 wybierz utwór → O lub Enter. Oczekiwane: jeden wybrany adres w skonfigurowanej przeglądarce oraz właściwy status opened, bez automatycznego finalizowania czegokolwiek.
- [ ] **LINK-02 — Sklep wybrany filtrem.** Na utworze mającym dwa sklepy wybierz filtr jednego z nich i O. Oczekiwane: otwiera się ten sklep. To zachowanie dotyczy Open links; nie zakładaj tego samego dla C.
- [ ] **LINK-03 — Grupa i wszystkie widoczne.** Porównaj O przy kilku zaznaczonych i Shift+O po zawężeniu filtrem. Oczekiwane: odpowiednio wybrane lub wszystkie widoczne pozycje, maksymalnie jeden kwalifikujący się link na utwór.
- [ ] **LINK-04 — Potwierdzenie ponad 20.** Na ponad 20 kwalifikujących się pozycjach użyj Shift+O. Oczekiwane: lista adresów do zatwierdzenia. Wybierz Cancel i sprawdź, że nie otwarto kart. Do sprawdzenia samego progu nie musisz uruchamiać całej grupy.
- [ ] **LINK-05 — Brak sklepu.** Otwórz utwór no-link. Oczekiwane: jego strona SoundCloud, jeżeli jest dostępnym linkiem zapasowym, bez wymyślonego adresu zakupu.

## 11. Pobieranie plików i kopiowanie lokalnych

- [ ] **DL-01 — Bezpośredni darmowy plik.** Na odpowiednim FREE użyj D. Oczekiwane: plik w testowym folderze pobierania/podfolderze playlisty, poprawny dźwięk przy późniejszym odtworzeniu i got dopiero po ukończeniu.
- [ ] **DL-02 — Oficjalny download SoundCloud.** Wybierz utwór z włączonym pobieraniem autora; sprawdź przed logowaniem i po zalogowaniu. Oczekiwane: wymagane konto jest zgłoszone, po spełnieniu warunku pobranie może zostać ponowione. Nie zastępuj tego testu nagrywaniem podglądu.
- [ ] **DL-03 — Folder playlisty i nazwy.** Pobierz z playlisty o nazwie zawierającej spacje/polskie znaki; jeżeli masz własną, sprawdź też znaki niedozwolone w nazwach plików. Oczekiwane: bezpieczna nazwa podfolderu, bez wyjścia poza cel i bez wielokrotnego dodawania tej samej nazwy folderu.
- [ ] **DL-04 — Nazwa już zajęta.** Umieść w celu testowy plik o przewidywanej nazwie, następnie pobierz inny plik o tej nazwie. Oczekiwane: brak nadpisania poprzedniego pliku, nowa unikalna nazwa. Powtórny D dla już dopasowanego pliku może jedynie potwierdzić lokalne posiadanie.
- [ ] **DL-05 — Kopia pliku lokalnego.** W lokalnym widoku wybierz plik spoza docelowego folderu, D. Oczekiwane: skopiowany, odtwarzalny plik w celu; źródło pozostaje. Powtórz, gdy plik jest już w celu: brak zbędnej ponownej kopii.
- [ ] **DL-06 — Zaznaczenie kontra cały widok.** Użyj D na dwóch pozycjach, potem Shift+D na przefiltrowanym widoku. Oczekiwane: właściwy zakres; pozycje bez możliwości pobrania nie są automatycznie kupowane.
- [ ] **DL-07 — Pasek postępu.** Pobierz większy dozwolony plik, przewijaj tabelę i zmieniaj szerokości kolumn. Oczekiwane: postęp przypisany do właściwego utworu, czytelny procent i zaznaczenie; po ukończeniu znika stan „w trakcie”.
- [ ] **DL-08 — Anulowanie grupy.** Anuluj, gdy część plików już się pobrała. Oczekiwane: ukończone pozostają poprawne, nieukończone nie są przedstawione jako got; po zakończeniu porządkowania można pobierać ponownie.
- [ ] **DL-09 — Błąd sieci/miejsca/dostępu.** Na testowym celu przerwij sieć lub użyj folderu bez zapisu. Oczekiwane: błąd w Messages/Diagnostics, brak uszkodzonego pliku pod finalną nazwą i brak fałszywego got. Brak miejsca testuj tylko na małym, osobnym nośniku/obrazie, nie przez zapełnianie systemowego dysku.
- [ ] **DL-10 — HTML zamiast pliku.** Jeżeli masz kontrolowany adres download zwracający stronę błędu zamiast audio, spróbuj D. Oczekiwane: odrzucenie odpowiedzi, nie zapis strony jako działającego MP3. Bez takiego źródła oznacz NIE TESTOWANO.
- [ ] **DL-11 — Zmiana widoku podczas pobierania.** Rozpocznij w P1 i przejdź do P2. Oczekiwane: operacja zachowuje pierwotny cel i folder; postęp P1 nie pojawia się na obcych wierszach P2. Po powrocie/restartcie ukończone pliki i statusy są dostępne.
- [ ] **DL-12 — Niepełny wynik grupy.** Użyj grupy z działającym i niedziałającym źródłem. Oczekiwane: sukces jednego nie ukrywa awarii drugiego; porównaj dysk, statusy i Messages. Obecny GUI nie pokazuje pełnego zbiorczego raportu wszystkich zdarzeń DownloadWorkflow.

## 12. Bramki i huby — bez zakupu

- [ ] **GATE-01 — Sam hub sklepów.** Dodaj źródło prowadzące do strony z linkami do sklepów, bez downloadu. Oczekiwane: rozpoznane linki albo wyjaśniony brak możliwości pobrania; sam hub nie staje się plikiem audio.
- [ ] **GATE-02 — Prosty Hypeddit.** D na bramce bez ręcznego kroku. Oczekiwane: dozwolone rozwiązanie bramki, plik przechodzi zwykłą walidację i uzyskuje got; podgląd HTML nie jest wynikiem.
- [ ] **GATE-03 — Brak profilu i e-mail.** Na bramce wymagającej profilu rozpocznij bez prawdziwego e-maila, anuluj ustawienia; potem powtórz z własnym poprawnym adresem. Oczekiwane: brak wysyłki placeholdera; po prawidłowym profilu próba może być wznowiona.
- [ ] **GATE-04 — Wyłączone kroki społecznościowe.** Wyłącz zgodę, uruchom bramkę deklarującą takie kroki. Oczekiwane: automatyka nie traktuje starej zgody jako aktualnej; pojawia się odmowa albo ręczna ścieżka. Nie wykonuj ręcznie działań, których nie chcesz.
- [ ] **GATE-05 — CAPTCHA/nieobsługiwany krok.** Użyj bramki wymagającej człowieka. Oczekiwane: dedykowane widoczne okno, możliwość samodzielnego ukończenia lub przerwania, bez pozorowania rozwiązanej CAPTCHA.
- [ ] **GATE-06 — Wiele kart ręcznych.** Uruchom kilka bramek z ręcznymi krokami. Ukończ jedną, zamknij drugą, pozostaw trzecią. Oczekiwane: zamknięcie jednej nie niszczy pozostałych; pobrany plik zachowuje sukces, zamknięta niedokończona ścieżka ma właściwy wynik.
- [ ] **GATE-07 — Popup i karta nadrzędna.** Jeśli bramka otworzyła własny popup, zamknij kartę nadrzędną i spróbuj ukończyć w popupie. Oczekiwane: wynik należy do właściwego utworu, o ile popup nadal jest częścią tego procesu; brak przejmowania pobrań z obcych kart.
- [ ] **GATE-08 — Anulowanie ręcznej partii.** Cancel w GUI podczas ręcznego okna. Oczekiwane: ukończone pliki zachowane; reszta anulowana, a nie uznana za udane pobranie; okno/operacja daje się zakończyć.
- [ ] **GATE-09 — Limit ręcznej partii.** Jeśli masz ponad osiem bramek wymagających przeglądarki, uruchom grupę. Oczekiwane: najwyżej osiem trafi do tej partii przeglądarkowej; pozostałe nie uzyskują fałszywego got. GUI może nie pokazać osobnego licznika odroczonych.
- [ ] **GATE-10 — Pozostałe resolvery.** Osobno sprawdź dostępne darmowe przykłady ToneDen, Droploud, GateRush, MediaFire, Dropbox i Google Drive. Dla każdego zapisz: plik pobrany i odtwarzalny / blokada dostawcy / błąd. GateRush może przesłać e-mail i włączony komentarz.
- [ ] **GATE-11 — Zmieniona/niedostępna bramka.** Użyj wygasłego lub odrzuconego źródła. Oczekiwane: wyjaśnienie awarii lub ręczna ścieżka, nigdy „sukces” bez pliku. Nie uznawaj pojedynczego błędu antybotowego za dowód awarii wszystkich bramek.
- [ ] **GATE-12 — Powrót z bramki.** Po darmowym pobraniu wróć do GUI, odtwórz wynik lokalnie, uruchom aplikację ponownie. Oczekiwane: trwały status i dostępna lokalna ścieżka, bez potrzeby ponownego przechodzenia bramki dla tej kopii.

## 13. BPM, tonacja i ręczne poprawki

- [ ] **ANA-01 — Tagi przed analizą.** Otwórz plik REF z BPM/Key w tagach i `Edit BPM / key…` / E. Oczekiwane: wartości z pliku oraz informacja o źródle każdego pola. Samo otwarcie folderu nie rozpoczyna analizy BPM.
- [ ] **ANA-02 — Analiza jednego.** Wybierz lokalny plik → Analyze BPM / key. Oczekiwane: wynik w tabeli, krótki komunikat o liczbie tonacji/braków/błędów, możliwość odtworzenia pliku.
- [ ] **ANA-03 — Grupa.** Zaznacz kilka plików z różnych formatów i analizuj. Oczekiwane: niezależne wyniki i brak nadpisywania ręcznych wartości; błędny plik nie kasuje wyników poprawnych.
- [ ] **ANA-04 — Cały folder.** W BIG ustaw filtr i zaznacz jeden utwór, wybierz Tools → Analyze folder. Oczekiwane: wszystkie bezpośrednie pliki audio w folderze, również poza stroną i filtrem; bez rekursji do podfolderów.
- [ ] **ANA-05 — Jakość oszacowania.** Porównaj REF ze znanym BPM/tonacją. Zapisz różnicę, także rozpoznanie połowy/podwójnego tempa. To osobna ocena jakości estymacji; aplikacja nie deklaruje zweryfikowanej dokładności ani procentowej pewności.
- [ ] **ANA-06 — Cisza i brak jednoznacznej tonacji.** Analizuj ciszę i materiał bez wyraźnego rytmu/tonacji. Oczekiwane: może pozostać brak wyniku z przyczyną w raporcie; brak tonacji nie jest automatycznie błędem przetwarzania.
- [ ] **ANA-07 — Ręczna zmiana.** E → ustaw BPM i tonację z listy → Save. Oczekiwane: tabela natychmiast pokazuje nowe wartości; formularz po ponownym otwarciu oznacza Manual. Tonacje mają etykiety klasyczne i Camelot.
- [ ] **ANA-08 — ×2, ÷2 i walidacja.** Sprawdź przyciski mnożenia/dzielenia. Spróbuj BPM 0, ponad 999 i wartości nienumerycznej. Oczekiwane: poprawne przeliczenie oraz odmowa nieprawidłowej wartości z zachowaniem formularza.
- [ ] **ANA-09 — Wyczyść ręczne wartości.** Clear manual values → Save. Oczekiwane: powrót do aktualnej analizy, następnie tagów lub braku wartości, zależnie od dostępnych danych.
- [ ] **ANA-10 — Trwałość i ponowna analiza.** Zapisz ręczne wartości, uruchom analizę ponownie, zamknij i otwórz folder oraz aplikację. Oczekiwane: ręczne wartości mają pierwszeństwo i nie znikają; automatyczne wyniki też pozostają dostępne bez obowiązkowej reanalizy.
- [ ] **ANA-11 — Niezmieniony plik audio.** Przed analizą i ręczną edycją zapisz sumę pliku narzędziem systemowym, porównaj po. Oczekiwane: oryginalne audio i tagi nie są przepisywane; wyniki aplikacji trafiają do jej bazy.
- [ ] **ANA-12 — Anulowanie i raport.** Anuluj analizę grupy po kilku wynikach. Oczekiwane: zakończone wiersze pozostają, procesy analizy się kończą; w testowym katalogu logów `last-analysis.jsonl` opisuje wynik/terminację. Po anulowaniu da się rozpocząć kolejną operację.
- [ ] **ANA-13 — Zmiana zawartości pliku.** Na kopii zmień rzeczywistą zawartość audio poza programem, odśwież folder i analizuj. Oczekiwane: stara analiza innej sygnatury nie jest traktowana jako aktualna. Nie wymagaj zachowania tożsamości przy każdej metodzie zastąpienia pliku przez zewnętrzny edytor.

## 14. Eksport audio, wznawianie i zastępowanie kopii

- [ ] **EXP-01 — Plan bez wykonania.** Zaznacz lokalne pliki → Tools → Export audio… → testowy cel, Copy, WAV, 24 bit/48 kHz. Oczekiwane: drugi ekran Review export z operacjami i ścieżkami. Anuluj: źródła i docelowe pliki audio pozostają bez zmian.
- [ ] **EXP-02 — Kopiowanie do nowego folderu.** Powtórz EXP-01 i zatwierdź. Oczekiwane: kompletny odtwarzalny zestaw w nowym unikalnym folderze, także dla pozycji niewymagających konwersji; źródła nadal istnieją.
- [ ] **EXP-03 — Trzy formaty docelowe.** Osobno wykonaj plan WAV, AIFF i FLAC na kopiach. Oczekiwane: plan wyjaśnia copy/convert; potrzebne konwersje dają zgodny, odtwarzalny wynik. Wybór WAV nie znaczy, że każdy zgodny MP3/AAC zostanie zamieniony na WAV.
- [ ] **EXP-04 — Limity jakości.** Porównaj 16/24 bit i limity 44,1/48/88,2/96 kHz na odpowiednich źródłach. Oczekiwane: limit jest maksimum, nie poleceniem sztucznego zwiększenia jakości; parametry sprawdź np. przez ffprobe lub właściwości pliku.
- [ ] **EXP-05 — Zgodny plik stratny.** Eksportuj zgodny MP3/AAC do profilu WAV/AIFF, a zgodny FLAC/ALAC do profilu FLAC. Oczekiwane: tam, gdzie reguły pozwalają, plik jest zachowany zamiast zbędnej ponownej kompresji; potwierdź plan i sumę kopii.
- [ ] **EXP-06 — Zakres zaznaczenia.** Wybierz kilka plików, eksportuj. Oczekiwane: tylko wybrane cele. Następnie usuń zaznaczenie w folderze i eksportuj: obejmowane są pasujące pliki z całego folderu, również poza stroną.
- [ ] **EXP-07 — Filtr i rekursja.** Bez zaznaczenia ustaw wyszukiwanie/Hide handled, eksportuj bez rekursji i osobno z Include subfolders. Oczekiwane: właściwy zakres zgodny z planem; dopiero rekursja włącza podfoldery. Przed wykonaniem przeczytaj listę ścieżek.
- [ ] **EXP-08 — Nazwy i kolizje.** Powtórz eksport do tego samego bazowego celu; użyj nazw ze spacjami i Unicode. Oczekiwane: nowy bezkolizyjny zestaw, brak nadpisania obcych plików.
- [ ] **EXP-09 — Nieobsługiwane/uszkodzone wejście.** Dołącz BAD lub plik o parametrach spoza obsługi. Oczekiwane: jawna odmowa/wyjątek w planie lub wyniku; brak przedstawienia uszkodzonego wyjścia jako zweryfikowanego sukcesu.
- [ ] **EXP-10 — Anuluj i wznów kopię.** Zacznij większy eksport Copy, anuluj po części wyników, wybierz Tools → Resume export. Oczekiwane: potwierdzenie wznowienia i dokończenie ostatniej niezakończonej operacji z dziennika, bez ponownego niszczenia ukończonych wyników.
- [ ] **EXP-11 — Wznowienie po restarcie.** Powtórz przerwanie Copy, zamknij aplikację, otwórz ten sam profil i Resume export. Oczekiwane: wznowienie nadal dostępne. Przy braku niedokończonego eksportu komunikat `No unfinished folder exports`.
- [ ] **EXP-12 — Ostrzeżenie zastępowania.** Wyłącznie DESTROY: wybierz Replace originals. Oczekiwane: plan z ostrzeżeniem i przyciskiem Replace; Cancel nie upoważnia do zastąpienia. Kolejne nowe okno eksportu wraca do Copy.
- [ ] **EXP-13 — Faktyczne zastąpienie kopii.** Na jednym pliku DESTROY wymagającym konwersji zatwierdź Replace. Oczekiwane: Replace nie wymaga folderu docelowego; zweryfikowany wynik, zgodny lokalny rekord/playlisty i brak trwałej kopii zapasowej po sukcesie. Oryginał tej testowej kopii może zostać trwale usunięty.
- [ ] **EXP-14 — Ochrona odtwarzanego pliku.** Włącz plik DESTROY, spróbuj jego zastąpienia. Oczekiwane: odmowa dla załadowanego/przygotowanego audio; po Stop i ponownym wyborze operacja może być wykonana.
- [ ] **EXP-15 — Zmiana pliku po planie.** Wyświetl Review export, zmień kopię źródła w menedżerze/edytorze, zatwierdź. Oczekiwane: wykrycie nieaktualnego źródła, bez ślepego wykonania starego planu; czerwony baner podaje nazwę pliku i przyczynę.
- [ ] **EXP-16 — Metadane i odsłuch.** Porównaj długość, kanały, głośność, początek/koniec, tagi oraz okładkę tam, gdzie format ją obsługuje. Oczekiwane: brak niezamierzonej normalizacji/downmixu; pominięte metadane nie są obiecywane jako zachowane. Nie uznawaj wyboru profilu za dowód działania na fizycznym CDJ.

## 15. Trwałe usuwanie plików — tylko DESTROY

- [ ] **DELETE-01 — Lista i Cancel.** W lokalnym widoku zaznacz kopie → Delete files. Oczekiwane: pełne ścieżki i jawne potwierdzenie trwałego usunięcia. Cancel/Esc pozostawia pliki.
- [ ] **DELETE-02 — Usuń jedną kopię.** Potwierdź. Oczekiwane: konkretny plik znika z dysku, folder po odświeżeniu jest spójny; dane innych plików są nietknięte. To nie jest przeniesienie do kosza systemowego.
- [ ] **DELETE-03 — Odtwarzany/przygotowany plik.** Spróbuj skasować aktualnie załadowany plik i — jeśli jest już przygotowany — następny. Oczekiwane: odmowa z prośbą o zamknięcie odtwarzacza. Po Stop wybierz plik ponownie.
- [ ] **DELETE-04 — Zmieniony plik i symlink.** Na kopiach sprawdź zmianę pliku po otwarciu potwierdzenia oraz wybranie dowiązania symbolicznego. Oczekiwane: symlink obok oryginału nie tworzy osobnego wiersza; zaznaczony symlink odrzucony przed potwierdzeniem; zmiana któregokolwiek pliku po potwierdzeniu zatrzymuje całą operację, nic nie zostaje usunięte.
- [ ] **DELETE-05 — Grupa i referencje.** Usuń kilka kopii występujących w lokalnej playliście, odśwież/otwórz ją ponownie. Oczekiwane: brak udawania dostępnego audio; zachowanie odniesień i ręcznych metadanych nie oznacza, że usunięty plik nadal istnieje.

## 16. Eksport linków i import JSON

- [ ] **DATA-01 — JSON wybranych.** Zaznacz część P1 → Tools → Export links / Shift+E → JSON → nowa ścieżka. Oczekiwane: poprawny plik z wybranymi utworami i ich linkami pogrupowanymi kategoriami.
- [ ] **DATA-02 — Cały widoczny zestaw.** Wyczyść zaznaczenie, ustaw filtr, eksportuj. Oczekiwane: eksport widocznych utworów. Pojedynczy utwór może dać kilka rekordów linków, więc liczba rekordów nie musi równać się liczbie utworów.
- [ ] **DATA-03 — CSV.** Wyeksportuj CSV i otwórz jako UTF-8 w arkuszu/edytorze. Oczekiwane: kolumny `category, artist, title, track_url, shop_link, bpm, key, release_year, label`, poprawne znaki i cytowanie przecinków/cudzysłowów.
- [ ] **DATA-04 — Nadpisanie pliku.** Eksportuj pod istniejącą testową nazwą; najpierw anuluj potwierdzenie Replace, potem je zatwierdź. Oczekiwane: anulowanie zachowuje stary plik, zatwierdzenie zapisuje nowy.
- [ ] **DATA-05 — Import JSON.** `Library → Import saved summary…` → JSON z DATA-01. Oczekiwane: tabela zgrupowana według utworów i dostępne linki. W GUI jest to widok wczytanego pliku; nie oczekuj automatycznego zapisania nowej playlisty w panelu ani odtworzenia tego widoku po restarcie.
- [ ] **DATA-06 — CSV i YAML na wejściu.** Spróbuj zaimportować CSV i YAML. Oczekiwane: etykieta `JSON file` / `Plik JSON` i filtr JSON; CSV odrzucony komunikatem, że eksport CSV jest tylko wyjściowy (output-only); YAML z komunikatem o starym formacie.
- [ ] **DATA-07 — Błędny JSON.** Na kopii eksportu sprawdź ucięty JSON, brak `track_url`, kategorię niebędącą listą, nieistniejącą ścieżkę. Oczekiwane: czytelny błąd bez utraty biblioteki; poprawny plik nadal można zaimportować.
- [ ] **DATA-08 — Niedozwolony schemat URL.** W kopii JSON zmień jeden `shop_link` na `javascript:alert(1)` lub `file:///tmp/test`. Oczekiwane: odrzucenie przy imporcie, bez wykonania czy otwarcia tego adresu.

## 17. Bandcamp, Beatport i Soundiiz — zatrzymanie przed zakupem

Przed testem zapisz obecny stan koszyka. Nie używaj produktów, których przypadkowe dodanie utrudni Ci rozpoznanie wcześniejszej zawartości. Celem jest koszyk/plan i ich poprawne wyniki; **nigdy finalizacja zamówienia**.

- [ ] **SHOP-01 — Konta sklepowe.** Settings → Store accounts. Oczekiwane: dedykowana przeglądarka tylko dla Bandcamp, możliwość logowania i zamknięcia procesu; Cancel kończy się neutralnym „Cancelled”, nie czerwonym błędem. Bez Chromium w profilu: pytanie o jednorazowe pobranie, po zgodzie ponowienie. Bez samoczynnego zakupu; nie wymagaj logowania ani koszyka Beatport.
- [ ] **SHOP-02 — Pojedynczy Bandcamp.** Zaznacz jeden SHOP o stałej cenie, C → sprawdź Review cart → Continue. Oczekiwane: poprawny produkt i zweryfikowany wynik dodania albo wyraźna odmowa. Sprawdź dokładny utwór/wydanie w koszyku, **zatrzymaj się przed Checkout**.
- [ ] **SHOP-03 — Już w koszyku.** Ponów C na tym samym produkcie. Oczekiwane: rozpoznanie istniejącej pozycji, bez niekontrolowanych ponownych dodawań i bez uznawania produktu za pobrane audio.
- [ ] **SHOP-04 — Plan grupy i deselect.** Zaznacz kilka produktów → C → Review cart; odznacz jeden, zatwierdź pozostałe. Oczekiwane: odznaczony nie jest dodawany; sprawdź wyniki i rzeczywisty koszyk, bez płatności.
- [ ] **SHOP-05 — Anulowanie planu.** Na grupie otwórz Review cart i Cancel. Oczekiwane: nie wykonuje zatwierdzanych dopiero w tym planie zmian koszyka.
- [ ] **SHOP-06 — Cena elastyczna.** Wybierz produkt name-your-price. Sprawdź minimum, własną wyższą cenę zgodną z krokiem sklepu oraz cenę poniżej minimum/błędny tekst. Oczekiwane: walidacja zachowuje formularz; zaakceptowana cena jest respektowana albo dodanie zostaje wstrzymane. Nie realizuj nawet zamówienia za 0.
- [ ] **SHOP-07 — Właściwy produkt.** Użyj linku do albumu/wydania z kilkoma wersjami utworu. Oczekiwane: brak samowolnego dodania niepewnego remiksu lub całego wydania jako pewnego pojedynczego utworu; przypadek niejednoznaczny powinien zostać wyjaśniony.
- [ ] **SHOP-08 — Zmiana ceny/niedostępność.** Jeśli trafisz na produkt niedostępny lub zmieniony od preflight, sprawdź wynik. Oczekiwane: zatrzymanie/odmowa zamiast cichej akceptacji nowej tożsamości lub ceny. Bez kontrolowanego przypadku oznacz NIE TESTOWANO.
- [ ] **SHOP-09 — Ręczne dokończenie.** Jeśli pojawi się Finish in browser, wykonaj wyłącznie Add to cart na właściwej pozycji, wróć do dialogu. Oczekiwane: ponowne sprawdzenie i wynik ręcznego dodania; nigdy Checkout. Anuluj drugi taki przypadek, aby sprawdzić wyjście.
- [ ] **SHOP-10 — Ponowienie bezpiecznych awarii.** Jeśli Cart results oferuje Retry failed items, użyj go po usunięciu przyczyny. Oczekiwane: ponawiane wyłącznie bezpieczne awarie, a nie niepewne kliknięcia, które mogły już dodać produkt.
- [ ] **SHOP-11 — Anulowanie podczas grupy.** Cancel po rozpoczęciu pracy. Oczekiwane: kliknięte już dodanie może dokończyć sprawdzanie; wynik nie obiecuje cofnięcia koszyka. Porównaj faktyczny koszyk, potem usuń testowe pozycje.
- [ ] **SHOP-12 — Widoczne cele i filtr.** Użyj Shift+C po zawężeniu listy. Oczekiwane: zakres wierszy zgodny z widokiem. **Uwaga z kodu:** C zbiera linki Bandcamp i Beatport z tych wierszy, a nie przekazuje filtra sklepu jak O; przy wierszu z oboma sklepami filtr Beatport nie gwarantuje pominięcia Bandcamp. Do testu wyłącznie Beatport wybierz wiersze zawierające tylko ten sklep.
- [ ] **SHOP-13 — Dokładny Beatport track.** Na wierszu z samym linkiem `/track/.../id` użyj C. Oczekiwane: pozycja gotowa do playlisty, bez automatycznej modyfikacji koszyka Beatport.
- [ ] **SHOP-14 — Beatport release/fallback.** Powtórz z linkiem wydania. Oczekiwane: dokładne dopasowanie, jeśli uda się je potwierdzić, albo wpis oparty na oczyszczonych artist/title przy blokadzie dostawcy; brak wymyślonego exact track URL.
- [ ] **SHOP-15 — Podgląd wyników i koszyka.** W Cart results wybierz Show carts in browser, potem Close. Oczekiwane: właściwe okno i powrót do GUI; brak okna nie może wymazać wcześniejszych zweryfikowanych dodań. Obecny dialog może pokazywać surowe angielskie kody statusów.
- [ ] **SHOP-16 — Jawne wysłanie do Soundiiz.** Dla gotowych pozycji Beatport wybierz `Send Beatport playlist metadata to Soundiiz` → Continue. Oczekiwane: nowy lokalny plik tekstowy; przy udanej odpowiedzi metadane/URL-e w schowku i strona podglądu Soundiiz. Porównaj wykonawcę, remiks i tytuł; **zatrzymaj się na przeglądzie**, bez płatnego planu/zakupu.
- [ ] **SHOP-17 — Powtórny zapis i błąd handoffu.** Powtórz generowanie playlisty; osobno sprawdź próbę przy niedostępnej sieci. Oczekiwane: bez nadpisania poprzedniego pliku; przy błędzie Soundiiz lokalny plik nadal istnieje. Schowek i przeglądarka mogą nie zostać zaktualizowane po nieudanym żądaniu.
- [ ] **SHOP-18 — Limit Soundiiz.** Jeżeli dysponujesz ponad 200 gotowymi pozycjami, sprawdź odmowę/ograniczenie handoffu. Oczekiwane: osobny komunikat o limicie 200 utworów (bez żądania do Soundiiz), a nie ogólne „Soundiiz import failed”; zachowany lokalny plik. Bez takiego zestawu NIE TESTOWANO.
- [ ] **SHOP-19 — Porządek po teście.** Usuń z koszyka testowe dodatki, sprawdź zachowanie wcześniejszych pozycji. Oczekiwane: żadnego zamówienia, obciążenia, zakupu subskrypcji ani niezamierzonego transferu.

## 18. Błędy, operacje w tle, diagnostyka i zamykanie

- [ ] **ERR-01 — Baner błędu.** Otwórz nieprawidłowy JSON lub niedostępny folder. Oczekiwane: widoczny baner z Details i Dismiss error; można dalej obsługiwać aplikację.
- [ ] **ERR-02 — Trwałość komunikatu.** Po błędzie wykonaj akcję informacyjną. Oczekiwane: nowa informacja nie usuwa błędu; Dismiss ukrywa baner, ale wpis pozostaje w Help → Messages.
- [ ] **ERR-03 — Diagnostyka.** Help → Diagnostics. Oczekiwane: ścieżka i czytelny końcowy fragment logu do skopiowania; brak niezamierzonego wysyłania logów. Messages to ograniczona historia bieżącego okna, nie pełny dziennik wszystkich sesji.
- [ ] **ERR-04 — Dane w logach.** Po logowaniu i bramkach przejrzyj własny log, szukając przypadkowo ujawnionych tokenów, cookies lub danych dostępu. Oczekiwane: dane uwierzytelniające zredagowane. Do zgłoszenia błędu dołączaj tylko przejrzany fragment.
- [ ] **ERR-05 — Błąd a blokada dostawcy.** Zapisz URL typu źródła, etap i komunikat przy 403/CAPTCHA/timeout. Oczekiwane: da się ustalić, co nie doszło do skutku; nie oznaczaj testu PASS tylko dlatego, że pojawił się komunikat.
- [ ] **ERR-06 — Druga operacja.** Podczas analizy/downloadu spróbuj drugiej głównej operacji. Oczekiwane: kontrolowane wyłączenie/odmowa lub uporządkowanie pracy; żadnego niekontrolowanego nakładania dwóch takich zadań. Zwykła nawigacja i transport mają pozostać używalne.
- [ ] **ERR-07 — Zamknięcie podczas pracy.** Osobno zamknij aplikację podczas pobierania, analizy i eksportu Copy. Oczekiwane: kończy/anuluje własne zadania, nie pozostawia nieskończonego procesu; po ponownym starcie kompletne wyniki są zachowane, niedokończone nie udają sukcesu.
- [ ] **ERR-08 — Zamknięcie z pytaniem.** Zamknij aplikację przy otwartym potwierdzeniu lub formularzu. Oczekiwane: nie zatwierdza automatycznie operacji i daje się ponownie uruchomić.
- [ ] **ERR-09 — Utrata sieci.** Odłącz sieć, otwórz zapisaną playlistę i lokalny folder, odtwórz lokalny plik; następnie wykonaj akcję wymagającą sieci. Oczekiwane: lokalne operacje działają, sieciowa kończy się wyjaśnionym błędem; po przywróceniu sieci można ponowić.
- [ ] **ERR-10 — Dłuższa sesja.** Przez 30–60 min przełączaj playlisty/foldery, odtwarzaj, filtruj i pobieraj. Oczekiwane: brak narastającego opóźnienia, lawiny okien/procesów i utraty reakcji. Zapisz użycie RAM na początku i końcu; nie stosuj wymyślonego twardego limitu wydajności.

## 19. Dodatkowa akceptacja platformy i granice ręcznych prób

- [ ] **PLAT-01 — Skalowanie.** Powtórz najważniejsze menu, formularze i transport przy systemowym skalowaniu 100%, 150%, 200%, jeśli dostępne. Oczekiwane: czytelny interfejs i dostępne przyciski.
- [ ] **PLAT-02 — Klawiatura i dostępność.** Spróbuj dojść Tab/Shift+Tab do pól i przycisków oraz odczytać podstawowe kontrolki czytnikiem ekranu, jeśli go używasz. Oczekiwane do oceny: widoczny fokus i zrozumiałe nazwy. Pełna dostępność nie jest potwierdzona samymi testami startu.
- [ ] **PLAT-03 — Uruchomienie bez terminala.** Po testach izolowanych sprawdź sam start z menu aplikacji, nie wykonując tam destrukcyjnych prób. Oczekiwane: GUI i ikona działają; pamiętaj, że ten skrót używa normalnego profilu.
- [ ] **PLAT-04 — Inny system, jeśli masz sprzęt.** Na Windows/macOS powtórz start, pliki ze spacjami, audio, analizę, przeglądarkę i Copy. Oczekiwane: kompletna ścieżka na tym konkretnym systemie; test na Linuksie nie zalicza Windows ani macOS.
- [ ] **PLAT-05 — Instalacja/aktualizacja na testowym profilu.** Jeżeli osobno testujesz instalator, sprawdź aktualizację, skróty i zachowanie biblioteki po ponownym uruchomieniu. Oczekiwane: zachowane dane; wykonanie samych testów GUI nie potwierdza instalatora ani podpisu wydawcy.

Awaria w dokładnym momencie zapisu bazy, podmiana ceny pomiędzy odczytem a kliknięciem, uszkodzenie dziennika eksportu, power loss i wyścigi dwóch procesów wymagają kontrolowanego środowiska/atrap. Nie wywołuj ich na własnej bibliotece. Jeśli nie da się bezpiecznie przygotować przypadku, zostaw NIE TESTOWANO. Przy planowym teście odzyskiwania po wymuszonym przerwaniu użyj osobnego profilu, kopii DESTROY i zachowaj stan przed/po do analizy; nie utożsamiaj go z gwarancją odporności na awarię nośnika.

## 20. Pełne przebiegi od początku do końca

Poniższe przebiegi spinają pojedyncze przypadki. Zaliczenie osobnych przycisków nie zastępuje tych sekwencji.

- [ ] **E2E-01 — Publiczne kopanie → lokalny plik.** Czysty profil → preferencje → dodaj P1 → filtruj → odsłuchaj → oznacz/undo → wybierz darmowy download → sprawdź plik → otwórz folder → odtwórz lokalnie → restart → sprawdź status i dostępność.
- [ ] **E2E-02 — Własne konto → prywatna playlista.** Logowanie → import OWN z private → porównanie zawartości → status wspólnego utworu → restart → wylogowanie → kontrolowana odmowa prywatnej akcji → ponowne publiczne użycie.
- [ ] **E2E-03 — Lokalny set.** Dodaj L1 → przejrzyj pliki → analiza → ręczna poprawka → zapisz lokalną playlistę → odsłuch kolejnych utworów → eksport Copy z Review → sprawdź pliki/parametry → ponownie otwórz wynik w GUI → restart.
- [ ] **E2E-04 — Bramka z człowiekiem.** Dodaj źródło GATE → D → wymagany profil/logowanie/ręczny krok → ukończ darmowy download → wróć do GUI → sprawdź got, ścieżkę i odsłuch → restart. Drugi przebieg zakończ Cancel i porównaj różnicę.
- [ ] **E2E-05 — Sklepy bez zamówienia.** Playlista z SHOP → C → sprawdź plan/produkt/cenę → przygotuj koszyk Bandcamp → sprawdź zawartość → osobne pozycje Beatport → jawny handoff Soundiiz → porównaj plik i podgląd → zakończ przed zakupem/transferem → usuń testowe dodatki z koszyka.
- [ ] **E2E-06 — Odporność na przerwanie.** Duży lokalny folder → eksport Copy → Cancel po części wyników → zamknij → uruchom → Resume export → sprawdź kompletność → odtwórz wynik → przejrzyj diagnostykę.
- [ ] **E2E-07 — Biblioteka i wymiana linków.** Dodaj P1/P2 → statusy → eksport wybranego zestawu do JSON/CSV → import JSON → porównanie → usunięcie testowego wiersza z zapisanej playlisty → refresh → restore → restart → kontrola trwałości.

## Znane różnice, żeby nie testować funkcji, których GUI nie udostępnia

| Obszar | Co rzeczywiście wynika z kodu |
| --- | --- |
| Import CSV | Formularz mówi „JSON or CSV”, ale `links.load_summary()` czyta JSON. CSV jest formatem wyjściowym. |
| Cofanie usunięcia | Ctrl+Z cofa statusy. Usunięte z playlisty SoundCloud wiersze przywraca Restore removed tracks. |
| Lokalna playlista | Można zapisać/dopisać pliki i usunąć całą playlistę. Kontekst lokalny oferuje trwałe Delete files; zwykłe usunięcie pojedynczego odniesienia nie jest wystawione jak dla playlist SoundCloud. |
| Importowany JSON | Backend buduje tymczasowy widok; ta akcja nie zapisuje automatycznie playlisty. |
| CDJ/rekordbox | W formularzu GUI są format, bity, częstotliwość, tryb i rekursja. Nie ma selektora modelu CDJ, zapisu bazy rekordbox, beatgridu ani cue points. |
| Eksplorator | Pokazuje obsługiwane rozszerzenia z `media.FORMATS`; lista nie jest identyczna z szerszą listą skanera. Filtry tabeli są ograniczone do załadowanej strony. |
| Analyze folder | Obejmuje bezpośrednie pliki całego folderu, ignorując filtr, zaznaczenie i stronę. Rekursja jest opcją eksportu, nie tego polecenia analizy. |
| Sklepy i filtr | Open links honoruje konkretny sklep. `action_cart()` zbiera oba rodzaje linków sklepowych z wybranych wierszy. |
| Potwierdzenie koszyka | GUI przekazuje callback wyświetlający Review cart również dla pojedynczego Bandcamp. Opis skróconej ścieżki pojedynczego produktu w specyfikacji nie oznacza pominięcia tego dialogu GUI. Dokładne linki Beatport mogą od razu przejść do wyników playlisty bez zmiany koszyka. |
| Wynik koszyka | GUI składa tekst z `status` i `reason`; nie należy wymagać bogatego, przetłumaczonego ekranu wyników. |
| Wynik downloadu | GUI obsługuje postęp i błędy pojedynczych plików, ale nie prezentuje wszystkich summary/status/deferred zdarzeń workflow. |
| Wylogowanie | W menu jest wylogowanie SoundCloud, nie ogólne wyczyszczenie wszystkich profili/cookies/kont. |
| Skróty | Korzystaj z Help → Keyboard shortcuts w GUI. Np. E oznacza edycję BPM/Key, Shift+E eksport linków, P poprzedni utwór. |
| Wygląd/metadata | Rok/label/gatunek mogą być puste w lokalnym widoku mimo istnienia tagów; `media_track()` obecnie mapuje przede wszystkim artist/title, duration, BPM i key. |
| Analiza i sprzęt | Dostępna funkcja analizy nie oznacza zmierzonej dokładności na kolekcji DJ-skiej; zgodność pliku nie zastępuje próby na odtwarzaczu. |
| Aktualizacje | GUI nie ma automatycznej aktualizacji ani potwierdzonej funkcji pełnego kasowania danych aplikacji. |

Powyższe różnice są ustaleniami z odczytu kodu, nie wynikami żywego testu. Warto osobno zgłosić szczególnie mylącą etykietę CSV i różnicę zakresu filtra przy koszyku.

## Notatka do każdego nieudanego testu

```text
ID testu:
Wersja / system / sposób uruchomienia:
Profil testowy / typ źródła:
Kroki (w tym zaznaczenie, filtr, strona folderu):
Oczekiwany wynik:
Rzeczywisty wynik:
Czy błąd da się powtórzyć:
Czy plik, status lub koszyk faktycznie się zmienił:
Godzina i przejrzany fragment logu / zrzut ekranu:
Klasyfikacja: FAIL / BLOKADA DOSTAWCY / NIE TESTOWANO
```

Nie umieszczaj w zgłoszeniu haseł, tokenów, cookies ani nieprzejrzanych danych prywatnych. Zestawienie końcowe powinno zawierać liczbę PASS, FAIL i NIE TESTOWANO/BLOKADA oraz osobno wynik każdego E2E-01…07. Nie nazywaj całości przetestowaną, jeśli część zależna od konta, dostawcy lub sprzętu nie została wykonana.

## Źródła i zakres weryfikacji dokumentu

| Zakres checklisty | Bieżące źródło |
| --- | --- |
| Kontrakt GUI, układ, skróty, trwałość | [Specyfikacja §3.8](../PROJECT-SPECIFICATION.md#38-qt-quick-desktop), `dj_digger/gui/qml/Main.qml:231`, `dj_digger/gui/bridge.py:167` |
| Wejścia SoundCloud, prywatny profil | Specyfikacja §3.1, §9.1; `dj_digger/gui/backend.py:248`, `:508`; `dj_digger/services/profile_import.py:19` |
| Akcje tabeli i statusy | `dj_digger/gui/model.py:9`, `dj_digger/gui/backend.py:270`, `:485`, `:834` |
| Foldery, lokalne playlisty, skan | Specyfikacja §3.5, §3.7; `dj_digger/gui/backend.py:215`, `:471`, `:823`, `:843`; `dj_digger/services/local_library.py:54`; `dj_digger/db.py:434` |
| Audio | Specyfikacja §3.4 i §3.8; `dj_digger/gui/backend.py:575`, `:706`, `:714`; `dj_digger/gui/qml/Main.qml:623` |
| Download i bramki | Specyfikacja §3.5, §9.2, §10.2; `dj_digger/gui/backend.py:306`; `dj_digger/services/downloads.py:274`; `dj_digger/gates/browser.py:739` |
| Analiza i eksport | Specyfikacja §3.7; `dj_digger/gui/backend.py:368`, `:401`, `:426`, `:764`, `:857`; `dj_digger/decks.py:69`; `dj_digger/export.py` |
| JSON/CSV i błędy importu | Specyfikacja §8.2; `dj_digger/gui/backend.py:792`, `:801`; `dj_digger/links.py:417` |
| Koszyki i Soundiiz | Specyfikacja §3.6, §10.4, §13.2; `dj_digger/gui/backend.py:871`; `dj_digger/services/purchases.py:438`, `:805`, `:1000` |
| Preferences i logi | `dj_digger/gui/backend.py:525`, `:758`; `dj_digger/gui/bridge.py:270`; `dj_digger/paths.py:13` |
| Dotychczasowe testy jako pomoc w doborze przypadków | [tests/test_gui.py](../tests/test_gui.py), [opis implementacji desktopu](implementation/qt-quick-desktop.md) — nie były uruchamiane w ramach przygotowania tej listy |
| Ograniczenia grafu | [docs/graph-notes.md](graph-notes.md), [graf JSON](../graphify-out/graph.json) — graf nie jest dowodem zachowania QML |

Źródłami nadrzędnymi dla tej listy są bieżący kod oraz właściwe sekcje specyfikacji. Aplikacja nie ma już interfejsu TUI; GUI jest jedynym interfejsem interaktywnym.
