"""Tests for translate.py with a fake model (the real one runs in GitHub Actions)."""
import translate


class FakeTranslator:
    calls = 0

    def translate_many(self, texts):
        FakeTranslator.calls += 1
        return [t.replace("Travaux", "Works").replace("arrêt non desservi", "stop not served") for t in texts]


def events():
    return [
        {"id": "a", "lang": "fr", "title": "Travaux - Ligne 56", "text": "arrêt non desservi\nTravaux"},
        {"id": "b", "lang": "en", "title": "Works", "text": "Stop not served"},
    ]


def test_french_events_get_english(tmp_path):
    evs = events()
    status = translate.translate_events(evs, FakeTranslator, tmp_path / "c.json")
    assert evs[0]["title_en"] == "Works - Ligne 56"
    assert evs[0]["text_en"] == "stop not served\nWorks"
    assert "title_en" not in evs[1]
    assert "translated 1 of 1" in status


def test_cache_avoids_retranslating(tmp_path):
    cache = tmp_path / "c.json"
    translate.translate_events(events(), FakeTranslator, cache)
    before = FakeTranslator.calls
    translate.translate_events(events(), FakeTranslator, cache)
    assert FakeTranslator.calls == before


def test_failure_keeps_original(tmp_path):
    def broken():
        raise RuntimeError("model download blocked")
    evs = events()
    status = translate.translate_events(evs, broken, tmp_path / "c.json")
    assert "title_en" not in evs[0] and "translated 0 of 1" in status


def test_sentence_split_keeps_lines():
    assert translate.split_sentences("Un. Deux.\nTrois") == [["Un.", "Deux."], ["Trois"]]


def test_clean_removes_sentencepiece_markers():
    # Real output seen from the model in GitHub Actions.
    assert translate.clean("▁Due to an undeclared▁demonstration,▁line 91") == "Due to an undeclared demonstration, line 91"
