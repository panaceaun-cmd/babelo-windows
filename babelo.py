# -*- coding: utf-8 -*-
"""
Babelo для Windows — живой перевод любого звука, который играет на компьютере.
F10: первое нажатие — перевод включён; ещё раз — выезжает меню; два раза подряд — перевод выключен.
Распознавание (Whisper) -> перевод (NLLB) -> озвучка (голос Windows или Edge), оригинал заглушается, субтитры белым по чёрному.
"""
import os, sys, json, time, threading, queue, ctypes, traceback, re, tempfile, math, gc, subprocess, asyncio, datetime

# программа без консоли: у неё нет stdout/stderr, а библиотеки в них пишут
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
from ctypes import wintypes

if os.name != "nt":
    print("Babelo работает только в Windows")
    sys.exit(1)

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

APP = "Babelo"
APP_BUILD = 0                      # номер сборки: при сборке на GitHub подставляется автоматически
UPDATE_REPO = "panaceaun-cmd/babelo-windows"
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
MODELS_DIR = os.path.join(DATA, "models")
NLLB_DIR = os.path.join(MODELS_DIR, "nllb")
os.makedirs(MODELS_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA, "babelo.log")
SETTINGS_FILE = os.path.join(DATA, "settings.json")
MUTED_FILE = os.path.join(DATA, "muted.json")
MASTER_FILE = os.path.join(DATA, "master_muted.flag")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# код языка: (название, код NLLB, основной код языка Windows, голоса Edge)
LANGS = {
    "ru": ("Русский", "rus_Cyrl", 0x19, ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]),
    "en": ("English", "eng_Latn", 0x09, ["en-US-AriaNeural", "en-US-GuyNeural"]),
    "es": ("Español", "spa_Latn", 0x0A, ["es-ES-ElviraNeural", "es-ES-AlvaroNeural"]),
    "de": ("Deutsch", "deu_Latn", 0x07, ["de-DE-KatjaNeural", "de-DE-ConradNeural"]),
    "fr": ("Français", "fra_Latn", 0x0C, ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"]),
    "uk": ("Українська", "ukr_Cyrl", 0x22, ["uk-UA-PolinaNeural", "uk-UA-OstapNeural"]),
    "pl": ("Polski", "pol_Latn", 0x15, ["pl-PL-ZofiaNeural", "pl-PL-MarekNeural"]),
    "it": ("Italiano", "ita_Latn", 0x10, ["it-IT-ElsaNeural", "it-IT-DiegoNeural"]),
    "pt": ("Português", "por_Latn", 0x16, ["pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"]),
    "tr": ("Türkçe", "tur_Latn", 0x1F, ["tr-TR-EmelNeural", "tr-TR-AhmetNeural"]),
    "nl": ("Nederlands", "nld_Latn", 0x13, ["nl-NL-ColetteNeural", "nl-NL-MaartenNeural"]),
    "zh": ("中文", "zho_Hans", 0x04, ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]),
    "ja": ("日本語", "jpn_Jpan", 0x11, ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"]),
    "ko": ("한국어", "kor_Hang", 0x12, ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural"]),
    "ar": ("العربية", "arb_Arab", 0x01, ["ar-SA-ZariyahNeural", "ar-SA-HamedNeural"]),
    "hi": ("हिन्दी", "hin_Deva", 0x39, ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"]),
}
WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3"]
TEST_PHRASE = {"ru": "Привет! Это проверка голоса Babelo.", "en": "Hello! This is a Babelo voice test."}

DEFAULTS = {"target": "ru", "source": "auto", "whisper": "small", "phrase_sec": 5, "pause_ms": 400,
            "speed": 100, "volume": 100, "mute_original": True, "engine": "sapi", "voice": "", "threads": 4, "github_token": "",
            "mode": "live", "pause_s": 2, "repeats": 1, "block_sec": 60, "subs": "translated",
            "esc_stops": True, "banner_always": False}

# ---------------------------------------------------------------- журнал и настройки
LOG_LINES = []
LOG_TOTAL = [0]
_log_lock = threading.Lock()

def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    with _log_lock:
        LOG_LINES.append(line)
        LOG_TOTAL[0] += 1
        del LOG_LINES[:-300]
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s

CFG = load_settings()

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def clean_text(t):
    t = re.sub(r"[^\w\s.,!?;:'\"()\-–—…]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()

def to16k(x, sr):
    if sr == 16000 or len(x) == 0:
        return x
    if sr % 16000 == 0:
        k = sr // 16000
        n = len(x) // k * k
        return x[:n].reshape(-1, k).mean(axis=1)
    idx = np.linspace(0, len(x) - 1, int(len(x) * 16000 / sr))
    return np.interp(idx, np.arange(len(x)), x)

# ---------------------------------------------------------------- модели
class Cancelled(Exception):
    pass

def fmt_time(sec):
    sec = int(max(0, sec))
    return "%d мин %02d с" % (sec // 60, sec % 60) if sec >= 60 else "%d с" % sec

def fetch_repo(repo, patterns, dest, title, progress, cancel):
    """Скачивает файлы модели с huggingface.co: с докачкой, с прогрессом, без tqdm и без Xet (они у вас зависали)."""
    import requests, fnmatch, urllib.parse
    headers = {"User-Agent": "Babelo/2"}
    progress("%s: связываюсь с huggingface.co…" % title, None)
    r = requests.get("https://huggingface.co/api/models/%s" % repo, params={"blobs": "true"}, headers=headers, timeout=30)
    r.raise_for_status()
    files = [(s["rfilename"], int(s.get("size") or 0)) for s in r.json().get("siblings", [])
             if any(fnmatch.fnmatch(s["rfilename"], p) for p in patterns)]
    if not files:
        raise RuntimeError("в репозитории %s нет файлов модели" % repo)
    total = sum(sz for _, sz in files) or 1
    got_done = 0
    last_ui, last_b, last_t, speed = 0.0, 0, time.time(), 0.0
    os.makedirs(dest, exist_ok=True)
    for name, size in files:
        path = os.path.join(dest, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and (size == 0 or os.path.getsize(path) == size):
            got_done += size or os.path.getsize(path)
            continue
        part = path + ".part"
        attempts = 0
        while True:
            if cancel.is_set():
                raise Cancelled()
            have = os.path.getsize(part) if os.path.exists(part) else 0
            try:
                hdr = dict(headers)
                if have:
                    hdr["Range"] = "bytes=%d-" % have
                url = "https://huggingface.co/%s/resolve/main/%s" % (repo, urllib.parse.quote(name))
                with requests.get(url, headers=hdr, stream=True, timeout=(20, 40), allow_redirects=True) as resp:
                    if resp.status_code == 416:                      # докачивать нечего
                        break
                    resp.raise_for_status()
                    if have and resp.status_code != 206:
                        have = 0                                     # сервер не умеет докачку — сначала
                    with open(part, "ab" if have else "wb") as f:
                        for chunk in resp.iter_content(1 << 20):
                            if cancel.is_set():
                                raise Cancelled()
                            f.write(chunk)
                            have += len(chunk)
                            now = time.time()
                            if now - last_ui > 0.4:
                                cur = got_done + have
                                inst = (cur - last_b) / max(now - last_t, 1e-3)
                                speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
                                last_b, last_t, last_ui = cur, now, now
                                eta = (total - cur) / speed if speed > 1 else 0
                                progress("%s: %s — %d%%  (%d из %d МБ, %.1f МБ/с, осталось ~%s)" % (
                                    title, name, cur * 100 // total, cur >> 20, total >> 20, speed / 1048576.0,
                                    fmt_time(eta) if eta else "?"), cur / float(total))
                break
            except Cancelled:
                raise
            except Exception as e:
                attempts += 1
                if attempts >= 6:
                    raise
                log("обрыв связи при загрузке %s: %s — продолжаю (попытка %d)" % (name, str(e)[:80], attempts))
                progress("%s: обрыв связи, продолжаю… (попытка %d)" % (title, attempts), None)
                time.sleep(3)
        if size and os.path.getsize(part) != size:
            os.remove(part)
            raise RuntimeError("файл %s скачан не полностью, нажмите «Скачать модели» ещё раз" % name)
        os.replace(part, path)
        got_done += size or os.path.getsize(path)

# ---------------------------------------------------------------- обновления
def gh_headers(accept="application/vnd.github+json"):
    h = {"User-Agent": "Babelo", "Accept": accept}
    tok = str(CFG.get("github_token", "")).strip()
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h

def check_update():
    """(номер последней сборки, файл Babelo-windows.zip) из последнего релиза на GitHub."""
    import requests
    r = requests.get("https://api.github.com/repos/%s/releases/latest" % UPDATE_REPO, headers=gh_headers(), timeout=20)
    if r.status_code == 404:
        raise RuntimeError("релизы недоступны: репозиторий закрытый. Сделайте его публичным "
                           "(Settings → Change visibility) или впишите токен GitHub в окне программы")
    r.raise_for_status()
    j = r.json()
    m = re.search(r"(\d+)\s*$", j.get("tag_name", ""))
    build = int(m.group(1)) if m else 0
    asset = next((a for a in j.get("assets", []) if a["name"] == "Babelo-windows.zip"), None)
    return build, asset

def download_asset(asset, dest, progress, cancel):
    import requests
    tok = str(CFG.get("github_token", "")).strip()
    url = asset["url"] if tok else asset["browser_download_url"]
    hdr = gh_headers("application/octet-stream" if tok else "*/*")
    total = int(asset.get("size") or 0) or 1
    last = 0.0
    last_b, last_t, speed = 0, time.time(), 0.0
    for attempt in range(3):
        try:
            got = 0
            with requests.get(url, headers=hdr, stream=True, timeout=(20, 40), allow_redirects=True) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        if cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last > 0.4:
                            inst = (got - last_b) / max(now - last_t, 1e-3)
                            speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
                            last_b, last_t, last = got, now, now
                            eta = (total - got) / speed if speed > 1 else 0
                            progress("Обновление: %d%% (%d из %d МБ, %.1f МБ/с, осталось ~%s)" % (
                                got * 100 // total, got >> 20, total >> 20, speed / 1048576.0, fmt_time(eta) if eta else "?"),
                                got / float(total))
            if got < total and int(asset.get("size") or 0):
                raise RuntimeError("файл скачан не полностью")
            return
        except Cancelled:
            raise
        except Exception as e:
            if attempt == 2:
                raise
            log("обрыв при загрузке обновления: %s — пробую ещё раз" % e)
            time.sleep(3)

def launch_updater(zip_path):
    """Скрипт ждёт закрытия программы, распаковывает новую версию ПОВЕРХ старой и запускает её. Модели и настройки не трогает."""
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    tmp = os.path.join(tempfile.gettempdir(), "babelo_update_dir")
    q = lambda p: p.replace("'", "''")
    script = (
        "$zip = '%s'; $app = '%s'; $tmp = '%s'; $logf = '%s'\r\n"
        "Start-Sleep -Seconds 3\r\n"
        "try {\r\n"
        "  if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }\r\n"
        "  New-Item -ItemType Directory -Force -Path $tmp | Out-Null\r\n"
        "  tar -xf $zip -C $tmp\r\n"
        "  if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $tmp 'Babelo'))) { Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force }\r\n"
        "  robocopy \"$tmp\\Babelo\" \"$app\" /E /R:10 /W:2 /NFL /NDL /NJH /NJS | Out-Null\r\n"
        "  if ($LASTEXITCODE -ge 8) { \"robocopy: ошибка $LASTEXITCODE\" | Out-File -Append $logf }\r\n"
        "} catch { $_ | Out-File -Append $logf }\r\n"
        "Start-Process -FilePath (Join-Path $app 'Babelo.exe')\r\n"
        "Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue\r\n"
        "Remove-Item -Force $zip -ErrorAction SilentlyContinue\r\n"
    ) % (q(zip_path), q(app_dir), q(tmp), q(os.path.join(DATA, "update.log")))
    ps1 = os.path.join(tempfile.gettempdir(), "babelo_update.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1],
                     creationflags=0x08000000 | 0x00000008, close_fds=True)

class ModelHub:
    def __init__(self):
        self.whisper = None
        self.wkey = None
        self.nllb = None
        self.sp = None
        self.lock = threading.Lock()
        self.rev_cache = {}

    @staticmethod
    def whisper_dir(size):
        return os.path.join(MODELS_DIR, "whisper-" + size)

    def ready(self, size):
        return (os.path.exists(os.path.join(self.whisper_dir(size), "model.bin"))
                and os.path.exists(os.path.join(NLLB_DIR, "model.bin")))

    def download(self, size, progress, cancel):
        fetch_repo("Systran/faster-whisper-%s" % size,
                   ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
                   self.whisper_dir(size), "Распознавание речи (Whisper %s)" % size, progress, cancel)
        fetch_repo("skywood/nllb-200-distilled-600M-ct2-int8",
                   ["config.json", "model.bin", "sentencepiece.bpe.model", "shared_vocabulary.json"],
                   NLLB_DIR, "Модель перевода (NLLB)", progress, cancel)
        progress("Модели готовы ✓", 1.0)

    def load(self, size, threads):
        with self.lock:
            from faster_whisper import WhisperModel
            key = (size, threads)
            if self.wkey != key:
                self.whisper = None
                gc.collect()
                self.whisper = WhisperModel(self.whisper_dir(size), device="cpu", compute_type="int8",
                                            cpu_threads=threads, num_workers=1)
                self.wkey = key
            if self.nllb is None:
                import ctranslate2
                import sentencepiece as spm
                self.nllb = ctranslate2.Translator(NLLB_DIR, device="cpu", compute_type="int8",
                                                   intra_threads=threads, inter_threads=1)
                self.sp = spm.SentencePieceProcessor()
                self.sp.load(os.path.join(NLLB_DIR, "sentencepiece.bpe.model"))

    def release(self):
        with self.lock:
            self.whisper = None; self.wkey = None; self.nllb = None; self.sp = None
        gc.collect()

    def transcribe(self, audio, language):
        segs, info = self.whisper.transcribe(audio.astype(np.float32), language=language, beam_size=1, best_of=1,
                                             temperature=0.0, vad_filter=False, condition_on_previous_text=False,
                                             without_timestamps=True)
        text = " ".join(s.text.strip() for s in segs).strip()
        return text, info.language

    # --- перевод
    @staticmethod
    def _sentences(text):
        return [p for p in re.split(r"(?<=[.!?…。！？])\s+", text) if p.strip()]

    @staticmethod
    def _words(tokens, skip=0):
        """Группы индексов токенов по словам (слово начинается с токена «▁»)."""
        groups = []
        for i, t in enumerate(tokens):
            if i < skip:
                continue
            if t.startswith("▁") or not groups:
                groups.append([i])
            else:
                groups[-1].append(i)
        return groups

    def translate_plain(self, text, src, tgt):
        s_code, t_code = LANGS[src][1], LANGS[tgt][1]
        out = []
        for sent in self._sentences(text):
            toks = [s_code] + self.sp.encode(sent, out_type=str) + ["</s>"]
            res = self.nllb.translate_batch([toks], target_prefix=[[t_code]], beam_size=2, max_decoding_length=200)
            hyp = res[0].hypotheses[0]
            if hyp and hyp[0] == t_code:
                hyp = hyp[1:]
            out.append(self.sp.decode(hyp))
        return " ".join(out)

    def translate_aligned(self, text, src, tgt):
        """Перевод + список (слово перевода, слово оригинала) по вниманию модели; слово оригинала может быть None."""
        s_code, t_code = LANGS[src][1], LANGS[tgt][1]
        pairs, parts = [], []
        for sent in self._sentences(text):
            toks = self.sp.encode(sent, out_type=str)
            source = [s_code] + toks + ["</s>"]
            kw = dict(target_prefix=[[t_code]], beam_size=2, max_decoding_length=200)
            try:
                res = self.nllb.translate_batch([source], return_attention=True, **kw)
            except TypeError:
                res = self.nllb.translate_batch([source], **kw)
            r = res[0]
            hyp = list(r.hypotheses[0])
            skip = 1 if hyp and hyp[0] == t_code else 0
            att = None
            try:
                a = getattr(r, "attention", None)
                if a:
                    a = a[0]
                if a and len(a) in (len(hyp), len(hyp) - skip):
                    att = a
            except Exception:
                att = None
            offset = len(hyp) - len(att) if att else 0
            s_groups = self._words(toks)
            s_words = [self.sp.decode([toks[i] for i in g]).strip() for g in s_groups]
            tok2word = {}
            for wi, g in enumerate(s_groups):
                for i in g:
                    tok2word[i + 1] = wi                  # +1: в начале стоит код языка
            for g in self._words(hyp, skip):
                tw = self.sp.decode([hyp[i] for i in g]).strip()
                if not tw:
                    continue
                ow = None
                if att:
                    vec = {}
                    for i in g:
                        row = i - offset
                        if 0 <= row < len(att):
                            for j, v in enumerate(att[row]):
                                if j in tok2word:
                                    vec[j] = vec.get(j, 0.0) + float(v)
                    if vec:
                        ow = s_words[tok2word[max(vec, key=vec.get)]]
                pairs.append((tw, ow))
            parts.append(self.sp.decode(hyp[skip:]))
        return " ".join(parts), pairs

    def reverse_word(self, word, src, tgt):
        """Запасной способ для подсказки: перевести слово субтитров обратно на язык оригинала."""
        key = (word, src, tgt)
        if key not in self.rev_cache:
            w = re.sub(r"^[^\w]+|[^\w]+$", "", word) or word
            self.rev_cache[key] = self.translate_plain(w, tgt, src).strip(" .!?…")
        return self.rev_cache[key]

HUB = ModelHub()

# ---------------------------------------------------------------- озвучка
_mci = None

def mci(cmd):
    global _mci
    if _mci is None:
        _mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(256)
    err = _mci(cmd, buf, 255, 0)
    return err, buf.value

def play_mp3(path, volume, stop_evt):
    alias = "bab%d" % (time.time_ns() % 10 ** 9)
    err, _ = mci('open "%s" type mpegvideo alias %s' % (path, alias))
    if err:
        raise RuntimeError("не удалось открыть звук (MCI %d)" % err)
    try:
        mci("setaudio %s volume to %d" % (alias, max(0, min(1000, int(volume) * 10))))
        mci("play " + alias)
        time.sleep(0.15)
        t0 = time.time()
        while time.time() - t0 < 60 and not stop_evt.is_set():
            _, mode = mci("status %s mode" % alias)
            if mode != "playing":
                break
            time.sleep(0.05)
    finally:
        mci("stop " + alias)
        mci("close " + alias)

def sapi_rate(pct):
    return max(-10, min(10, round(10 * math.log(max(int(pct), 10) / 100.0, 3))))

def play_mp3_async(path, volume):
    alias = "bab%d" % (time.time_ns() % 10 ** 9)
    err, _ = mci('open "%s" type mpegvideo alias %s' % (path, alias))
    if err:
        raise RuntimeError("не удалось открыть звук (MCI %d)" % err)
    mci("setaudio %s volume to %d" % (alias, max(0, min(1000, int(volume) * 10))))
    mci("play " + alias)
    return alias

def mp3_playing(alias):
    _, mode = mci("status %s mode" % alias)
    return mode == "playing"

def mp3_close(alias):
    mci("stop " + alias)
    mci("close " + alias)

class Tts:
    """Голос Windows (SAPI, офлайн) или Edge (онлайн, качественнее). Один объект — на один поток."""
    def __init__(self):
        self.sapi = None
        self.alias = None

    def _sapi(self):
        if self.sapi is None:
            import win32com.client
            self.sapi = win32com.client.Dispatch("SAPI.SpVoice")
        return self.sapi

    def _prepare(self, lang, pct):
        sp = self._sapi()
        voices = sp.GetVoices()
        chosen = None
        want = CFG.get("voice", "")
        for i in range(voices.Count):
            v = voices.Item(i)
            if want and v.GetDescription() == want:
                chosen = v
                break
        if chosen is None:
            prim = LANGS[lang][2]
            for i in range(voices.Count):
                v = voices.Item(i)
                try:
                    lid = int(v.GetAttribute("Language").split(";")[0], 16)
                except Exception:
                    continue
                if (lid & 0x3FF) == prim:
                    chosen = v
                    break
            if chosen is None:
                log("В Windows нет голоса для языка «%s»: Параметры → Время и язык → Речь" % LANGS[lang][0])
        if chosen is not None:
            sp.Voice = chosen
        sp.Rate = sapi_rate(pct)
        sp.Volume = int(CFG["volume"])
        return sp

    def edge_file(self, text, lang, pct):
        import edge_tts
        names = LANGS[lang][3]
        voice = CFG.get("voice", "") if CFG.get("voice", "") in names else names[0]
        path = os.path.join(tempfile.gettempdir(), "babelo_%d.mp3" % time.time_ns())
        rate = "%+d%%" % max(-50, min(100, int(pct) - 100))

        async def gen():
            await edge_tts.Communicate(text, voice, rate=rate).save(path)
        asyncio.run(gen())
        return path

    def speak(self, text, lang, stop_evt, pct=None):
        """Говорит и ждёт конца."""
        pct = int(pct or CFG["speed"])
        if CFG["engine"] == "edge":
            try:
                path = self.edge_file(text, lang, pct)
                try:
                    play_mp3(path, int(CFG["volume"]), stop_evt)
                finally:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                return
            except Exception as e:
                log("Edge недоступен (%s), говорю голосом Windows" % e)
        sp = self._prepare(lang, pct)
        sp.Speak(text, 1)                                  # асинхронно, ждём сами
        while not sp.WaitUntilDone(80):
            if stop_evt.is_set():
                sp.Speak("", 3)
                break

    def say_now(self, text, lang, pct, mp3=None):
        """Начинает говорить сразу и не ждёт (для режимов с перемоткой видео)."""
        self.stop_now()
        if mp3:
            self.alias = play_mp3_async(mp3, int(CFG["volume"]))
            return
        sp = self._prepare(lang, pct)
        sp.Speak(text, 3)                                  # асинхронно, предыдущую фразу обрезает

    def busy(self):
        try:
            if self.alias:
                return mp3_playing(self.alias)
            if self.sapi is not None:
                return not self.sapi.WaitUntilDone(0)
        except Exception:
            pass
        return False

    def wait_done(self, stop_evt, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout and not stop_evt.is_set() and self.busy():
            time.sleep(0.05)

    def stop_now(self):
        try:
            if self.alias:
                mp3_close(self.alias)
                self.alias = None
            if self.sapi is not None:
                self.sapi.Speak("", 3)
        except Exception:
            pass

def list_sapi_voices(lang):
    try:
        import win32com.client
        voices = win32com.client.Dispatch("SAPI.SpVoice").GetVoices()
        prim = LANGS[lang][2]
        match, other = [], []
        for i in range(voices.Count):
            v = voices.Item(i)
            d = v.GetDescription()
            try:
                lid = int(v.GetAttribute("Language").split(";")[0], 16)
                (match if (lid & 0x3FF) == prim else other).append(d)
            except Exception:
                other.append(d)
        return match or other
    except Exception:
        return []

# ---------------------------------------------------------------- заглушение оригинала
class Muter:
    """Выключает звук ВСЕХ других программ на время озвучки (звук самого Babelo остаётся)."""
    def __init__(self):
        self.ids = set()

    def _sessions(self):
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetAllSessions()

    def _save(self):
        try:
            if self.ids:
                with open(MUTED_FILE, "w", encoding="utf-8") as f:
                    json.dump(sorted(self.ids), f)
            elif os.path.exists(MUTED_FILE):
                os.remove(MUTED_FILE)
        except Exception:
            pass

    def mute(self):
        try:
            me = os.getpid()
            for s in self._sessions():
                try:
                    if s.ProcessId == me:
                        continue
                    v = s.SimpleAudioVolume
                    if not v.GetMute():
                        v.SetMute(1, None)
                        self.ids.add(s.Identifier)
                except Exception:
                    continue
            self._save()
        except Exception as e:
            log("не удалось заглушить оригинал: %s" % e)

    def restore(self):
        if not self.ids:
            return
        try:
            for s in self._sessions():
                try:
                    if s.Identifier in self.ids:
                        s.SimpleAudioVolume.SetMute(0, None)
                except Exception:
                    pass
        except Exception as e:
            log("не удалось вернуть звук: %s" % e)
        finally:
            self.ids.clear()
            self._save()

    def recover(self):
        """После аварийного закрытия: вернуть звук тому, что осталось заглушённым."""
        try:
            with open(MUTED_FILE, encoding="utf-8") as f:
                self.ids = set(json.load(f))
        except Exception:
            return
        self.restore()

# ---------------------------------------------------------------- общая громкость и управление плеером
class MasterMute:
    """Выключает звук колонок целиком. Запись звука компьютера (loopback) при этом продолжает всё слышать."""
    def _endpoint(self):
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        try:
            return dev.EndpointVolume                      # новые версии pycaw
        except Exception:
            pass
        from pycaw.pycaw import IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))

    def set(self, mute):
        try:
            ep = self._endpoint()
            if mute:
                if ep.GetMute():
                    return False                           # пользователь сам выключил звук — не трогаем
                ep.SetMute(1, None)
                with open(MASTER_FILE, "w") as f:
                    f.write("1")
            else:
                if os.path.exists(MASTER_FILE):
                    ep.SetMute(0, None)
                    os.remove(MASTER_FILE)
            return True
        except Exception as e:
            log("общий звук: %s" % e)
            return False

    def recover(self):
        if os.path.exists(MASTER_FILE):
            self.set(False)

class MediaCtl:
    """Управление плеером Windows (YouTube в Chrome/Edge, Spotify и др.): позиция, пауза, перемотка."""
    def __init__(self):
        self.err = ""
        self.mgr = None
        self.state = None
        self.state_t = 0.0
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()

    def _run(self, coro, timeout=3.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _session(self):
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        if self.mgr is None:
            self.mgr = await M.request_async()
        return self.mgr.get_current_session()

    async def _info(self):
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as PS
        s = await self._session()
        if s is None:
            return None
        pb = s.get_playback_info()
        tl = s.get_timeline_properties()
        playing = pb.playback_status == PS.PLAYING
        pos = tl.position.total_seconds()
        rate = float(pb.playback_rate or 1.0)
        try:
            if playing:
                pos += max(0.0, (datetime.datetime.now(datetime.timezone.utc) - tl.last_updated_time).total_seconds()) * rate
        except Exception:
            pass
        seek = bool(pb.controls.is_playback_position_enabled) and tl.end_time.total_seconds() > 0
        return {"pos": int(pos * 1000), "playing": playing, "seek": seek, "rate": rate}

    def refresh(self):
        try:
            self.state = self._run(self._info())
            self.state_t = time.time()
            self.err = ""
        except Exception as e:
            self.state = None
            self.err = str(e)[:100]

    def _poll(self):
        while True:
            self.refresh()
            time.sleep(0.25)

    def info(self):
        return self.state

    def available(self):
        return self.state is not None

    def can_seek(self):
        return self.state is not None and self.state["seek"]

    def position_ms(self):
        st = self.state
        if st is None:
            return None
        pos = st["pos"]
        if st["playing"]:
            pos += int((time.time() - self.state_t) * 1000 * st["rate"])
        return pos

    def _call(self, name, *args):
        async def go():
            s = await self._session()
            if s is None:
                return False
            return await getattr(s, name)(*args)
        try:
            ok = self._run(go())
        except Exception as e:
            log("плеер: %s" % e)
            ok = False
        self.refresh()
        return ok

    def play(self):
        return self._call("try_play_async")

    def pause(self):
        return self._call("try_pause_async")

    def seek(self, ms):
        return self._call("try_change_playback_position_async", int(ms) * 10000)

    def wait_near(self, ms, timeout=3.0):
        """Ждёт, пока позиция плеера окажется рядом с ms (после перемотки)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            p = self.position_ms()
            if p is not None and abs(p - ms) < 2500:
                return True
            time.sleep(0.05)
            self.refresh()
        return False

# ---------------------------------------------------------------- движок перевода
def com_init():
    for mod in ("pythoncom", "comtypes"):
        try:
            __import__(mod).CoInitialize()
        except Exception:
            pass

class Phrase:
    def __init__(self, audio, t, prep, start, end):
        self.audio, self.t, self.prep, self.start, self.end = audio, t, prep, start, end

class Restart(Exception):
    """Видео обновили, запустили заново или перемотали: подгрузка начинается с нового места."""

class Item:
    """Подготовленная фраза непрерывного перевода: когда она звучит в видео и что сказать."""
    def __init__(self, start, end, sub, speak, mp3, pct):
        self.start, self.end, self.sub, self.speak, self.mp3, self.pct = start, end, sub, speak, mp3, pct

class Engine:
    def __init__(self, app):
        self.app = app
        self.stop_evt = threading.Event()
        self.gate = False                  # True: свой голос играет, звук не слушаем
        self.phase = 0                     # 0 обычный, 1 подготовка блока, 2 показ блока с переводом
        self.phrase_q = queue.Queue()
        self.speak_q = queue.Queue()
        self.hist = []
        self.items = []
        self.pending = 0
        self.plock = threading.Lock()
        self.loud_prep = 0
        self.stats = {"heard": 0, "recognised": 0, "translated": 0, "spoken": 0, "errors": 0}
        self.last_loud = 0.0
        self.listening = False
        self.guard_until = 0.0             # до этого момента свои команды плееру не считаем действиями пользователя
        self.spoken = set()
        self.silent_prep = False

    def start(self):
        for fn in (self.capture, self.worker, self.speaker, self.controller):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.stop_evt.set()

    def wait(self, sec):
        t0 = time.time()
        while time.time() - t0 < sec and not self.stop_evt.is_set():
            time.sleep(0.05)

    # --- слушаем звук компьютера (WASAPI loopback) и режем на фразы
    def capture(self):
        p = None
        stream = None
        media = self.app.media
        try:
            import pyaudiowpatch as pa
            p = pa.PyAudio()
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not spk.get("isLoopbackDevice"):
                for lb in p.get_loopback_device_info_generator():
                    if spk["name"] in lb["name"]:
                        spk = lb
                        break
                else:
                    raise RuntimeError("не найдено устройство захвата звука (loopback)")
            ch = int(spk["maxInputChannels"])
            sr = int(spk["defaultSampleRate"])
            raw_q = queue.Queue()

            def cb(in_data, frame_count, time_info, status):
                raw_q.put(in_data)
                return (None, pa.paContinue)

            stream = p.open(format=pa.paInt16, channels=ch, rate=sr, frames_per_buffer=int(sr * 0.1), input=True,
                            input_device_index=spk["index"], stream_callback=cb)
            stream.start_stream()
            self.listening = True
            log("слушаю: %s (%d Гц, %d кан.)" % (spk["name"], sr, ch))
            buf, n_samples, silent, start_pos = [], 0, 0, None
            while not self.stop_evt.is_set():
                try:
                    data = raw_q.get(timeout=0.15)
                except queue.Empty:
                    data = None                                   # тишина: Windows не присылает пакеты
                if data is None:
                    m = np.zeros(1600, dtype=np.float32)
                else:
                    a = np.frombuffer(data, dtype=np.int16)
                    a = a[: len(a) // ch * ch].reshape(-1, ch).astype(np.float32) / 32768.0
                    m = to16k(a.mean(axis=1), sr)
                if self.gate:
                    buf, n_samples, silent = [], 0, 0
                    continue
                loud = len(m) > 0 and float(np.abs(m).mean()) > 0.010
                if loud:
                    self.last_loud = time.time()
                    if self.phase == 1:
                        self.loud_prep += 1
                if loud and not buf:
                    pos = None if CFG["mode"] == "live" else media.position_ms()
                    start_pos = None if pos is None else max(0, pos - 150)
                silent = 0 if loud else silent + 1
                if loud or buf:
                    buf.append(m)
                    n_samples += len(m)
                if not buf:
                    continue
                prep = self.phase == 1
                max_sec = max(int(CFG["phrase_sec"]), 12) if prep else int(CFG["phrase_sec"])
                pause_frames = max(1, int(CFG["pause_ms"] // 100))
                if silent < pause_frames and n_samples <= max_sec * 16000:
                    continue
                audio = np.concatenate(buf)
                trail = silent * 100
                buf, n_samples, silent = [], 0, 0
                if len(audio) < 6400:
                    continue
                end_pos = None
                if CFG["mode"] != "live":
                    pos = media.position_ms()
                    if pos is not None:
                        end_pos = max(pos - trail + 200, (start_pos or 0) + 500)
                self.stats["heard"] += 1
                if prep:
                    with self.plock:
                        self.pending += 1
                self.phrase_q.put(Phrase(audio, time.time(), prep, start_pos, end_pos))
                if CFG["mode"] == "live" and self.phase == 0:
                    while self.phrase_q.qsize() > 2:              # медленный ПК: держим только свежие фразы
                        try:
                            self.phrase_q.get_nowait()
                        except queue.Empty:
                            break
        except Exception as e:
            self.app.fail("захват звука", e)
        finally:
            try:
                if stream is not None:
                    stream.stop_stream(); stream.close()
                if p is not None:
                    p.terminate()
            except Exception:
                pass

    # --- общие шаги: понять фразу и перевести
    def understand(self, audio):
        secs = len(audio) / 16000.0
        target = CFG["target"]
        forced = None if CFG["source"] == "auto" else CFG["source"]
        if forced is None and secs < 4 and self.hist:                # короткие фразы: держимся языка предыдущих
            maj = max(set(self.hist), key=self.hist.count)
            if self.hist.count(maj) >= 2:
                forced = maj
        text, det = HUB.transcribe(audio, forced)
        frm = forced or det
        if forced is None:
            self.hist = (self.hist + [det])[-5:]
        if not text:
            return None
        if frm == target:
            log("[%s] %s — язык уже целевой" % (frm, text[:60]))
            return None
        if len(text) > secs * 25 + 40 or frm not in LANGS:
            log("[%s] «%s» — отброшено (мусор или язык не поддерживается)" % (frm, text[:60]))
            return None
        self.stats["recognised"] += 1
        return text, frm

    def make_sub(self, text, frm):
        target = CFG["target"]
        tr, pairs = HUB.translate_aligned(text, frm, target)
        speak = clean_text(tr)[:500]
        if not any(c.isalpha() for c in speak):
            return None
        self.stats["translated"] += 1
        log("[%s→%s] %s → %s" % (frm, target, text[:60], speak[:60]))
        return {"orig": text, "tr": tr, "pairs": pairs, "frm": frm, "to": target}, speak

    # --- распознавание и перевод в потоке
    def worker(self):
        com_init()
        tts = Tts()
        try:
            self.app.ui(self.app.subs.show, "Babelo: загрузка моделей…")
            HUB.load(CFG["whisper"], int(CFG["threads"]))
            self.app.ui(self.app.subs.hide)
        except Exception as e:
            self.app.fail("загрузка моделей", e)
            return
        notified = False
        while not self.stop_evt.is_set():
            try:
                ph = self.phrase_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if ph.prep:
                    self.prepare(ph, tts)
                elif time.time() - ph.t > 25:
                    pass
                else:
                    mode = CFG["mode"]
                    if mode == "interrupted":
                        if self.app.media.available():
                            self.process_interrupted(ph, tts)
                        else:
                            if not notified:
                                notified = True
                                self.app.ui(self.app.subs.flash, "Прерывистый режим: плеер с управлением не найден, работаю в обычном", 5000)
                            self.process_live(ph)
                    elif mode == "live":
                        self.process_live(ph)
            except Exception as e:
                self.stats["errors"] += 1
                log("ошибка фразы: %s" % e)
                log(traceback.format_exc())
                self.app.ui(self.app.subs.flash, "⚠ " + str(e)[:80], 3500)
            finally:
                if ph.prep:
                    with self.plock:
                        self.pending -= 1
        tts.stop_now()

    # --- обычный режим: говорим, пока следующая фраза уже распознаётся
    def process_live(self, ph):
        res = self.understand(ph.audio)
        if not res:
            return
        made = self.make_sub(*res)
        if made:
            self.speak_q.put((made[0], made[1], time.time()))

    def speaker(self):
        com_init()
        tts = Tts()
        while not self.stop_evt.is_set():
            try:
                sub, text, t = self.speak_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.time() - t > 20:
                continue
            try:
                self.gate = True
                if CFG["mute_original"]:
                    self.app.muter.mute()
                self.app.ui(self.app.subs.show_sub, sub, "tr")
                tts.speak(text, CFG["target"], self.stop_evt)
                self.stats["spoken"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                log("ошибка озвучки: %s" % e)
                self.app.ui(self.app.subs.flash, "⚠ озвучка: " + str(e)[:70], 3500)
            finally:
                if self.speak_q.empty():
                    self.app.muter.restore()
                    self.app.ui(self.app.subs.hide)
                    time.sleep(0.4)
                    if self.speak_q.empty() and self.phase == 0:
                        self.gate = False
        self.app.muter.restore()
        self.gate = False
        self.app.ui(self.app.subs.hide)
        tts.stop_now()

    # --- прерывистый режим: оригинал, потом тот же отрывок с переводом (повторы и паузы настраиваются)
    def fragment(self, start, end, say, tts, mute_others):
        media = self.app.media
        media.seek(start)
        media.wait_near(start)
        if mute_others and CFG["mute_original"]:
            self.app.muter.mute()
        if say:
            try:
                tts.say_now(say[0], CFG["target"], int(CFG["speed"]), say[1])
            except Exception as e:
                log("озвучка отрывка: %s" % e)
        media.play()
        t0 = time.time()
        limit = (end - start) / 1000.0 + 4
        while not self.stop_evt.is_set() and time.time() - t0 < limit:
            pos = media.position_ms()
            if pos is not None and pos >= end:
                break
            time.sleep(0.03)
        media.pause()
        if say:
            tts.wait_done(self.stop_evt, 40)
        if mute_others:
            self.app.muter.restore()

    def process_interrupted(self, ph, tts):
        media = self.app.media
        target = CFG["target"]
        self.gate = True
        mp3 = None
        try:
            media.pause()
            res = self.understand(ph.audio)
            if not res:
                return
            made = self.make_sub(*res)
            if not made:
                return
            sub, speak = made
            n = max(1, int(CFG["repeats"]))
            gap = float(CFG["pause_s"])
            seek_ok = media.can_seek() and ph.start is not None and ph.end is not None and ph.end > ph.start
            if CFG["engine"] == "edge":
                try:
                    mp3 = tts.edge_file(speak, target, int(CFG["speed"]))
                except Exception as e:
                    log("Edge: %s" % e)
            for i in range(n):
                if self.stop_evt.is_set():
                    break
                if seek_ok and i > 0:                            # оригинал ещё раз
                    self.app.ui(self.app.subs.show_sub, sub, "orig")
                    self.fragment(ph.start, ph.end, None, tts, False)
                self.wait(gap)                                   # пауза между фразой и переводом
                self.app.ui(self.app.subs.show_sub, sub, "tr")
                if seek_ok:                                      # тот же отрывок, но с голосом перевода
                    self.fragment(ph.start, ph.end, (speak, mp3), tts, True)
                else:                                            # перематывать нельзя: говорим на паузе
                    if CFG["mute_original"]:
                        self.app.muter.mute()
                    tts.speak(speak, target, self.stop_evt)
                    self.app.muter.restore()
                self.stats["spoken"] += 1
                self.wait(gap)
            self.app.ui(self.app.subs.hide)
            if seek_ok:
                media.seek(ph.end)
        finally:
            self.app.muter.restore()
            media.play()
            if mp3:
                try:
                    os.remove(mp3)
                except Exception:
                    pass
            time.sleep(0.4)
            self.gate = False

    # --- непрерывный режим: отрезок подгружается вперёд, потом видео идёт только с переводом
    def prepare(self, ph, tts):
        res = self.understand(ph.audio)
        if not res or ph.start is None or ph.end is None:
            return
        made = self.make_sub(*res)
        if not made:
            return
        sub, speak = made
        win = max(1.0, (ph.end - ph.start) / 1000.0)
        pct = int(min(220, max(int(CFG["speed"]), len(speak) / (13.0 * win) * 100)))   # успеть за окном фразы
        mp3 = None
        if CFG["engine"] == "edge":
            try:
                mp3 = tts.edge_file(speak, CFG["target"], pct)
            except Exception as e:
                log("Edge: %s" % e)
        self.items.append(Item(ph.start, ph.end, sub, speak, mp3, pct))
        if self.phase == 1 and self.silent_prep:                        # беззвучная подгрузка: перевод хотя бы в субтитрах
            self.app.ui(self.app.subs.show_sub, sub, "tr")

    def controller(self):
        com_init()
        tts = Tts()
        while not self.stop_evt.is_set():
            if CFG["mode"] == "continuous":
                try:
                    self.continuous(tts)
                except Exception as e:
                    log("непрерывный режим: %s" % e)
                    log(traceback.format_exc())
                    CFG["mode"] = "live"
                    save_settings()
                    self.app.ui(self.app.sync_ui)
                    self.app.ui(self.app.subs.flash, "Непрерывный режим остановлен: %s" % str(e)[:80], 5000)
            else:
                time.sleep(0.3)
        tts.stop_now()

    def guard(self, sec=2.0):
        self.guard_until = time.time() + sec

    def make_watch(self):
        """Замечает, что видео обновили, запустили заново или перемотали (не считая наших собственных команд)."""
        media = self.app.media
        w = {"playing": None, "pos": None, "t": None, "lost": None}

        def watch():
            st = media.info()
            now = time.time()
            if st is None:
                if w["lost"] is None:
                    w["lost"] = now
                return None
            if w["lost"] is not None:
                gone = now - w["lost"]
                w["lost"] = None
                w["t"] = None
                if gone > 0.7:
                    return "страница или видео обновлены"
            pos = media.position_ms()
            if now < self.guard_until:
                w.update(playing=st["playing"], pos=pos, t=now)
                return None
            reason = None
            if w["t"] is not None:
                if st["playing"] and not w["playing"]:
                    reason = "видео запущено"
                elif w["playing"] and pos is not None and w["pos"] is not None:
                    expected = w["pos"] + (now - w["t"]) * 1000 * st["rate"]
                    if abs(pos - expected) > 2500:
                        reason = "видео перемотано"
            w.update(playing=st["playing"], pos=pos, t=now)
            return reason
        return watch

    def continuous(self, tts):
        app = self.app
        media = app.media
        st = media.info()
        if st is None or not st["seek"]:
            app.ui(app.subs.flash, "Непрерывный режим: нужен плеер с перемоткой (YouTube в Chrome или Edge). "
                                   "Включён обычный режим.", 6000)
            CFG["mode"] = "live"
            save_settings()
            app.ui(app.sync_ui)
            return

        def alive():
            return not self.stop_evt.is_set() and CFG["mode"] == "continuous"
        first, next_start = True, None
        try:
            while alive():
                watch = self.make_watch()
                try:
                    next_start = self.cycle(tts, first, next_start, watch, alive)
                    first = False                                  # дальше отрезки идут подряд: оригинал больше не слышен
                except Restart as r:
                    log("непрерывный режим: %s — подгрузка начинается заново" % r)
                    first, next_start = True, None
                    tts.stop_now()
                    app.muter.restore()
                    app.master.set(False)
                    self.gate = False
                    self.phase = 0
                    app.ui(app.subs.hide)
                    app.ui(app.banner_idle)
                    self.wait(0.3)
        finally:
            app.master.set(False)
            app.muter.restore()
            tts.stop_now()
            self.phase = 0
            self.gate = False
            self.silent_prep = False
            app.ui(app.banner_idle)
            for it in self.items:
                if it.mp3:
                    try:
                        os.remove(it.mp3)
                    except Exception:
                        pass

    def cycle(self, tts, first, start_at, watch, alive):
        """Один отрезок: подгрузка -> возврат на начало -> показ с переводом. Возвращает, откуда начнётся следующий."""
        app = self.app
        media = app.media
        target = CFG["target"]
        block = max(1, int(CFG["block_sec"])) * 1000
        # --- 1. подгрузка. Первый отрезок идёт со слышимым оригиналом, следующие — без звука
        if start_at is not None:
            cur = media.position_ms()
            if cur is not None and abs(cur - start_at) > 800:
                self.guard(3.0)
                media.seek(start_at)
                media.wait_near(start_at)
                self.guard(1.5)
            p0 = start_at
        else:
            p0 = media.position_ms() or 0
        self.items = []
        self.spoken = set()
        self.loud_prep = 0
        self.gate = False
        self.silent_prep = not first
        self.phase = 1
        muted = False if first else app.master.set(True)
        if first:
            self.guard(2.0)
            media.play()                                           # видео запускается само
        t_play, last, unmuted = 0.0, time.time(), False
        while alive():
            now = time.time()
            st = media.info()
            if st and st["playing"]:
                t_play += now - last
            last = now
            r = watch()
            if r:
                raise Restart(r)
            pos = media.position_ms() or p0
            if pos >= p0 + block or t_play > block / 1000.0 * 2 + 30:
                break
            pct = max(0, min(100, int((pos - p0) * 100 / block)))
            app.ui(app.banner.show, "Непрерывный перевод: подгрузка %d %% %s" % (
                pct, "(оригинал слышно один раз)" if first else "(без звука, перевод — в субтитрах)"))
            if muted and not unmuted and pos - p0 > 6000 and self.loud_prep == 0:
                app.master.set(False)                              # без звука запись ничего не слышит — включаем
                unmuted = True
            time.sleep(0.15)
        if not alive():
            return None
        # --- 2. ждём только первых готовых фраз: остальные доперевёдутся, пока видео идёт с переводом
        t0 = time.time()
        while alive() and time.time() - t0 < 8 and not self.items:
            with self.plock:
                left = self.pending
            if left <= 0:
                break
            app.ui(app.banner.show, "Непрерывный перевод: перевожу первые фразы…")
            time.sleep(0.2)
        app.master.set(False)
        self.phase = 2
        self.gate = True
        app.muter.mute()                                           # оригинал выключен, остаётся только голос перевода
        app.ui(app.subs.hide)
        self.guard(4.0)
        media.seek(p0)
        media.wait_near(p0)
        self.guard(1.5)
        app.ui(app.banner_idle)
        # --- 3. показ с переводом; фразы, которые ещё доделываются, берутся по мере готовности
        t_play, last, shown = 0.0, time.time(), None
        while alive():
            now = time.time()
            st = media.info()
            if st and st["playing"]:
                t_play += now - last
            last = now
            r = watch()
            if r:
                raise Restart(r)
            pos = media.position_ms()
            if pos is None or pos >= p0 + block or t_play > block / 1000.0 * 2 + 30:
                break
            for it in sorted(list(self.items), key=lambda x: x.start):
                if id(it) in self.spoken or pos < it.start - 250:
                    continue
                self.spoken.add(id(it))
                if pos <= it.end + 1500:                           # не опоздавшие фразы
                    shown = it
                    app.ui(app.subs.show_sub, it.sub, "tr")
                    try:
                        tts.say_now(it.speak, target, it.pct, it.mp3)
                    except Exception as e:
                        log("озвучка: %s" % e)
            if shown is not None and pos > shown.end + 300 and not tts.busy():
                app.ui(app.subs.hide)
                shown = None
            time.sleep(0.03)
        self.stats["spoken"] += len(self.spoken)
        tts.wait_done(self.stop_evt, 15)                           # договорить последнюю фразу, пока видео идёт дальше
        app.ui(app.subs.hide)
        app.muter.restore()
        for it in self.items:
            if it.mp3:
                try:
                    os.remove(it.mp3)
                except Exception:
                    pass
        return p0 + block

# ---------------------------------------------------------------- окна
import tkinter.font as tkfont

BG = "#1c1c20"
MODES = [("live", "Обычный (по ходу)"),
         ("continuous", "Непрерывный: сначала подгрузка, потом только перевод"),
         ("interrupted", "Прерывистый: оригинал, потом тот же отрывок с переводом")]
SUBS = [("off", "Не показывать субтитры"), ("translated", "Переведённые субтитры"),
        ("original", "Субтитры на языке оригинала"), ("both", "Перевод и оригинал вместе")]

def label_of(pairs, key):
    return next((t for k, t in pairs if k == key), pairs[0][1])

def key_of(pairs, text):
    return next((k for k, t in pairs if t == text), pairs[0][0])

def make_scroll(parent, bg=None):
    """Прокручиваемая область: всё содержимое доступно колёсиком мыши, даже на маленьком экране."""
    kw = {"bg": bg} if bg else {}
    outer = tk.Frame(parent, **kw)
    canvas = tk.Canvas(outer, highlightthickness=0, **kw)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, **kw)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

    def wheel(e):
        canvas.yview_scroll(int(-e.delta / 120), "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    sb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return outer, inner, canvas

def add_slider(parent, dark, title, lo, hi, key):
    kw = dict(bg=BG, fg="white") if dark else {}
    lbl = tk.Label(parent, text="", anchor="w", justify="left", **kw)
    lbl.pack(fill="x", padx=12, pady=(6, 0))

    def upd(v):
        CFG[key] = int(float(v))
        lbl.config(text="%s: %d" % (title, CFG[key]))
    skw = dict(bg=BG, fg="white", troughcolor="#444", highlightthickness=0) if dark else {}
    sc = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", showvalue=False, command=upd, sliderlength=18,
                  width=12, **skw)
    sc.set(int(CFG[key]))
    sc.bind("<ButtonRelease-1>", lambda e: save_settings())
    sc.pack(fill="x", padx=12)
    upd(CFG[key])
    return lambda: sc.set(int(CFG[key]))

def add_controls(parent, dark, app):
    """Общий набор настроек для короткого и для главного меню. Возвращает функцию «обновить из настроек»."""
    kw = dict(bg=BG, fg="white") if dark else {}
    refresh = []

    def lab(text):
        tk.Label(parent, text=text, anchor="w", justify="left", **kw).pack(fill="x", padx=12, pady=(8, 0))

    lab("Режим перевода")
    cm = ttk.Combobox(parent, state="readonly", values=[t for _, t in MODES])
    cm.pack(fill="x", padx=12)

    def pick_mode(_=None):
        CFG["mode"] = key_of(MODES, cm.get())
        save_settings()
        app.on_mode_changed()
    cm.bind("<<ComboboxSelected>>", pick_mode)
    refresh.append(lambda: cm.set(label_of(MODES, CFG["mode"])))

    var = tk.BooleanVar()
    ckw = dict(selectcolor="#333", activebackground=BG, activeforeground="white", **kw) if dark else {}

    def chg():
        CFG["mute_original"] = bool(var.get())
        save_settings()
    tk.Checkbutton(parent, text="Заглушать оригинал на время перевода", variable=var, command=chg, anchor="w",
                   **ckw).pack(fill="x", padx=8, pady=(8, 0))
    refresh.append(lambda: var.set(bool(CFG["mute_original"])))

    lab("Субтитры")
    cs = ttk.Combobox(parent, state="readonly", values=[t for _, t in SUBS])
    cs.pack(fill="x", padx=12)

    def pick_subs(_=None):
        CFG["subs"] = key_of(SUBS, cs.get())
        save_settings()
    cs.bind("<<ComboboxSelected>>", pick_subs)
    refresh.append(lambda: cs.set(label_of(SUBS, CFG["subs"])))

    refresh.append(add_slider(parent, dark, "Прерывистый: пауза между фразой и переводом, с", 0, 10, "pause_s"))
    refresh.append(add_slider(parent, dark, "Прерывистый: сколько раз повторить (оригинал и перевод)", 1, 10, "repeats"))
    refresh.append(add_slider(parent, dark, "Непрерывный: длина отрезка подгрузки вперёд, с (1–180)", 1, 180, "block_sec"))
    refresh.append(add_slider(parent, dark, "Слушать фразу не дольше, с", 2, 15, "phrase_sec"))
    refresh.append(add_slider(parent, dark, "Пауза конца фразы, мс", 200, 1000, "pause_ms"))
    refresh.append(add_slider(parent, dark, "Скорость озвучки, %", 70, 150, "speed"))
    refresh.append(add_slider(parent, dark, "Громкость перевода, %", 10, 100, "volume"))
    for r in refresh:
        r()
    return lambda: [r() for r in refresh]

def exstyle(win, clickthrough):
    """Окно без фокуса; clickthrough=True — мышь проходит сквозь него."""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        style = u.GetWindowLongW(hwnd, -20) | 0x80 | 0x08000000
        style = (style | 0x20) if clickthrough else (style & ~0x20)
        u.SetWindowLongW(hwnd, -20, style)
    except Exception:
        pass

HINT = "Перевод видео и аудио включён F10. Меню F10. Выключить — Esc или дважды F10"

class Banner:
    """Подсказка самого вверху экрана; мышь проходит сквозь неё."""
    def __init__(self, root):
        self.root = root
        self.win = None
        self.lbl = None
        self.job = None

    def _ensure(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.97)
        w.configure(bg="black")
        self.lbl = tk.Label(w, text="", fg="white", bg="black", font=("Segoe UI", 14, "bold"), padx=18, pady=8)
        self.lbl.pack()
        w.withdraw()
        w.update_idletasks()
        exstyle(w, True)
        self.win = w

    def show(self, text, ms=None):
        self._ensure()
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        self.lbl.config(text=text)
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw = self.win.winfo_screenwidth()
        self.win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), 4))
        self.win.deiconify()
        self.win.lift()
        if ms:
            self.job = self.root.after(ms, self.hide)

    def hide(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.win is not None:
            self.win.withdraw()

class Subtitles:
    """Субтитры внизу экрана: белое на чёрном. Наведите мышь на слово перевода — сверху появится слово оригинала жёлтым."""
    def __init__(self, root):
        self.root = root
        self.win = None
        self.holder = None
        self.tip = None
        self.tip_lbl = None
        self.cur = None
        self.job = None

    def _ensure(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.97)
        w.configure(bg="black")
        self.holder = tk.Frame(w, bg="black")
        self.holder.pack(padx=28, pady=12)
        w.withdraw()
        w.update_idletasks()
        self.win = w

    def _begin(self):
        self._ensure()
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        self._hide_tip()
        for c in self.holder.winfo_children():
            c.destroy()

    def _place(self, hover):
        exstyle(self.win, not hover)
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, sh - h - 90)))
        self.win.deiconify()
        self.win.lift()

    def show(self, text):
        self._begin()
        sw = self.win.winfo_screenwidth()
        tk.Label(self.holder, text=text, fg="white", bg="black", font=("Segoe UI", 22, "bold"),
                 wraplength=int(sw * 0.8), justify="center").pack()
        self._place(False)

    def show_sub(self, sub, kind="tr"):
        mode = CFG.get("subs", "translated")
        if kind == "orig":
            lines = ["orig"] if mode in ("original", "both") else []
        else:
            lines = {"translated": ["tr"], "original": ["orig"], "both": ["orig", "tr"]}.get(mode, [])
        if not lines:
            self.hide()
            return
        self._begin()
        maxw = int(self.win.winfo_screenwidth() * 0.8)
        hover = False
        for ln in lines:
            if ln == "orig":
                two = len(lines) > 1
                tk.Label(self.holder, text=sub["orig"], fg="#dddddd" if two else "white", bg="black",
                         font=("Segoe UI", 18 if two else 22, "bold"), wraplength=maxw, justify="center").pack()
            elif sub.get("pairs"):
                self._flow(sub, maxw)
                hover = True
            else:
                tk.Label(self.holder, text=sub["tr"], fg="white", bg="black", font=("Segoe UI", 22, "bold"),
                         wraplength=maxw, justify="center").pack()
        self._place(hover)

    def _flow(self, sub, maxw):
        font = ("Segoe UI", 22, "bold")
        f = tkfont.Font(font=font)
        line, used = None, 0
        for tw, ow in sub["pairs"]:
            w = f.measure(tw + " ")
            if line is None or used + w > maxw:
                line = tk.Frame(self.holder, bg="black")
                line.pack(anchor="center")
                used = 0
            lbl = tk.Label(line, text=tw + " ", font=font, fg="white", bg="black", padx=0, pady=2)
            lbl.pack(side="left")
            lbl.bind("<Enter>", lambda e, l=lbl, o=ow, t=tw: self._enter(l, o, t, sub))
            lbl.bind("<Leave>", lambda e, l=lbl: self._leave(l))
            used += w

    def _enter(self, lbl, ow, tw, sub):
        try:
            lbl.config(bg="#333333")
        except tk.TclError:
            return
        self.cur = lbl
        if ow:
            self._tip(lbl, ow)
            return

        def work():                                          # слова нет в выравнивании: переводим слово обратно
            try:
                w = HUB.reverse_word(tw, sub["to"], sub["frm"])
            except Exception:
                w = ""
            if w:
                self.root.after(0, lambda: self._tip(lbl, w))
        threading.Thread(target=work, daemon=True).start()

    def _leave(self, lbl):
        try:
            lbl.config(bg="black")
        except tk.TclError:
            pass
        if self.cur is lbl:
            self.cur = None
        self._hide_tip()

    def _tip(self, lbl, text):
        if self.cur is not lbl:
            return
        try:
            if self.tip is None:
                t = tk.Toplevel(self.root)
                t.overrideredirect(True)
                t.attributes("-topmost", True)
                t.attributes("-alpha", 0.97)
                t.configure(bg="black")
                self.tip_lbl = tk.Label(t, text="", fg="#ffd400", bg="black", font=("Segoe UI", 22, "bold"), padx=14, pady=6)
                self.tip_lbl.pack()
                t.withdraw()
                t.update_idletasks()
                exstyle(t, True)
                self.tip = t
            self.tip_lbl.config(text=text)
            self.tip.update_idletasks()
            tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
            sw = self.tip.winfo_screenwidth()
            x = min(max(0, lbl.winfo_rootx() + lbl.winfo_width() // 2 - tw // 2), sw - tw)
            y = max(0, self.win.winfo_rooty() - th - 8)
            self.tip.geometry("%dx%d+%d+%d" % (tw, th, x, y))
            self.tip.deiconify()
            self.tip.lift()
        except tk.TclError:
            pass

    def _hide_tip(self):
        if self.tip is not None:
            try:
                self.tip.withdraw()
            except tk.TclError:
                pass

    def hide(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        self.cur = None
        self._hide_tip()
        if self.win is not None:
            self.win.withdraw()

    def flash(self, text, ms=3000):
        self.show(text)
        self.job = self.root.after(ms, self.hide)

class Menu:
    """Короткое меню, выезжающее справа. Заголовок и нижние кнопки всегда видны, середина прокручивается."""
    W = 390

    def __init__(self, app):
        self.app = app
        self.win = None

    @property
    def shown(self):
        return self.win is not None

    def toggle(self):
        self.hide() if self.shown else self.show()

    def show(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=BG)
        hdr = tk.Frame(w, bg=BG)
        hdr.pack(side="top", fill="x", padx=14, pady=(10, 2))
        tk.Label(hdr, text="Babelo — перевод идёт", fg="white", bg=BG, font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Button(hdr, text="✕", fg="white", bg=BG, bd=0, font=("Segoe UI", 14), activebackground="#333",
                  activeforeground="white", command=self.hide).pack(side="right")
        foot = tk.Frame(w, bg=BG)
        foot.pack(side="bottom", fill="x", padx=14, pady=(4, 10))
        for text, cmd in (("Все настройки", self.app.show_main), ("Полное закрытие программы", self.app.full_close)):
            tk.Button(foot, text=text, command=cmd, bg="#333", fg="white", activebackground="#444",
                      activeforeground="white", relief="flat", pady=5).pack(fill="x", pady=2)
        outer, inner, canvas = make_scroll(w, bg=BG)
        outer.pack(side="top", fill="both", expand=True)
        add_controls(inner, True, self.app)
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        h = min(inner.winfo_reqheight() + 120, sh - 80)
        y = max(10, (sh - h) // 2)
        self.win = w
        self._anim(w, sw, sw - self.W, y, h, 0, None)

    def _anim(self, w, x0, x1, y, h, step, done):
        try:
            x = x0 + (x1 - x0) * step // 10
            w.geometry("%dx%d+%d+%d" % (self.W, h, x, y))
            if step < 10:
                w.after(15, lambda: self._anim(w, x0, x1, y, h, step + 1, done))
            elif done:
                done()
        except tk.TclError:
            pass

    def hide(self):
        w = self.win
        if w is None:
            return
        self.win = None
        try:
            sw = w.winfo_screenwidth()
            geo = w.geometry()
            h = int(geo.split("x")[1].split("+")[0])
            y = int(geo.split("+")[2])
            self._anim(w, sw - self.W, sw, y, h, 0, w.destroy)
        except Exception:
            try:
                w.destroy()
            except Exception:
                pass

# ---------------------------------------------------------------- программа
class App:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Babelo for Windows — сборка %d" % APP_BUILD)
        sh = self.root.winfo_screenheight()
        self.root.geometry("780x%d" % min(980, sh - 90))
        self.events = queue.Queue()
        self.engine = None
        self.last_f10 = 0.0
        self.release_job = None
        self.muter = Muter()
        self.muter.recover()
        self.master = MasterMute()
        self.master.recover()
        self.media = MediaCtl()
        self.subs = Subtitles(self.root)
        self.banner = Banner(self.root)
        self.menu = Menu(self)
        self.hk_tid = None
        self.hidden_note = False
        self.log_shown = 0
        self.dl_cancel = threading.Event()
        self.dl_thread = None
        self.pending_update = None
        self.refresh_controls = lambda: None
        self._build_main()
        self.refresh_models_state()
        self.root.after(1000, self._tick)
        self.root.after(4000, lambda: self.check_updates(True))      # тихая проверка обновлений при запуске
        self.root.protocol("WM_DELETE_WINDOW", self.hide_main)
        threading.Thread(target=self._hotkey, daemon=True).start()
        self.root.after(50, self._poll)

    # --- безопасный вызов из любых потоков
    def ui(self, fn, *args):
        self.events.put((fn,) + args)

    def _poll(self):
        try:
            while True:
                item = self.events.get_nowait()
                try:
                    item[0](*item[1:])
                except Exception:
                    log(traceback.format_exc())
        except queue.Empty:
            pass
        with _log_lock:
            new = LOG_TOTAL[0] - self.log_shown
            fresh = LOG_LINES[-new:] if new > 0 else []
            self.log_shown = LOG_TOTAL[0]
        if fresh:
            self.logbox.config(state="normal")
            for line in fresh:
                self.logbox.insert("end", line + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        self.root.after(50, self._poll)

    # --- F10
    def _hotkey(self):
        u = ctypes.windll.user32
        msg = wintypes.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)          # создаёт очередь сообщений потока
        self.hk_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not u.RegisterHotKey(None, 1, 0x4000, 0x79):          # MOD_NOREPEAT, VK_F10
            self.ui(self.set_status, "Клавиша F10 занята другой программой. Закройте её и запустите Babelo заново.")
            return
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:                             # WM_HOTKEY
                self.ui(self.on_f10 if msg.wParam == 1 else self.on_esc)
            elif msg.message == 0x8001:                           # включить Esc (пока идёт перевод)
                u.RegisterHotKey(None, 2, 0x4000, 0x1B)
            elif msg.message == 0x8002:                           # вернуть Esc другим программам
                u.UnregisterHotKey(None, 2)

    def esc_hotkey(self, on):
        if self.hk_tid:
            ctypes.windll.user32.PostThreadMessageW(self.hk_tid, 0x8001 if on else 0x8002, 0, 0)

    def on_esc(self):
        if self.engine is not None:
            self.stop()

    def banner_idle(self):
        if self.engine is not None and CFG.get("banner_always"):
            self.banner.show(HINT)
        else:
            self.banner.hide()

    def on_f10(self):
        now = time.time()
        quick = now - self.last_f10 < 0.45
        self.last_f10 = now
        if self.engine is None:
            self.start()
        elif quick:
            self.menu.hide()
            self.stop()
        else:
            self.menu.toggle()

    def on_mode_changed(self):
        if CFG["mode"] != "live" and not self.media.available():
            self.subs.flash("Для этого режима нужен плеер Windows с управлением (YouTube в Chrome или Edge). "
                            "Сейчас такой плеер не найден — включите видео.", 6000)

    def sync_ui(self):
        try:
            self.refresh_controls()
        except Exception:
            pass

    def start(self):
        if not HUB.ready(CFG["whisper"]):
            self.show_main()
            self.set_status("Сначала скачайте модели (кнопка «Скачать модели»), потом нажмите F10.")
            return
        if self.release_job:
            self.root.after_cancel(self.release_job)
            self.release_job = None
        self.engine = Engine(self)
        self.engine.start()
        self.set_status("Перевод включён. F10 — меню, F10 два раза подряд — выключить.")
        self.banner.show(HINT, None if CFG.get("banner_always") else 9000)
        if CFG.get("esc_stops"):
            self.esc_hotkey(True)
        self.on_mode_changed()

    def stop(self):
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        self.muter.restore()
        self.master.set(False)
        self.esc_hotkey(False)
        self.menu.hide()
        self.subs.hide()
        self.banner.hide()
        try:
            self.media.play()                                     # не оставляем видео на паузе
        except Exception:
            pass
        self.set_status("Перевод выключен. F10 — включить.")
        self.release_job = self.root.after(180000, HUB.release)    # освободить память через 3 минуты

    def fail(self, where, exc):
        log("ОШИБКА (%s): %s" % (where, exc))
        log(traceback.format_exc())
        self.ui(self._fail_ui, where, exc)

    def _fail_ui(self, where, exc):
        self.stop()
        self.set_status("Остановлено: %s — %s" % (where, exc))
        self.subs.flash("Babelo остановлен: %s" % str(exc)[:90], 6000)

    def full_close(self):
        try:
            self.stop()
            self.muter.restore()
            self.master.set(False)
            self.subs.hide()
            self.root.destroy()
        finally:
            os._exit(0)

    # --- главное окно
    def hide_main(self):
        self.root.withdraw()
        if not self.hidden_note:
            self.hidden_note = True
            messagebox.showinfo("Babelo", "Babelo продолжает работать в фоне.\nF10 — включить перевод.\n"
                                          "Полностью закрыть: кнопка «Полное закрытие программы» (в окне или в меню).")

    def show_main(self):
        self.sync_ui()
        self.root.deiconify()
        self.root.lift()

    def set_status(self, text):
        self.status.config(text=text)
        log(text)

    def _build_main(self):
        r = self.root
        top = tk.Frame(r)
        top.pack(side="top", fill="x", padx=10, pady=6)
        self.status = tk.Label(top, text="Нажмите F10, когда модели скачаны.", anchor="w", justify="left",
                               wraplength=520, font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Полное закрытие программы", command=self.full_close).pack(side="right", padx=4)
        ttk.Label(r, text="F10: включить · ещё раз: меню · два раза подряд: выключить").pack(side="bottom", pady=3)
        self.logbox = tk.Text(r, height=7, state="disabled", bg="#111", fg="#ddd", font=("Consolas", 9))
        self.logbox.pack(side="bottom", fill="x", padx=10, pady=4)
        outer, inner, canvas = make_scroll(r)
        outer.pack(side="top", fill="both", expand=True)

        f = ttk.Frame(inner)
        f.pack(fill="x", padx=10, pady=4)
        names = [LANGS[c][0] for c in LANGS]
        codes = list(LANGS)
        self.v_src = tk.StringVar(value="Авто" if CFG["source"] == "auto" else LANGS.get(CFG["source"], ("Авто",))[0])
        self.v_dst = tk.StringVar(value=LANGS[CFG["target"]][0])
        self.v_whisper = tk.StringVar(value=CFG["whisper"])
        self.v_engine = tk.StringVar(value="Edge (онлайн, лучше качество)" if CFG["engine"] == "edge" else "Голос Windows (офлайн)")
        self.v_voice = tk.StringVar(value=CFG.get("voice", ""))

        def row(i, text, widget):
            ttk.Label(f, text=text).grid(row=i, column=0, sticky="w", pady=3)
            widget.grid(row=i, column=1, sticky="ew", padx=8, pady=3)
        f.columnconfigure(1, weight=1)
        cs = ttk.Combobox(f, textvariable=self.v_src, values=["Авто"] + names, state="readonly")
        cd = ttk.Combobox(f, textvariable=self.v_dst, values=names, state="readonly")
        cw = ttk.Combobox(f, textvariable=self.v_whisper, values=WHISPER_SIZES, state="readonly")
        ce = ttk.Combobox(f, textvariable=self.v_engine, state="readonly",
                          values=["Голос Windows (офлайн)", "Edge (онлайн, лучше качество)"])
        self.cv = ttk.Combobox(f, textvariable=self.v_voice, state="readonly")
        row(0, "Язык оригинала", cs)
        row(1, "Перевод на", cd)
        row(2, "Распознавание (tiny быстро … large-v3 точно)", cw)
        row(3, "Озвучка", ce)
        row(4, "Голос", self.cv)

        def pick_src(_=None):
            n = self.v_src.get()
            CFG["source"] = "auto" if n == "Авто" else codes[names.index(n)]
            save_settings()

        def fill_voices(_=None):
            lang = CFG["target"]
            vals = LANGS[lang][3] if CFG["engine"] == "edge" else list_sapi_voices(lang)
            self.cv.config(values=vals)
            if CFG.get("voice") not in vals:
                CFG["voice"] = vals[0] if vals else ""
            self.v_voice.set(CFG["voice"])

        def pick_dst(_=None):
            CFG["target"] = codes[names.index(self.v_dst.get())]
            save_settings()
            fill_voices()

        def pick_whisper(_=None):
            CFG["whisper"] = self.v_whisper.get()
            save_settings()
            self.refresh_models_state()
            if not HUB.ready(CFG["whisper"]):
                self.set_status("Для модели %s нужна загрузка — нажмите «Скачать модели»." % CFG["whisper"])

        def pick_engine(_=None):
            CFG["engine"] = "edge" if self.v_engine.get().startswith("Edge") else "sapi"
            save_settings()
            fill_voices()

        def pick_voice(_=None):
            CFG["voice"] = self.v_voice.get()
            save_settings()
        cs.bind("<<ComboboxSelected>>", pick_src)
        cd.bind("<<ComboboxSelected>>", pick_dst)
        cw.bind("<<ComboboxSelected>>", pick_whisper)
        ce.bind("<<ComboboxSelected>>", pick_engine)
        self.cv.bind("<<ComboboxSelected>>", pick_voice)
        fill_voices()

        # те же настройки, что в коротком меню: режимы, субтитры, паузы, повторы, громкость
        self.refresh_controls = add_controls(inner, False, self)
        self.v_esc = tk.BooleanVar(value=bool(CFG.get("esc_stops", True)))
        self.v_ban = tk.BooleanVar(value=bool(CFG.get("banner_always", False)))

        def chg2():
            CFG["esc_stops"] = bool(self.v_esc.get())
            CFG["banner_always"] = bool(self.v_ban.get())
            save_settings()
        ttk.Checkbutton(inner, text="Esc выключает перевод (пока перевод включён, Esc не работает в других программах)",
                        variable=self.v_esc, command=chg2).pack(anchor="w", padx=12, pady=(8, 0))
        ttk.Checkbutton(inner, text="Держать подсказку «F10 / Esc» вверху экрана всё время (иначе она видна 9 секунд)",
                        variable=self.v_ban, command=chg2).pack(anchor="w", padx=12)

        # потоки распознавания — только здесь
        lab = ttk.Label(inner, text="")
        lab.pack(anchor="w", padx=12, pady=(8, 0))

        def upd(v):
            CFG["threads"] = int(float(v))
            lab.config(text="Потоки распознавания (больше — быстрее, но тяжелее): %d" % CFG["threads"])
        sc = ttk.Scale(inner, from_=2, to=12, orient="horizontal", command=upd)
        sc.set(int(CFG["threads"]))
        sc.bind("<ButtonRelease-1>", lambda e: save_settings())
        sc.pack(fill="x", padx=12)
        upd(CFG["threads"])

        self.models_lbl = ttk.Label(inner, text="", font=("Segoe UI", 9, "bold"))
        self.models_lbl.pack(anchor="w", padx=12, pady=(10, 0))
        self.dl_label = ttk.Label(inner, text="Загрузка не идёт.", wraplength=640)
        self.dl_label.pack(anchor="w", padx=12)
        self.dl_bar = ttk.Progressbar(inner, maximum=100)
        self.dl_bar.pack(fill="x", padx=12, pady=2)
        self.stats_lbl = ttk.Label(inner, text="Перевод выключен.", wraplength=640)
        self.stats_lbl.pack(anchor="w", padx=12, pady=2)
        tkf = ttk.Frame(inner)
        tkf.pack(fill="x", padx=12, pady=2)
        ttk.Label(tkf, text="Токен GitHub (нужен только для закрытого репозитория):").pack(side="left")
        self.v_token = tk.StringVar(value=str(CFG.get("github_token", "")))
        te = ttk.Entry(tkf, textvariable=self.v_token, show="•", width=22)
        te.pack(side="left", padx=6)

        def save_token(_=None):
            CFG["github_token"] = self.v_token.get().strip()
            save_settings()
        te.bind("<FocusOut>", save_token)
        bar = ttk.Frame(inner)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Button(bar, text="Скачать модели", command=self.download).pack(side="left", padx=4)
        ttk.Button(bar, text="Отмена загрузки", command=lambda: self.dl_cancel.set()).pack(side="left", padx=4)
        self.btn_update = ttk.Button(bar, text="Проверить обновления", command=self.on_update_btn)
        self.btn_update.pack(side="left", padx=4)
        ttk.Button(bar, text="Проверить голос", command=self.test_voice).pack(side="left", padx=4)

    def refresh_models_state(self):
        w = os.path.exists(os.path.join(HUB.whisper_dir(CFG["whisper"]), "model.bin"))
        n = os.path.exists(os.path.join(NLLB_DIR, "model.bin"))
        self.models_lbl.config(text="Модели:  распознавание (%s) — %s     перевод (NLLB) — %s" % (
            CFG["whisper"], "✓ скачано" if w else "✗ нет", "✓ скачано" if n else "✗ нет"))

    def _on_progress(self, text, frac):
        self.dl_label.config(text=text)
        if frac is not None:
            self.dl_bar["value"] = frac * 100

    def download(self):
        if self.dl_thread is not None and self.dl_thread.is_alive():
            self.set_status("Загрузка уже идёт — следите за полосой ниже.")
            return
        self.dl_cancel.clear()
        self.dl_bar["value"] = 0

        def run():
            try:
                HUB.download(CFG["whisper"], lambda t, f: self.ui(self._on_progress, t, f), self.dl_cancel)
                self.ui(self.set_status, "Модели готовы ✓ Включите видео и нажмите F10.")
            except Cancelled:
                self.ui(self.set_status, "Загрузка отменена. «Скачать модели» продолжит с того же места.")
            except Exception as e:
                self.ui(self.set_status, "Ошибка загрузки: %s. Проверьте интернет и нажмите «Скачать модели» — загрузка продолжится." % e)
                log(traceback.format_exc())
            finally:
                self.ui(self.refresh_models_state)
        self.dl_thread = threading.Thread(target=run, daemon=True)
        self.dl_thread.start()
        self.set_status("Загрузка моделей началась — прогресс на полосе ниже.")

    # --- обновления: новая версия ставится ПОВЕРХ старой, модели и настройки остаются
    def on_update_btn(self):
        if self.pending_update:
            self.install_update()
        else:
            self.check_updates(False)

    def check_updates(self, silent=False):
        def run():
            try:
                build, asset = check_update()
                if build > APP_BUILD and asset:
                    self.ui(self.offer_update, build, asset, silent)
                elif not silent:
                    self.ui(self.set_status, "У вас последняя версия (сборка %d)." % APP_BUILD)
            except Exception as e:
                log("проверка обновлений: %s" % e)
                if not silent:
                    self.ui(self.set_status, "Не удалось проверить обновления: %s" % e)
        threading.Thread(target=run, daemon=True).start()

    def offer_update(self, build, asset, silent):
        self.pending_update = (build, asset)
        self.btn_update.config(text="Установить обновление (сборка %d)" % build)
        self.set_status("Вышла новая версия (сборка %d, у вас %d). Нажмите «Установить обновление»." % (build, APP_BUILD))
        if silent and self.root.state() != "withdrawn" and self.engine is None:
            if messagebox.askyesno("Babelo", "Вышла новая версия (сборка %d). Установить сейчас?\n"
                                              "Модели и настройки сохранятся, удалять ничего не нужно." % build):
                self.install_update()

    def install_update(self):
        if not getattr(sys, "frozen", False):
            self.set_status("Обновление работает только в собранной программе Babelo.exe.")
            return
        if self.engine is not None:
            self.set_status("Сначала выключите перевод (F10 два раза подряд), потом обновляйте.")
            return
        if not self.pending_update:
            self.check_updates(False)
            return
        if self.dl_thread is not None and self.dl_thread.is_alive():
            self.set_status("Загрузка уже идёт — следите за полосой ниже.")
            return
        build, asset = self.pending_update
        zip_path = os.path.join(tempfile.gettempdir(), "Babelo-update-%d.zip" % build)
        self.dl_cancel.clear()
        self.dl_bar["value"] = 0

        def run():
            try:
                download_asset(asset, zip_path, lambda t, f: self.ui(self._on_progress, t, f), self.dl_cancel)
                self.ui(self._finish_update, zip_path)
            except Cancelled:
                self.ui(self.set_status, "Загрузка обновления отменена.")
            except Exception as e:
                self.ui(self.set_status, "Не удалось скачать обновление: %s" % e)
                log(traceback.format_exc())
        self.dl_thread = threading.Thread(target=run, daemon=True)
        self.dl_thread.start()
        self.set_status("Скачиваю обновление — прогресс на полосе ниже.")

    def _finish_update(self, zip_path):
        try:
            launch_updater(zip_path)
        except Exception as e:
            self.set_status("Не удалось запустить установку обновления: %s" % e)
            return
        self.set_status("Устанавливаю обновление… программа закроется и откроется заново сама.")
        self.muter.restore()
        self.master.set(False)
        self.root.after(600, lambda: os._exit(0))

    def _tick(self):
        e = self.engine
        if e is None:
            txt = "Перевод выключен. F10 — включить."
        else:
            if not e.listening:
                sound = "подключаюсь к звуку компьютера…"
            else:
                sound = "звук идёт" if time.time() - e.last_loud < 2 else "тихо (включите видео или музыку)"
            s = e.stats
            player = "плеер найден" if self.media.available() else "плеер не найден"
            txt = "Перевод включён · слышу: %s · %s · фраз услышано %d, распознано %d, переведено %d, озвучено %d, ошибок %d" % (
                sound, player, s["heard"], s["recognised"], s["translated"], s["spoken"], s["errors"])
        self.stats_lbl.config(text=txt)
        self.root.after(1000, self._tick)

    def test_voice(self):
        def run():
            com_init()
            try:
                Tts().speak(TEST_PHRASE.get(CFG["target"], TEST_PHRASE["en"]), CFG["target"], threading.Event())
                self.ui(self.set_status, "Проверка голоса выполнена. Вы слышали голос?")
            except Exception as e:
                self.ui(self.set_status, "Ошибка озвучки: %s" % e)
                log(traceback.format_exc())
        threading.Thread(target=run, daemon=True).start()

    def run(self):
        self.root.mainloop()

def main():
    k32 = ctypes.windll.kernel32
    mutex = k32.CreateMutexW(None, False, "Babelo_single_instance")
    if k32.GetLastError() == 183:
        r = tk.Tk(); r.withdraw()
        messagebox.showinfo("Babelo", "Babelo уже запущен. Нажмите F10.")
        return
    try:
        App().run()
    except Exception:
        log(traceback.format_exc())
        raise

if __name__ == "__main__":
    main()

