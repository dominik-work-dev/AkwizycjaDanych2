"""Testy elementów Selenium i integracyjne całego systemu (Crawler + Indexer + SearchEngine).

Przeglądarkę zastępuje FakeBrowser ("mini-Wikipedia" w pamięci), więc prawdziwy kod
fetch_robots / extract_article / extract_links / Crawler / Indexer / SearchEngine / main
przechodzi bez Chrome i bez sieci. Test na żywej Wikipedii: RUN_LIVE=1 (patrz TestLive).
"""
import contextlib
import io
import os
import sys
import types
import unittest
from unittest import mock

import src.app.wiki_search.wiki_search as wiki_search
from src.app.wiki_search.wiki_search import BASE_URL, START_URL, Crawler, Indexer, RateLimiter, SearchEngine

ROBOTS = "User-agent: *\nDisallow: /wiki/Zablokowana_strona\n"
ROBOTS_URL = f"{BASE_URL}/robots.txt"


def wiki(title):
    return f"{BASE_URL}/wiki/{title}"


START = wiki("Wyszukiwarka_internetowa")  # poziom 0
INDEKS = wiki("Indeks_(informatyka)")  # poziom 1
ROBOT = wiki("Robot_internetowy")  # poziom 1
RANKING = wiki("Ranking_stron")  # poziom 1
STRUKTURY = wiki("Struktury_danych")  # poziom 2
PAGERANK = wiki("PageRank")  # poziom 2
DRZEWO = wiki("Drzewo_(informatyka)")  # poziom 3
BLOCKED = wiki("Zablokowana_strona")

PAGES = {
    START: (
        "Wyszukiwarka internetowa",
        ["Wyszukiwarka internetowa to program przeszukujący zasoby sieci.", "   ", "Przykładem jest Google."],
        [
            INDEKS,
            wiki("Specjalna:Szukaj"),
            wiki("Robot_internetowy#Historia"),
            BLOCKED,
            "https://en.wikipedia.org/wiki/Search_engine",
            INDEKS,
            ROBOT,
            RANKING,
        ],
    ),
    INDEKS: ("Indeks (informatyka)", ["Indeks odwrócony przyspiesza wyszukiwanie słów w dokumentach."], [ROBOT, STRUKTURY]),
    ROBOT: ("Robot internetowy", ["Robot internetowy pobiera strony i zapisuje ich treść."], [START]),
    RANKING: ("Ranking stron", ["Ranking stron porządkuje wyniki wyszukiwania."], [PAGERANK]),
    STRUKTURY: ("Struktury danych", ["Struktury danych organizują informacje w pamięci."], [DRZEWO]),
    PAGERANK: ("PageRank", ["PageRank ocenia ważność strony na podstawie linków."], []),
    DRZEWO: ("Drzewo (informatyka)", ["Drzewo to hierarchiczna struktura danych."], []),
    BLOCKED: ("Zablokowana", ["tajna treść"], []),
}


class NoSuchElement(Exception):
    pass


class FakeElement:
    def __init__(self, text="", href=None):
        self.text = text
        self._href = href

    def get_attribute(self, name):
        if name == "textContent":
            return self.text
        return self._href if name == "href" else None


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeBrowser:
    """Udaje webdriver: get / find_element / find_elements / quit."""

    def __init__(self, pages, robots=ROBOTS, fail_on=(), clock=None):
        self.pages = pages
        self.robots = robots
        self.fail_on = set(fail_on)
        self.clock = clock
        self.requested = []
        self.request_times = []
        self.current_url = None
        self.quit_called = False

    def get(self, url):
        self.requested.append(url)
        if self.clock:
            self.request_times.append(self.clock())
        if url in self.fail_on:
            raise RuntimeError("timeout")
        self.current_url = url

    def find_element(self, by, value):
        if self.current_url.endswith("/robots.txt") and by == "tag name" and value == "body":
            return FakeElement(self.robots)
        page = self.pages.get(self.current_url)
        if page is not None and by == "id" and value == "firstHeading":
            return FakeElement(page[0])
        raise NoSuchElement(f"{by}={value}")

    def find_elements(self, by, value):
        page = self.pages.get(self.current_url)
        if page is None:
            return []
        if by == "css selector" and value == "#mw-content-text .mw-parser-output p":
            return [FakeElement(text) for text in page[1]]
        if by == "css selector" and value == "#mw-content-text a[href]":
            return [FakeElement(href=h) for h in page[2]]
        return []

    def quit(self):
        self.quit_called = True


