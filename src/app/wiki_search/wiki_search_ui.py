import threading
import webbrowser

import customtkinter as ctk

from wiki_search import (
    ALLOWED_DOMAINS,
    MAX_DEPTH,
    MAX_LINKS_PER_PAGE,
    MAX_PAGES,
    MIN_INTERVAL,
    START_URL,
    Crawler,
    Indexer,
    RateLimiter,
    SearchEngine,
    make_driver,
)

FONT = ("Consolas", 13)
FONT_BOLD = ("Consolas", 13, "bold")
POLL_MS = 300
IGNORED_KEYS = ("Alt_R", "ISO_Level3_Shift", "Control_L", "Control_R", "Shift_L", "Shift_R")

def format_header() -> str:
    return f"{'Nr':>3}  {'Wynik':>7}  Adres strony"


def format_row(number: int, url: str, score) -> str:
    score_text = "      -" if score is None else f"{score:7.4f}"
    return f"{number:>3}  {score_text}  {url}"


def all_pages_as_rows(urls) -> list[tuple[str, None]]:
    return [(url, None) for url in urls]


def open_link(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


class StopRequested(Exception):
    pass


class StoppableLimiter(RateLimiter):

    def __init__(self, stop_event: threading.Event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = stop_event

    def wait(self) -> None:
        if self._stop_event.is_set():
            raise StopRequested()
        super().wait()


# ------------------------------------------------------------------ okno

class SearchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Wyszukiwarka stron Wikipedii")
        self.geometry("950x550")

        self.indexer = Indexer()
        self.engine = SearchEngine(self.indexer)
        self.stop_event = threading.Event()
        self.crawl_done = threading.Event()
        self.crawl_error = None

        # Pole z zapytaniem (wyłączone, dopóki trwa pobieranie stron)
        self.entry = ctk.CTkEntry(self, placeholder_text="Wpisz zapytanie", height=36)
        self.entry.pack(fill="x", padx=15, pady=(15, 5))
        self.entry.bind("<KeyRelease>", self.on_query_change)
        self.entry.configure(state="disabled")

        # Informacja pod polem (postęp, liczba wyników, błędy)
        self.status_label = ctk.CTkLabel(self, text="Pobieranie stron...", anchor="w")
        self.status_label.pack(fill="x", padx=15)

        # Nagłówek tabeli
        self.header_label = ctk.CTkLabel(self, text=format_header(), anchor="w", font=FONT_BOLD)
        self.header_label.pack(fill="x", padx=20, pady=(5, 0))

        # Lista wierszy z przewijaniem
        self.list_frame = ctk.CTkScrollableFrame(self)
        self.list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.worker = threading.Thread(target=self._crawl_worker, daemon=True)
        self.worker.start()
        self.after(POLL_MS, self._poll_crawl)

    # pobieranie stron (osobny wątek

    def _crawl_worker(self):
        driver = None
        try:
            driver = make_driver()
            crawler = Crawler(
                driver,
                self.indexer,
                StoppableLimiter(self.stop_event, MIN_INTERVAL),
                allowed_domains=ALLOWED_DOMAINS,
                max_depth=MAX_DEPTH,
                max_links_per_page=MAX_LINKS_PER_PAGE,
                max_pages=MAX_PAGES,
            )
            crawler.crawl(START_URL)
        except StopRequested:
            pass
        except Exception as exc:
            self.crawl_error = str(exc)
        finally:
            if driver is not None:
                driver.quit()
            self.crawl_done.set()

    def _poll_crawl(self):
        if not self.crawl_done.is_set():
            self.status_label.configure(
                text=f"Pobieranie stron (limit {MAX_PAGES}): zaindeksowano {len(self.indexer)}..."
            )
            self.after(POLL_MS, self._poll_crawl)
            return
        self.entry.configure(state="normal")
        if self.crawl_error and len(self.indexer) == 0:
            self.status_label.configure(text=f"Nie udało się pobrać stron: {self.crawl_error}")
            self.show_rows([])
            return
        note = f" (pobieranie przerwane: {self.crawl_error})" if self.crawl_error else ""
        self.show_all_pages(note)

    # --- wyświetlanie i wyszukiwanie

    def show_all_pages(self, note: str = ""):
        rows = all_pages_as_rows(self.indexer.urls())
        self.status_label.configure(
            text=f"Wszystkie strony: {len(rows)}. Kliknij wiersz, żeby otworzyć link.{note}"
        )
        self.show_rows(rows)

    def on_query_change(self, event=None):
        query = self.entry.get().strip()
        if event and event.keysym in IGNORED_KEYS:
            return
        if query == "":
            self.show_all_pages()
            return
        results = self.engine.search_scored(query)
        if len(results) == 0:
            self.status_label.configure(text="Brak wyników.")
        else:
            self.status_label.configure(
                text=f"Wyniki: {len(results)}. Kliknij wiersz, żeby otworzyć link."
            )
        self.show_rows(results)

    def show_rows(self, rows):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        for number, (url, score) in enumerate(rows, start=1):
            button = ctk.CTkButton(
                self.list_frame,
                text=format_row(number, url, score),
                anchor="w",
                font=FONT,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray75", "gray25"),
                command=lambda url=url: self.open_document(url),
            )
            button.pack(fill="x", pady=1)

    def open_document(self, url):
        if not open_link(url):
            self.status_label.configure(text=f"Nie udało się otworzyć linku: {url}")

    def on_close(self):
        self.stop_event.set()
        self.worker.join(timeout=5)  # daje czas na zamknięcie Chrome
        self.destroy()


def main():
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = SearchApp()
    app.mainloop()


if __name__ == "__main__":
    main()
