# Końcowe review refaktoru

Zakres: `171f033` (`v1.0.0`) → `f485591`, branch
`refactor/post-1.0-architecture`. Review według `AGENTS.md`, odpowiednich sekcji
`PROJECT-SPECIFICATION.md` i zatwierdzonego planu użytkownika. Dwie niezależne
osie; poprawki sprawdzono ponownie po implementacji. Data: 2026-09-05.

## Standardy

Brak otwartych, potwierdzonych findingów w sprawdzonym zakresie.

Zamknięte problemy:

- Spóźniony skan usuwał nowsze pochodzenie pliku: obserwacje braku pliku
  porównują rewizję pod blokadą, przy zachowaniu reguły pewnego dopasowania po `skip`.
- Oczekiwanie asyncio na wątek omijało limit zamykania: niezależny timer działa
  już podczas demontażu aplikacji, bez zamykania zasobów używanego wątku.
- Powrót do załadowanego A nie odrzucał przygotowywanego B: skrót zmienia generację.
- Anulowanie mogło zwolnić slot podczas zapisu profilu: bariera obejmuje zapis,
  a dialogi zachowują anulowanie konkretnej operacji.
- Równoległe oznaczenia łamały powtórne `g` i przesuwanie kursora: akcje zachowują
  kolejność w workerach; oczekująca akcja jest odrzucana po zmianie playlisty.

Najpoważniejsze znalezione problemy dotyczyły utraty poprawnego stanu pliku
oraz przedwczesnego zwalniania zasobów/operacji. Wszystkie mają regresje.

## Spec

Brak otwartych, potwierdzonych findingów w sprawdzonym zakresie.

Zamknięte problemy:

- Pierwsze odświeżenie mogło zostać odrzucone po usunięciu wiersza: początkowa
  generacja pozostaje stabilna aż do usunięcia playlisty.
- Klient tworzony po logowaniu utrwalał token: klienci aplikacji odczytują
  aktualne poświadczenia przy żądaniu, z pierwszeństwem zmiennej środowiskowej.
- Anulowanie było utożsamiane z oczekiwaniem lub błędem: zdarzenia i podsumowania
  rozróżniają anulowanie; adapter Chromium nie dopisuje sztucznych błędów
  anulowanym pozycjom, zachowując gotowe pliki i rzeczywiste błędy.

Potwierdzono wspólny przepływ pobierania, wynik z opublikowaną ścieżką przy
nieudanym zapisie biblioteki, składanie usług CLI/TUI i modele bez zależności
od SQLite/UI. Testy anulowania obejmują rzeczywisty kształt wyniku adaptera,
nie tylko uproszczony mock usługi.

Review nie stanowi pełnego audytu bezpieczeństwa ani potwierdzenia zachowania
żywych dostawców. Wyniki wykonanych testów i granice macierzy środowisk znajdują
się w [raporcie weryfikacji](verification.md).
