"""Free, offline French -> English translation for operator notices.

Uses the open-source Argos Translate fr->en model (CTranslate2 + SentencePiece), run
directly so no heavy dependencies are needed. Nothing is sent to a third-party API.

The model (~100 MB) is downloaded once into MODEL_DIR; GitHub Actions caches that folder.
Translations are cached by text in CACHE_PATH so each notice is translated only once.
If anything fails, the original text is kept and the app shows it untranslated.
"""
import hashlib
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
ROOT = Path(__file__).parent
MODEL_DIR = Path(os.environ.get("PULSE_MODEL_DIR", ROOT / ".cache" / "argos-fr-en"))
CACHE_PATH = Path(os.environ.get("PULSE_TRANSLATION_CACHE", ROOT / ".cache" / "translations.json"))
MAX_CACHE = 5000
USER_AGENT = "ParisPulse/1.0 (personal travel app)"


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ensure_model(model_dir=MODEL_DIR):
    """Download and unpack the fr->en model if it isn't there yet. Returns the folder."""
    model_dir = Path(model_dir)
    if any(model_dir.rglob("model.bin")):
        return model_dir
    index = json.loads(_get(INDEX_URL, timeout=30))
    pkg = next(p for p in index if p.get("from_code") == "fr" and p.get("to_code") == "en")
    model_dir.mkdir(parents=True, exist_ok=True)
    archive = model_dir / "model.zip"
    archive.write_bytes(_get(pkg["links"][0], timeout=300))
    with zipfile.ZipFile(archive) as z:
        z.extractall(model_dir)
    archive.unlink()
    return model_dir


def split_sentences(text):
    """Keep line breaks; split long lines into sentences so the model sees short inputs."""
    out = []
    for line in str(text).split("\n"):
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9#])", line.strip())
        out.append([p for p in parts if p])
    return out


def clean(text):
    """Remove SentencePiece word markers ("▁") the decoder can leave behind."""
    return " ".join(str(text).replace("\u2581", " ").split())


class Translator:
    def __init__(self, model_dir=MODEL_DIR):
        import ctranslate2          # imported here so the collector runs without it
        import sentencepiece

        root = ensure_model(model_dir)
        model_path = next(root.rglob("model.bin")).parent
        sp_path = next(root.rglob("sentencepiece.model"))
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(sp_path))
        self.model = ctranslate2.Translator(str(model_path), device="cpu", inter_threads=2)

    def translate_many(self, texts):
        """Translate a list of strings, keeping each one's line structure."""
        structure, flat = [], []
        for text in texts:
            lines = split_sentences(text)
            structure.append([len(sents) for sents in lines])
            flat.extend(s for sents in lines for s in sents)
        if not flat:
            return list(texts)
        tokens = [self.sp.encode(s, out_type=str) for s in flat]
        results = self.model.translate_batch(tokens, max_batch_size=32, beam_size=2)
        translated = [clean(self.sp.decode(r.hypotheses[0])) for r in results]
        out, i = [], 0
        for counts in structure:
            lines = []
            for n in counts:
                lines.append(" ".join(translated[i:i + n]))
                i += n
            out.append("\n".join(lines))
        return out


def _key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_cache(path=CACHE_PATH):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache, path=CACHE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(cache) > MAX_CACHE:              # keep the most recent entries
        cache = dict(list(cache.items())[-MAX_CACHE:])
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def translate_events(events, translator_factory=Translator, cache_path=CACHE_PATH):
    """Add title_en / text_en to French events. Returns a short status string.

    Never raises: on any failure the events are left as they are.
    """
    french = [e for e in events if e.get("lang") == "fr"]
    if not french:
        return "no French notices"
    cache = load_cache(cache_path)
    todo = sorted({t for e in french for t in (e.get("title"), e.get("text")) if t and _key(t) not in cache})
    if todo:
        try:
            translator = translator_factory()
            for text, result in zip(todo, translator.translate_many(todo)):
                cache[_key(text)] = result
            save_cache(cache, cache_path)
        except Exception as exc:
            print(f"translation unavailable: {type(exc).__name__}: {str(exc)[:160]}")
    done = 0
    for e in french:
        title, text = cache.get(_key(e.get("title") or "")), cache.get(_key(e.get("text") or ""))
        if title or text:
            e["title_en"], e["text_en"] = title, text
            done += 1
    return f"translated {done} of {len(french)} French notices ({len(todo)} new texts)"