def selenium_stub_modules():
    """Atrapa selenium.webdriver.common.by.By (stałe jak w prawdziwym Selenium)."""
    names = ["selenium", "selenium.webdriver", "selenium.webdriver.common", "selenium.webdriver.common.by"]
    mods = {n: types.ModuleType(n) for n in names}
    mods["selenium.webdriver.common.by"].By = types.SimpleNamespace(
        ID="id", CSS_SELECTOR="css selector", TAG_NAME="tag name"
    )
    return mods


def stub_driver(heading="T", title_main=(), paragraphs=(), content=(), links=(), doc_title=None):
    """Mock webdrivera odpowiadający na selektory używane przez extract_article / extract_links."""
    answers = {
        "#firstHeading .mw-page-title-main": list(title_main),
        "#mw-content-text .mw-parser-output p": list(paragraphs),
        "#mw-content-text": list(content),
        "#mw-content-text a[href]": list(links),
    }
    driver = mock.Mock()
    driver.find_element.return_value = FakeElement(heading)
    driver.find_elements.side_effect = lambda by, value: answers.get(value, [])
    driver.title = doc_title
    return driver


class SeleniumStubbedCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, selenium_stub_modules())
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, browser=None, indexer=None, **kwargs):
        """Składa Crawler z fałszywym zegarem (testy nie czekają naprawdę)."""
        clock = FakeClock()
        browser = browser or FakeBrowser(PAGES)
        browser.clock = clock
        indexer = indexer if indexer is not None else Indexer()
        limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
        kwargs.setdefault("max_depth", 2)
        crawler = Crawler(browser, indexer, limiter, **kwargs)
        return crawler, browser, indexer


class TestSeleniumHelpers(SeleniumStubbedCase):
    def test_fetch_robots_opens_robots_url_and_returns_body_text(self):
        browser = FakeBrowser(PAGES)
        text = wiki_search.fetch_robots(browser, BASE_URL)
        self.assertEqual(browser.requested, [ROBOTS_URL])
        self.assertEqual(text, ROBOTS)

    def test_extract_article_returns_title_and_joined_nonblank_paragraphs(self):
        browser = FakeBrowser(PAGES)
        browser.get(START)
        title, text = wiki_search.extract_article(browser)
        self.assertEqual(title, "Wyszukiwarka internetowa")
        self.assertEqual(
            text,
            "Wyszukiwarka internetowa to program przeszukujący zasoby sieci.\nPrzykładem jest Google.",
        )

    def test_extract_article_reads_text_content_of_hidden_elements(self):
        class Hidden:
            text = ""  # Selenium: element niewidoczny

            def get_attribute(self, name):
                return "ukryty akapit" if name == "textContent" else None

        driver = stub_driver(heading="Tytuł", paragraphs=[Hidden()])
        self.assertEqual(wiki_search.extract_article(driver), ("Tytuł", "ukryty akapit"))

    def test_extract_article_requests_paragraphs_at_any_depth(self):
        driver = stub_driver(paragraphs=[FakeElement("a")])
        wiki_search.extract_article(driver)
        selectors = [c.args[1] for c in driver.find_elements.call_args_list]
        self.assertIn("#mw-content-text .mw-parser-output p", selectors)
        self.assertTrue(all(">" not in sel for sel in selectors))

    def test_extract_article_falls_back_to_whole_content_without_paragraphs(self):
        driver = stub_driver(content=[FakeElement("cała treść strony")])
        self.assertEqual(wiki_search.extract_article(driver), ("T", "cała treść strony"))

    def test_extract_article_empty_page_gives_empty_text(self):
        self.assertEqual(wiki_search.extract_article(stub_driver()), ("T", ""))

    def test_title_prefers_page_title_span_over_heading_with_edit_link(self):
        driver = stub_driver(heading="Tytułedytuj wstęp", title_main=[FakeElement("Tytuł")], paragraphs=[FakeElement("a")])
        self.assertEqual(wiki_search.extract_article(driver)[0], "Tytuł")

    def test_title_falls_back_to_document_title_without_wikipedia_suffix(self):
        driver = stub_driver(heading="Tytułedytuj wstęp", doc_title="Tytuł – Wikipedia, wolna encyklopedia", paragraphs=[FakeElement("a")])
        self.assertEqual(wiki_search.extract_article(driver)[0], "Tytuł")

    def test_title_keeps_dash_inside_the_title(self):
        driver = stub_driver(doc_title="Wojna – pokój – Wikipedia, wolna encyklopedia")
        self.assertEqual(wiki_search.extract_article(driver)[0], "Wojna – pokój")

    def test_title_falls_back_to_plain_heading(self):
        self.assertEqual(wiki_search.extract_article(stub_driver(heading="Tytuł"))[0], "Tytuł")

    def test_extract_links_does_not_depend_on_raw_href_prefix(self):
        # surowy href bywa "./Tytuł"; Selenium zwraca rozwiązany URL, więc selektor nie może wymagać "/wiki/"
        driver = stub_driver(links=[FakeElement(href=wiki("Kot")), FakeElement(href="https://pl.wikipedia.org/w/index.php?title=Kot&action=edit"), FakeElement(href=f"{wiki('Kot')}#Historia")])
        self.assertEqual(wiki_search.extract_links(driver), [wiki("Kot")])
        selectors = [c.args[1] for c in driver.find_elements.call_args_list]
        self.assertEqual(selectors, ["#mw-content-text a[href]"])

    def test_extract_links_filters_namespaces_anchors_dedupes_and_keeps_order(self):
        browser = FakeBrowser(PAGES)
        browser.get(START)
        self.assertEqual(
            wiki_search.extract_links(browser),
            [INDEKS, BLOCKED, "https://en.wikipedia.org/wiki/Search_engine", ROBOT, RANKING],
        )


