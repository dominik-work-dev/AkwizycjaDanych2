"""Testy jednostkowe: tokenizacja, RateLimiter, Indexer, SearchEngine, robots.txt, walidacja Crawlera, main()."""
import contextlib
import io
import unittest
from unittest import mock

import src.app.wiki_search.wiki_search as wiki_search
from src.app.wiki_search.wiki_search import (
    Crawler,
    Indexer,
    RateLimiter,
    SearchEngine,
    is_allowed,
    parse_robots,
    tokenize,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_engine(docs):
    indexer = Indexer()
    for url, text in docs.items():
        indexer.add(url, text)
    return indexer, SearchEngine(indexer)


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(tokenize("Kot, pies! Łódź."), ["kot", "pies", "łódź"])

    def test_empty_and_punctuation_only(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("?! ..."), [])

    def test_digits_and_polish_letters(self):
        self.assertEqual(tokenize("Rok 2024 żółć"), ["rok", "2024", "żółć"])


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = RateLimiter(1.0, clock=self.clock, sleep=self.clock.sleep)

    def test_first_wait_does_not_sleep(self):
        self.limiter.wait()
        self.assertEqual(self.clock.now, 0.0)

    def test_second_immediate_wait_sleeps_full_interval(self):
        self.limiter.wait()
        self.limiter.wait()
        self.assertAlmostEqual(self.clock.now, 1.0)

    def test_wait_sleeps_only_the_remaining_time(self):
        self.limiter.wait()
        self.clock.now += 0.4
        self.limiter.wait()
        self.assertAlmostEqual(self.clock.now, 1.0)

    def test_no_sleep_when_interval_already_elapsed(self):
        self.limiter.wait()
        self.clock.now += 5
        self.limiter.wait()
        self.assertAlmostEqual(self.clock.now, 5.0)

    def test_raise_min_interval_only_goes_up(self):
        self.limiter.raise_min_interval(3)
        self.assertEqual(self.limiter.min_interval, 3)
        self.limiter.raise_min_interval(2)
        self.limiter.raise_min_interval(None)
        self.assertEqual(self.limiter.min_interval, 3)


class TestIndexer(unittest.TestCase):
    def test_add_new_page_returns_true_and_marks_indexed(self):
        indexer = Indexer()
        self.assertFalse(indexer.is_indexed("u1"))
        self.assertTrue(indexer.add("u1", "kot siedzi na dachu"))
        self.assertTrue(indexer.is_indexed("u1"))
        self.assertEqual(len(indexer), 1)

    def test_add_same_url_again_is_ignored(self):
        indexer = Indexer()
        indexer.add("u1", "kot")
        self.assertFalse(indexer.add("u1", "zupełnie inny tekst"))
        self.assertEqual(len(indexer), 1)
        _, engine = indexer, SearchEngine(indexer)
        self.assertEqual(engine.search("kot"), ["u1"])
        self.assertEqual(engine.search("inny"), [])

    def test_empty_content_is_indexed_but_never_found(self):
        indexer, engine = make_engine({"a": "", "b": "kot"})
        self.assertTrue(indexer.is_indexed("a"))
        self.assertEqual(engine.search("kot"), ["b"])

    def test_idf_never_zero_even_for_term_in_every_document(self):
        indexer, _ = make_engine({"a": "x y", "b": "x z"})
        self.assertGreaterEqual(indexer.idf("x"), 1.0)

    def test_idf_of_unknown_term_is_none(self):
        indexer, _ = make_engine({"a": "x"})
        self.assertIsNone(indexer.idf("nieznane"))

    def test_incremental_adds_equal_building_at_once(self):
        docs = {"a": "kot pies kot", "b": "pies ryba", "c": "ryba ptak kot"}
        _, once = make_engine(docs)
        incremental_indexer = Indexer()
        incremental = SearchEngine(incremental_indexer)
        for url, text in docs.items():
            incremental_indexer.add(url, text)
            incremental.search("kot")  # zapytanie w trakcie nie psuje indeksu
        self.assertEqual(incremental.search_scored("kot pies"), once.search_scored("kot pies"))


class TestSearchEngine(unittest.TestCase):
    def setUp(self):
        _, self.engine = make_engine({"u1": "kot siedzi na dachu", "u2": "pies biega po parku"})

    def test_search_returns_list_of_urls_best_first(self):
        result = self.engine.search("kot")
        self.assertEqual(result, ["u1"])
        self.assertTrue(all(isinstance(u, str) for u in result))

    def test_unknown_terms_return_empty(self):
        self.assertEqual(self.engine.search("samolot"), [])

    def test_empty_or_punctuation_query_returns_empty(self):
        self.assertEqual(self.engine.search("  "), [])
        self.assertEqual(self.engine.search("?!"), [])

    def test_top_k(self):
        _, engine = make_engine({f"u{i}": f"słowo numer{i}" for i in range(7)})
        self.assertEqual(len(engine.search("słowo", top_k=5)), 5)
        self.assertEqual(len(engine.search("słowo")), 7)

    def test_rarer_term_weighs_more(self):
        _, engine = make_engine({"d1": "alfa beta", "d2": "alfa gamma", "d3": "alfa delta"})
        scores = dict(engine.search_scored("alfa beta"))
        self.assertEqual(engine.search("alfa beta")[0], "d1")
        self.assertGreater(scores["d1"], scores["d2"])

    def test_scores_in_zero_one_and_sorted_descending(self):
        _, engine = make_engine({"a": "kot pies kot", "b": "pies ryba", "c": "ryba ptak"})
        scored = engine.search_scored("kot pies ryba")
        for _, score in scored:
            self.assertGreater(score, 0.0)
            self.assertLessEqual(score, 1.0 + 1e-9)
        values = [s for _, s in scored]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_query_equal_to_document_scores_one(self):
        _, engine = make_engine({"a": "kot pies", "b": "ryba"})
        self.assertAlmostEqual(engine.search_scored("kot pies")[0][1], 1.0, places=6)

    def test_best_match_added_last_is_still_returned_first(self):
        _, engine = make_engine({"slaby": "kot pies ryba ptak lis", "sredni": "kot pies ryba", "najlepszy": "kot"})
        self.assertEqual(engine.search("kot"), ["najlepszy", "sredni", "slaby"])
        scores = [s for _, s in engine.search_scored("kot")]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_search_urls_match_scored_order(self):
        _, engine = make_engine({"a": "kot", "b": "kot pies ryba", "c": "kot pies"})
        self.assertEqual(engine.search("kot"), [u for u, _ in engine.search_scored("kot")])


class TestRobots(unittest.TestCase):
    def setUp(self):
        self.rp = parse_robots("User-agent: *\nDisallow: /w/")

    def test_blocks_disallowed_path(self):
        self.assertFalse(is_allowed(self.rp, "https://pl.wikipedia.org/w/index.php"))

    def test_permits_article(self):
        self.assertTrue(is_allowed(self.rp, "https://pl.wikipedia.org/wiki/Kot"))

    def test_empty_text_allows_all(self):
        self.assertTrue(is_allowed(parse_robots(""), "https://pl.wikipedia.org/wiki/Kot"))


class TestCrawlerValidation(unittest.TestCase):
    def make(self, **kw):
        return Crawler(mock.Mock(), Indexer(), RateLimiter(0), **kw)

    def test_depth_must_be_between_zero_and_five(self):
        for bad in (-1, 6):
            with self.assertRaises(ValueError):
                self.make(max_depth=bad)
        for good in (0, 5):
            self.make(max_depth=good)

    def test_limits_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.make(max_pages=0)
        with self.assertRaises(ValueError):
            self.make(max_links_per_page=0)

    def test_start_url_outside_allowed_domains_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make().crawl("https://example.com/wiki/Kot")


class FakeCrawler:
    PAGES = {
        "https://pl.wikipedia.org/wiki/Kot": ("Kot", "kot siedzi na dachu"),
        "https://pl.wikipedia.org/wiki/Pies": ("Pies", "pies biega po parku"),
    }
    error = None
    pages = PAGES

    def __init__(self, driver, indexer, limiter, **kwargs):
        self.indexer = indexer
        self.titles = {}
        self.depths = {}

    def crawl(self, start_url):
        if FakeCrawler.error:
            raise FakeCrawler.error
        for url, (title, text) in FakeCrawler.pages.items():
            self.indexer.add(url, text)
            self.titles[url] = title
            self.depths[url] = 0
        return self.depths


class TestMain(unittest.TestCase):
    def setUp(self):
        FakeCrawler.error = None
        FakeCrawler.pages = FakeCrawler.PAGES

    def run_main(self, inputs, make_driver=None):
        driver = mock.MagicMock()
        out = io.StringIO()
        with mock.patch.object(wiki_search, "make_driver", make_driver or mock.Mock(return_value=driver)), \
             mock.patch.object(wiki_search, "Crawler", FakeCrawler), \
             mock.patch("builtins.input", side_effect=inputs) as inp, \
             contextlib.redirect_stdout(out):
            wiki_search.main()
        return out.getvalue(), driver, inp

    def test_prints_results_and_handles_bad_queries(self):
        out, driver, _ = self.run_main(["kot", "   ", "xyzxyz", ""])
        self.assertIn("Kot | https://pl.wikipedia.org/wiki/Kot | ", out)
        self.assertEqual(out.count("Brak wyników."), 2)
        driver.quit.assert_called_once()

    def test_reports_crawl_failure_and_closes_driver(self):
        FakeCrawler.error = RuntimeError("brak sieci")
        out, driver, inp = self.run_main([])
        self.assertIn("Nie udało się pobrać stron", out)
        driver.quit.assert_called_once()
        inp.assert_not_called()

    def test_reports_when_no_pages_indexed(self):
        FakeCrawler.pages = {}
        out, _, inp = self.run_main([])
        self.assertIn("Nie zaindeksowano żadnych stron", out)
        inp.assert_not_called()

    def test_exits_cleanly_on_eof(self):
        _, driver, _ = self.run_main(EOFError())
        driver.quit.assert_called_once()

    def test_friendly_message_when_browser_cannot_start(self):
        out, _, inp = self.run_main([], make_driver=mock.Mock(side_effect=RuntimeError("no chrome")))
        self.assertIn("Nie udało się uruchomić przeglądarki", out)
        inp.assert_not_called()


if __name__ == "__main__":
    unittest.main()