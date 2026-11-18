import re
import time
from urllib.robotparser import RobotFileParser
from collections import Counter, deque
import math
from urllib.parse import urlparse
from selenium.webdriver.common.by import By

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
    def __init__(self) -> None:
        self._tf: dict[str, dict[str, float]] = {}
        self._df: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._tf)

    def __contains__(self, url: str) -> bool:
        return url in self._tf

    def is_indexed(self, url: str) -> bool:
        """Czy strona o podanym URL jest już w indeksie."""
        return url in self

    def add(self, url: str, content: str) -> bool:
        if url in self:
            return False

        tokens = tokenize(content)
        counts = Counter(tokens)
        total = len(tokens)

        self._tf[url] = {term: n / total for term, n in counts.items()} if total else {}
        self._df.update(counts.keys())
        return True

    def urls(self) -> list[str]:
        return list(self._tf)

    def term_frequencies(self, url: str) -> dict[str, float]:
        return self._tf[url]

    def idf(self, term: str) -> float | None:
        df = self._df.get(term)
        if not df:
            return None
        return math.log((1 + len(self)) / (1 + df)) + 1


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

def make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    return webdriver.Chrome(options=options)


def fetch_robots(driver, base_url: str) -> str:

    driver.get(f"{base_url}/robots.txt")
    return driver.find_element(By.TAG_NAME, "body").text


def _element_text(element) -> str:
    return (element.get_attribute("textContent") or element.text or "").strip()


def _page_title(driver, By) -> str:
    for element in driver.find_elements(By.CSS_SELECTOR, "#firstHeading .mw-page-title-main"):
        text = _element_text(element)
        if text:
            return text
    doc_title = getattr(driver, "title", None)
    if isinstance(doc_title, str) and " – " in doc_title:
        head, _, tail = doc_title.rpartition(" – ")
        if "Wikipedia" in tail and head.strip():
            return head.strip()
    return driver.find_element(By.ID, "firstHeading").text.strip()

def extract_article(driver) -> tuple[str, str]:


    title = _page_title(driver, By)
    paragraphs = driver.find_elements(By.CSS_SELECTOR, "#mw-content-text .mw-parser-output p")
    texts = [t for t in (_element_text(p) for p in paragraphs) if t]
    if not texts:
        containers = driver.find_elements(By.CSS_SELECTOR, "#mw-content-text")
        if containers:
            texts = [t for t in [_element_text(containers[0])] if t]
    return title, "\n".join(texts)


def extract_links(driver) -> list[str]:
    from selenium.webdriver.common.by import By

    links = []
    seen = set()
    for a in driver.find_elements(By.CSS_SELECTOR, "#mw-content-text a[href]"):
        href = a.get_attribute("href") or ""
        parts = urlparse(href)
        if not parts.path.startswith("/wiki/") or ":" in parts.path or parts.fragment or parts.query:
            continue
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links

# --------------------------------------------------------------------- crawler

class Crawler:
    def __init__(
        self,
        driver,
        indexer: Indexer,
        limiter: RateLimiter,
        allowed_domains=ALLOWED_DOMAINS,
        max_depth: int = MAX_DEPTH,
        max_links_per_page: int = MAX_LINKS_PER_PAGE,
        max_pages: int = MAX_PAGES,
    ):
        if not 0 <= max_depth <= MAX_DEPTH_LIMIT:
            raise ValueError(f"max_depth musi być w zakresie 0-{MAX_DEPTH_LIMIT}")
        if max_pages < 1 or max_links_per_page < 1:
            raise ValueError("max_pages i max_links_per_page muszą być dodatnie")
        self.driver = driver
        self.indexer = indexer
        self.limiter = limiter
        self.allowed_domains = tuple(allowed_domains)
        self.max_depth = max_depth
        self.max_links_per_page = max_links_per_page
        self.max_pages = max_pages
        self.titles: dict[str, str] = {}
        self.depths: dict[str, int] = {}
        self._robots_cache: dict[str, RobotFileParser] = {}

    def _robots(self, url: str) -> RobotFileParser:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots_cache:
            self.limiter.wait()
            rp = parse_robots(fetch_robots(self.driver, base))
            self.limiter.raise_min_interval(rp.crawl_delay("*"))
            self._robots_cache[base] = rp
        return self._robots_cache[base]

    def _select_links(self, links: list[str], queued: set) -> list[str]:
        selected = []
        for link in links:
            if len(selected) >= self.max_links_per_page:
                break
            if link in queued or urlparse(link).netloc not in self.allowed_domains:
                continue
            queued.add(link)
            if not is_allowed(self._robots(link), link):
                print(f"[POMINIĘTO robots.txt] {link}")
                continue
            selected.append(link)
        return selected

    def crawl(self, start_url: str) -> dict[str, int]:
        """Przechodzi strony wszerz od start_url (poziom 0) do max_depth; zwraca {adres: poziom}."""
        if urlparse(start_url).netloc not in self.allowed_domains:
            raise ValueError(f"domena adresu startowego spoza dozwolonych: {self.allowed_domains}")
        self.titles, self.depths = {}, {}
        if not is_allowed(self._robots(start_url), start_url):
            print(f"[POMINIĘTO robots.txt] {start_url}")
            return self.depths

        queue = deque([(start_url, 0)])
        queued = {start_url}
        downloads = 0
        while queue and downloads < self.max_pages:
            url, depth = queue.popleft()
            self.limiter.wait()
            downloads += 1
            try:
                self.driver.get(url)
                title, text = extract_article(self.driver)
                if not text.strip():
                    print(f"[BŁĄD] {url} (pusta treść, tytuł: {title!r})")
                    continue
                links = extract_links(self.driver) if depth < self.max_depth else []
            except Exception as exc:
                print(f"[BŁĄD] {url}: {exc}")
                continue
            self.indexer.add(url, text)
            self.titles[url] = title
            self.depths[url] = depth
            print(f"[poziom {depth}] [{len(self.depths)}/{self.max_pages}] {title}")
            for link in self._select_links(links, queued):
                queue.append((link, depth + 1))
        return self.depths
# --------------------------------------------------------------------- terminal

def main() -> None:
    try:
        driver = make_driver()
    except Exception as exc:
        print(f"Nie udało się uruchomić przeglądarki: {exc}")
        return
    indexer = Indexer()
    crawler = Crawler(
        driver,
        indexer,
        RateLimiter(MIN_INTERVAL),
        allowed_domains=ALLOWED_DOMAINS,
        max_depth=MAX_DEPTH,
        max_links_per_page=MAX_LINKS_PER_PAGE,
        max_pages=MAX_PAGES,
    )
    try:
        crawler.crawl(START_URL)
    except Exception as exc:
        print(f"Nie udało się pobrać stron: {exc}")
        return
    finally:
        driver.quit()
    if len(indexer) == 0:
        print("Nie zaindeksowano żadnych stron.")
        return

    engine = SearchEngine(indexer)
    print(f"\nZaindeksowano {len(indexer)} stron.")
    while True:
        try:
            query = input("Zapytanie (pusta linia = wyjście): ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query == "":
            break
        results = engine.search_scored(query, top_k=5)
        if not results:
            print("Brak wyników.")
            continue
        for url, score in results:
            print(f"{crawler.titles.get(url, url)} | {url} | {score:.4f}")

if __name__ == "__main__":
    main()