class TestCrawlerDepth(SeleniumStubbedCase):
    def test_levels_zero_to_three(self):
        expected = {
            0: {START},
            1: {START, INDEKS, ROBOT, RANKING},
            2: {START, INDEKS, ROBOT, RANKING, STRUKTURY, PAGERANK},
            3: {START, INDEKS, ROBOT, RANKING, STRUKTURY, PAGERANK, DRZEWO},
        }
        for depth, urls in expected.items():
            with self.subTest(depth=depth):
                crawler, _, indexer = self.build(max_depth=depth)
                depths = crawler.crawl(START)
                self.assertEqual(set(depths), urls)
                self.assertTrue(all(indexer.is_indexed(u) for u in urls))
                self.assertLessEqual(max(depths.values()), depth)

    def test_depths_record_the_level_of_each_page(self):
        crawler, _, _ = self.build(max_depth=2)
        depths = crawler.crawl(START)
        self.assertEqual(depths[START], 0)
        self.assertEqual(depths[ROBOT], 1)
        self.assertEqual(depths[PAGERANK], 2)

    def test_pages_at_max_depth_are_indexed_but_not_followed(self):
        crawler, browser, _ = self.build(max_depth=1)
        crawler.crawl(START)
        self.assertNotIn(STRUKTURY, browser.requested)
        self.assertNotIn(PAGERANK, browser.requested)

    def test_depth_five_is_allowed_and_terminates(self):
        crawler, _, _ = self.build(max_depth=5, max_pages=30)
        self.assertEqual(len(crawler.crawl(START)), 7)


