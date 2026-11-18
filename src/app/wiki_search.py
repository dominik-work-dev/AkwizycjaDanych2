import re
import time
from urllib.robotparser import RobotFileParser
from collections import Counter
import math

BASE_URL = "https://pl.wikipedia.org"
START_URL = "https://pl.wikipedia.org/wiki/Wyszukiwarka_internetowa"
ALLOWED_DOMAINS = ("pl.wikipedia.org",)
MAX_DEPTH = 2  # poziom 0 = strona startowa
MAX_DEPTH_LIMIT = 5  # tyle dopuszcza treść zadania
MAX_LINKS_PER_PAGE = 3  # "wybrane strony": ile linków śledzimy z jednej strony
MAX_PAGES = 30  # twardy limit pobrań
MIN_INTERVAL = 1.0  # sekundy między żądaniami


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())

# --------------------------------------------------------------------- robots.txt

def parse_robots(robots_text: str) -> RobotFileParser:
    rp = RobotFileParser()
    rp.parse(robots_text.splitlines())
    return rp


def is_allowed(rp: RobotFileParser, url: str, user_agent: str = "*") -> bool:
    return rp.can_fetch(user_agent, url)


# --------------------------------------------------------------------- rate limiter

class RateLimiter:
    def __init__(self, min_interval: float = MIN_INTERVAL, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last = None

    def raise_min_interval(self, seconds) -> None:
        if seconds and seconds > self.min_interval:
            self.min_interval = seconds

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.min_interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

# --------------------------------------------------------------------- indekser

class Indexer:
    """ przechowuje TF dokumentów i DF terminów; IDF liczy się przy zapytaniu."""
    def __init__(self):
        self._tf: dict[str, dict[str, float]] = {}
        self._df: Counter = Counter()

    def __len__(self) -> int:
        return len(self._tf)

    def is_indexed(self, url: str) -> bool:
        return url in self._tf

    # dodanie strony gdy jej brakuje
    def add(self, url: str, content: str) -> bool:
        if url in self._tf:
            return False
        tokens = tokenize(content)
        counts = Counter(tokens)
        total = len(tokens)
        self._tf[url] = {t: c / total for t, c in counts.items()} if total else {}
        self._df.update(counts.keys())
        return True

    def urls(self) -> list[str]:
        return list(self._tf)

    def term_frequencies(self, url: str) -> dict[str, float]:
        return self._tf[url]

    def idf(self, term: str):
        df = self._df.get(term)
        if not df:
            return None
        return math.log((1 + len(self._tf)) / (1 + df)) + 1


# --------------------------------------------------------------------- wyszukiwarka
class SearchEngine:
    def __init__(self, indexer: Indexer):
        self.indexer = indexer

    def search_scored(self, query: str, top_k: int | None = None) -> list[tuple[str, float]]:
        idx = self.indexer
        q_tokens = [t for t in tokenize(query) if idx.idf(t) is not None]
        if not q_tokens:
            return []
        counts = Counter(q_tokens)
        q_vec = {t: (c / len(q_tokens)) * idx.idf(t) for t, c in counts.items()}
        q_norm = math.sqrt(sum(w * w for w in q_vec.values()))
        results = []
        for url in idx.urls():
            tf = idx.term_frequencies(url)
            if not tf:
                continue
            d_norm = math.sqrt(sum((w * idx.idf(t)) ** 2 for t, w in tf.items()))
            dot = sum(qw * tf.get(t, 0.0) * idx.idf(t) for t, qw in q_vec.items())
            score = dot / (q_norm * d_norm)
            if score > 0:
                results.append((url, score))
        results.sort(key=lambda r: r[1], reverse=True)
        return results if top_k is None else results[:top_k]

    def search(self, query: str, top_k: int | None = None) -> list[str]:
        """Posortowana lista adresów."""
        return [url for url, _ in self.search_scored(query, top_k)]


# --------------------------------------------------------------------- Selenium


# --------------------------------------------------------------------- terminal

def main() -> None:
    indexer = Indexer()


if __name__ == "__main__":
    main()
