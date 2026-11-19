# Projekt 2 - Wyszukiwarka pełnotekstowa dla stron www (Wikipedia).
https://github.com/dominik-work-dev/AkwizycjaDanych2

## Wymagania

- Python 3.15
- Biblioteki zawarte w requirements.txt


## Instalacja

```
pip install -r requirements.txt
```

## Uruchamianie

```
python src/app/wiki_search/wiki_search.py
python src/app/wiki_search/wiki_search_ui.py
```

## Testy:

```
pip install pytest
pytest src/app/tests/test_wiki_search.py -v
pytest src/app/tests/test_pipeline.py -v
```