class TestCrawlerLimitsAndFilters(SeleniumStubbedCase):
    def test_max_links_per_page_selects_only_first_n_valid_links(self):
        crawler, _, _ = self.build(max_depth=1, max_links_per_page=2)
        self.assertEqual(set(crawler.crawl(START)), {START, INDEKS, ROBOT})

    def test_blocked_link_does_not_use_up_link_quota(self):
        # BLOCKED stoi w linkach przed ROBOT i RANKING, a mimo to oba mieszczą się w limicie 3
        crawler, _, _ = self.build(max_depth=1, max_links_per_page=3)
        self.assertEqual(set(crawler.crawl(START)), {START, INDEKS, ROBOT, RANKING})

    def test_max_pages_caps_downloads(self):
        crawler, browser, indexer = self.build(max_depth=5, max_pages=3)
        crawler.crawl(START)
        downloads = [u for u in browser.requested if u != ROBOTS_URL]
        self.assertEqual(len(downloads), 3)
        self.assertEqual(len(indexer), 3)

    def test_blocked_namespace_anchor_and_foreign_urls_never_requested(self):
        crawler, browser, _ = self.build(max_depth=3)
        crawler.crawl(START)
        for url in browser.requested:
            self.assertNotIn("Zablokowana_strona", url)
            self.assertNotIn("Specjalna:", url)
            self.assertNotIn("#", url)
            self.assertNotIn("en.wikipedia.org", url)

    def test_each_url_requested_once_and_robots_fetched_once(self):
        crawler, browser, _ = self.build(max_depth=3)
        crawler.crawl(START)
        self.assertEqual(len(browser.requested), len(set(browser.requested)))
        self.assertEqual(browser.requested.count(ROBOTS_URL), 1)

    def test_start_url_blocked_by_robots_is_not_downloaded(self):
        crawler, browser, indexer = self.build(browser=FakeBrowser(PAGES, robots="User-agent: *\nDisallow: /wiki/"))
        self.assertEqual(crawler.crawl(START), {})
        self.assertEqual(browser.requested, [ROBOTS_URL])
        self.assertEqual(len(indexer), 0)

    def test_allowed_domains_can_be_extended(self):
        en = "https://en.wikipedia.org/wiki/Search_engine"
        pages = dict(PAGES)
        pages[en] = ("Search engine", ["A search engine retrieves information."], [])
        browser = FakeBrowser(pages)
        browser.robots = ROBOTS
        crawler, _, _ = self.build(
            browser=browser,
            max_depth=1,
            max_links_per_page=5,
            allowed_domains=("pl.wikipedia.org", "en.wikipedia.org"),
        )
        self.assertIn(en, crawler.crawl(START))
        self.assertIn("https://en.wikipedia.org/robots.txt", browser.requested)


class TestCrawlerIndexerHandoff(SeleniumStubbedCase):
    def test_crawler_passes_content_and_address_to_indexer(self):
        indexer = mock.Mock(wraps=Indexer())
        crawler, _, _ = self.build(indexer=indexer, max_depth=0)
        crawler.crawl(START)
        indexer.add.assert_called_once_with(
            START,
            "Wyszukiwarka internetowa to program przeszukujący zasoby sieci.\nPrzykładem jest Google.",
        )

    def test_titles_are_stored_for_display(self):
        crawler, _, _ = self.build(max_depth=1)
        crawler.crawl(START)
        self.assertEqual(crawler.titles[INDEKS], "Indeks (informatyka)")

    def test_second_crawl_adds_nothing_new_to_the_index(self):
        indexer = Indexer()
        first, _, _ = self.build(indexer=indexer, max_depth=2)
        first.crawl(START)
        size = len(indexer)
        second, _, _ = self.build(indexer=indexer, max_depth=2)
        second.crawl(START)
        self.assertEqual(len(indexer), size)


class TestCrawlerFailures(SeleniumStubbedCase):
    def test_broken_page_is_skipped_and_rest_is_indexed(self):
        crawler, _, _ = self.build(browser=FakeBrowser(PAGES, fail_on={INDEKS}), max_depth=1)
        pages = crawler.crawl(START)
        self.assertNotIn(INDEKS, pages)
        self.assertIn(START, pages)
        self.assertIn(ROBOT, pages)

    def test_page_without_title_element_is_skipped(self):
        pages = dict(PAGES)
        del pages[ROBOT]
        crawler, _, _ = self.build(browser=FakeBrowser(pages), max_depth=1)
        self.assertNotIn(ROBOT, crawler.crawl(START))

    def test_page_with_empty_text_is_skipped(self):
        pages = dict(PAGES)
        pages[ROBOT] = ("Robot", ["   "], [])
        crawler, _, indexer = self.build(browser=FakeBrowser(pages), max_depth=1)
        self.assertNotIn(ROBOT, crawler.crawl(START))
        self.assertFalse(indexer.is_indexed(ROBOT))

    def test_failed_downloads_count_toward_max_pages(self):
        crawler, browser, _ = self.build(browser=FakeBrowser(PAGES, fail_on={INDEKS, ROBOT, RANKING}), max_depth=1, max_pages=3)
        crawler.crawl(START)
        self.assertEqual(len([u for u in browser.requested if u != ROBOTS_URL]), 3)

    def test_unreachable_robots_txt_aborts_crawl(self):
        crawler, browser, indexer = self.build(browser=FakeBrowser(PAGES, fail_on={ROBOTS_URL}))
        with self.assertRaises(RuntimeError):
            crawler.crawl(START)
        self.assertEqual(browser.requested, [ROBOTS_URL])
        self.assertEqual(len(indexer), 0)


class TestRateLimiting(SeleniumStubbedCase):
    def gaps(self, browser):
        times = browser.request_times
        return [b - a for a, b in zip(times, times[1:])]

    def test_at_least_one_second_between_all_requests_including_robots(self):
        crawler, browser, _ = self.build(max_depth=2)
        crawler.crawl(START)
        self.assertGreater(len(browser.requested), 3)
        for gap in self.gaps(browser):
            self.assertGreaterEqual(gap, 1.0 - 1e-9)

    def test_failed_requests_are_rate_limited_too(self):
        crawler, browser, _ = self.build(browser=FakeBrowser(PAGES, fail_on={INDEKS, ROBOT}), max_depth=1)
        crawler.crawl(START)
        for gap in self.gaps(browser):
            self.assertGreaterEqual(gap, 1.0 - 1e-9)

    def test_crawl_delay_from_robots_txt_is_respected(self):
        robots = "User-agent: *\nCrawl-delay: 3\nDisallow: /wiki/Zablokowana_strona\n"
        crawler, browser, _ = self.build(browser=FakeBrowser(PAGES, robots=robots), max_depth=1)
        crawler.crawl(START)
        for gap in self.gaps(browser):
            self.assertGreaterEqual(gap, 3.0 - 1e-9)


class TestWholeSystem(SeleniumStubbedCase):
    def test_crawl_index_search_end_to_end(self):
        crawler, _, indexer = self.build(max_depth=2)
        crawler.crawl(START)
        engine = SearchEngine(indexer)
        self.assertEqual(engine.search("indeks odwrócony")[0], INDEKS)
        self.assertEqual(engine.search("pagerank")[0], PAGERANK)
        self.assertEqual(engine.search("robot")[0], ROBOT)
        self.assertEqual(engine.search("samolot"), [])
        self.assertEqual(engine.search("drzewo"), [])  # poziom 3 nie został pobrany przy głębokości 2

    def test_deeper_crawl_makes_deeper_pages_searchable(self):
        crawler, _, indexer = self.build(max_depth=3)
        crawler.crawl(START)
        self.assertEqual(SearchEngine(indexer).search("drzewo hierarchiczna")[0], DRZEWO)

    def run_main(self, browser, inputs):
        out = io.StringIO()
        clock = FakeClock()
        real_limiter = wiki_search.RateLimiter
        with mock.patch.object(wiki_search, "make_driver", return_value=browser), \
             mock.patch.object(wiki_search, "RateLimiter",
                               lambda interval: real_limiter(interval, clock=clock, sleep=clock.sleep)), \
             mock.patch("builtins.input", side_effect=inputs), \
             contextlib.redirect_stdout(out):
            wiki_search.main()
        return out.getvalue()

    def test_main_full_session(self):
        browser = FakeBrowser(PAGES)
        out = self.run_main(browser, ["indeks", ""])
        self.assertIn(f"Indeks (informatyka) | {INDEKS} | ", out)
        self.assertIn("Zaindeksowano 6 stron", out)
        self.assertTrue(browser.quit_called)

    def test_main_full_session_with_bad_queries(self):
        out = self.run_main(FakeBrowser(PAGES), ["???", "samolot", "robot", ""])
        self.assertEqual(out.count("Brak wyników."), 2)
        self.assertIn("Robot internetowy | ", out)

    def test_main_when_robots_unreachable(self):
        browser = FakeBrowser(PAGES, fail_on={ROBOTS_URL})
        out = self.run_main(browser, [])
        self.assertIn("Nie udało się pobrać stron", out)
        self.assertTrue(browser.quit_called)


@unittest.skipUnless(os.environ.get("RUN_LIVE") == "1", "ustaw RUN_LIVE=1, aby uruchomić z prawdziwym Chrome i Wikipedią")
class TestLive(unittest.TestCase):
    def test_live_crawl_and_search(self):
        driver = wiki_search.make_driver()
        try:
            indexer = Indexer()
            crawler = Crawler(driver, indexer, RateLimiter(1.0), max_depth=1, max_links_per_page=2, max_pages=3)
            crawler.crawl(START_URL)
        finally:
            driver.quit()
        self.assertGreaterEqual(len(indexer), 1)
        self.assertTrue(SearchEngine(indexer).search("wyszukiwarka"))


if __name__ == "__main__":
    unittest.main()