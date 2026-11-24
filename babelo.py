# -*- coding: utf-8 -*-
"""
Babelo для Windows — живой перевод любого звука, который играет на компьютере.
F10: первое нажатие — перевод включён; ещё раз — выезжает меню; два раза подряд — полный перезапуск программы (Esc выключает перевод).
Распознавание (Whisper) -> перевод (NLLB) -> озвучка (голос Windows или Edge), оригинал заглушается, субтитры белым по чёрному.
"""
import os, sys, json, time, threading, queue, ctypes, traceback, re, tempfile, math, gc, subprocess, asyncio, datetime, bisect

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
from tkinter import ttk, messagebox, filedialog, simpledialog

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
EXT_DIR = os.path.join(DATA, "extension")
NEURAL_DIR = os.path.join(DATA, "voices")
# Нейросетевые офлайн-голоса (Piper). Проверены: русские. Остальные — по каталогу Piper.
NEURAL_VOICES = {
    "ru": [("Ирина — женский (нейросеть, офлайн)", "ru_RU-irina-medium"), ("Денис — мужской (нейросеть, офлайн)", "ru_RU-denis-medium"),
           ("Дмитрий — мужской (нейросеть, офлайн)", "ru_RU-dmitri-medium"), ("Руслан — мужской (нейросеть, офлайн)", "ru_RU-ruslan-medium")],
    "en": [("Amy — женский (нейросеть, офлайн)", "en_US-amy-medium"), ("Ryan — мужской (нейросеть, офлайн)", "en_US-ryan-medium")],
    "de": [("Thorsten — мужской (нейросеть, офлайн)", "de_DE-thorsten-medium"), ("Eva — женский (нейросеть, офлайн)", "de_DE-eva_k-x_low")],
    "fr": [("Siwis — женский (нейросеть, офлайн)", "fr_FR-siwis-medium"), ("Tom — мужской (нейросеть, офлайн)", "fr_FR-tom-medium")],
    "es": [("Davefx — мужской (нейросеть, офлайн)", "es_ES-davefx-medium")],
    "it": [("Paola — женский (нейросеть, офлайн)", "it_IT-paola-medium"), ("Riccardo — мужской (нейросеть, офлайн)", "it_IT-riccardo-x_low")],
    "pl": [("Gosia — женский (нейросеть, офлайн)", "pl_PL-gosia-medium"), ("Darkman — мужской (нейросеть, офлайн)", "pl_PL-darkman-medium")],
    "uk": [("Украинский (нейросеть, офлайн)", "uk_UA-ukrainian_tts-medium")],
    "tr": [("Fettah — мужской (нейросеть, офлайн)", "tr_TR-fettah-medium")],
    "pt": [("Faber — мужской (нейросеть, офлайн)", "pt_BR-faber-medium")],
    "zh": [("Huayan — женский (нейросеть, офлайн)", "zh_CN-huayan-medium")],
}
ENGINES = [("sapi", "Голос Windows (офлайн)"), ("neural", "Нейросеть (офлайн, заметно качественнее)"),
           ("edge", "Edge (онлайн, лучшее качество)")]
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
            "esc_stops": True, "orig_vol": 15, "text_src": "auto", "ext_prompted": False,
            "br_home": "https://www.google.com", "br_auto": True, "br_remember": True,
            "br_mode": "full", "br_which": "auto", "br_search": "google", "br_ask_dir": True, "br_dl_dir": "",
            "br_adblock": True}

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

UPDATER_PS1 = r"""
param([int]$AppPid, [string]$Zip, [string]$App, [string]$Data)
$ErrorActionPreference = "Stop"
$log = Join-Path $Data "update.log"
function L($m) {
    $t = (Get-Date).ToString("HH:mm:ss") + " " + $m
    Write-Host $t
    try { Add-Content -LiteralPath $log -Value $t -Encoding UTF8 } catch {}
}
function KillBabelo {
    Get-Process -Name Babelo -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID } | Stop-Process -Force -ErrorAction SilentlyContinue
}
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$host.UI.RawUI.WindowTitle = "Обновление Babelo — не закрывайте это окно"
Write-Host ""
Write-Host "  Обновляю Babelo. Это займёт 1-2 минуты, окно закроется само." -ForegroundColor Cyan
Write-Host ""
try {
    L "Жду, пока программа закроется..."
    try { Wait-Process -Id $AppPid -Timeout 25 } catch {}
    KillBabelo
    Start-Sleep -Seconds 2
    $tmp = Join-Path $env:TEMP "babelo_update_dir"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    L "Распаковываю новую версию..."
    & tar -xf $Zip -C $tmp
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $tmp "Babelo\Babelo.exe"))) {
        L "tar не справился, распаковываю другим способом (дольше)..."
        Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force
    }
    $src = Join-Path $tmp "Babelo"
    if (-not (Test-Path (Join-Path $src "Babelo.exe"))) { throw "в скачанном архиве нет Babelo.exe" }
    L "Заменяю файлы программы (настройки и модели не трогаю)..."
    $ok = $false
    for ($i = 1; $i -le 6; $i++) {
        & robocopy $src $App /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -lt 8) { $ok = $true; break }
        L ("попытка " + $i + ": файлы заняты (код " + $LASTEXITCODE + "), повторяю...")
        KillBabelo
        Start-Sleep -Seconds 3
    }
    if (-not $ok) { throw "не удалось заменить файлы: они заняты другой программой (закройте Babelo в диспетчере задач и повторите обновление)" }
    $n = (Get-Item (Join-Path $src "Babelo.exe")).Length
    $o = (Get-Item (Join-Path $App "Babelo.exe")).Length
    if ($n -ne $o) { throw "Babelo.exe не заменился (размер не совпал)" }
    L "Готово. Запускаю обновлённую программу..."
    Start-Process -FilePath (Join-Path $App "Babelo.exe") -WorkingDirectory $App
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Remove-Item -Force $Zip -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    exit 0
} catch {
    L ("ОШИБКА ОБНОВЛЕНИЯ: " + $_)
    Write-Host ""
    Write-Host "  Обновление не удалось. Подробности: $log" -ForegroundColor Red
    Write-Host "  Запускаю прежнюю версию. Нажмите Enter, чтобы закрыть это окно."
    try { Start-Process -FilePath (Join-Path $App "Babelo.exe") -WorkingDirectory $App } catch {}
    try { [void][Console]::ReadLine() } catch { Start-Sleep -Seconds 30 }
    exit 1
}
"""

def launch_updater(zip_path):
    """Отдельное видимое окно PowerShell: ждёт выхода программы, убивает зависшие процессы Babelo, распаковывает новую версию
    ПОВЕРХ старой, проверяет замену и запускает её. Модели и настройки не трогает. Журнал: %LOCALAPPDATA%\\Babelo\\update.log"""
    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    os.makedirs(DATA, exist_ok=True)
    ps1 = os.path.join(DATA, "update.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(UPDATER_PS1)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-AppPid", str(os.getpid()),
                      "-Zip", zip_path, "-App", app_dir, "-Data", DATA],
                     creationflags=0x00000010, close_fds=True)                  # CREATE_NEW_CONSOLE: окно видно

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

    def load_nllb(self, threads):
        """Только переводчик (для расширения браузера): без модели распознавания."""
        with self.lock:
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

# ---------------------------------------------------------------- нейросетевые голоса
def neural_list(lang):
    return NEURAL_VOICES.get(lang, [])

def neural_id(lang):
    lst = neural_list(lang)
    if not lst:
        return None
    for label, mid in lst:
        if label == CFG.get("voice"):
            return mid
    return lst[0][1]

def neural_root():
    """Папка голосов. Нейроголос не читает пути с русскими буквами (имя пользователя Windows), поэтому тогда — C:\\Users\\Public."""
    try:
        NEURAL_DIR.encode("ascii")
        return NEURAL_DIR
    except UnicodeEncodeError:
        return os.path.join(os.environ.get("PUBLIC") or r"C:\Users\Public", "Babelo", "voices")

def neural_dir(mid):
    return os.path.join(neural_root(), mid)

def _voice_ok(d, mid):
    return (os.path.exists(os.path.join(d, mid + ".onnx")) and os.path.exists(os.path.join(d, "tokens.txt"))
            and os.path.isdir(os.path.join(d, "espeak-ng-data")))

def neural_ready(mid):
    d = neural_dir(mid)
    if not _voice_ok(d, mid):
        old = os.path.join(NEURAL_DIR, mid)
        if neural_root() != NEURAL_DIR and _voice_ok(old, mid):          # голос скачан раньше в папку с русскими буквами
            import shutil
            try:
                shutil.copytree(old, d, dirs_exist_ok=True)
                log("голос %s перенесён в %s" % (mid, d))
            except Exception as e:
                log("перенос голоса: %s" % e)
    return _voice_ok(d, mid)

def download_neural(mid, progress, cancel):
    """Скачивает голос (архив ~60 МБ) с GitHub: с докачкой и прогрессом."""
    import requests, tarfile, shutil
    if neural_ready(mid):
        return
    os.makedirs(neural_root(), exist_ok=True)
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-%s.tar.bz2" % mid
    part = os.path.join(neural_root(), mid + ".tar.bz2.part")
    title = "Голос %s" % mid
    attempts = 0
    while True:
        if cancel.is_set():
            raise Cancelled()
        have = os.path.getsize(part) if os.path.exists(part) else 0
        try:
            hdr = {"User-Agent": "Babelo/2"}
            if have:
                hdr["Range"] = "bytes=%d-" % have
            with requests.get(url, headers=hdr, stream=True, timeout=(20, 40), allow_redirects=True) as r:
                if r.status_code == 416:
                    break
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0
                total = have + int(r.headers.get("Content-Length") or 0)
                last = 0.0
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        if cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 0.4:
                            last = time.time()
                            progress("%s: %d%% (%d из %d МБ)" % (title, have * 100 // max(total, 1), have >> 20, total >> 20),
                                     have / float(max(total, 1)))
            break
        except Cancelled:
            raise
        except Exception as e:
            attempts += 1
            if attempts >= 6:
                raise
            log("обрыв связи при загрузке голоса: %s — продолжаю (попытка %d)" % (str(e)[:80], attempts))
            time.sleep(3)
    progress("%s: распаковываю…" % title, None)
    tmp = os.path.join(neural_root(), "_unpack_" + mid)
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(part, "r:bz2") as tf:
        base = os.path.realpath(tmp)
        for m in tf.getmembers():
            if not os.path.realpath(os.path.join(tmp, m.name)).startswith(base):
                raise RuntimeError("небезопасный архив голоса")
        tf.extractall(tmp)
    src = os.path.join(tmp, "vits-piper-" + mid)
    if not os.path.isdir(src):
        subs = os.listdir(tmp)
        src = os.path.join(tmp, subs[0]) if subs else src
    dst = neural_dir(mid)
    shutil.rmtree(dst, ignore_errors=True)
    os.replace(src, dst)
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.remove(part)
    except OSError:
        pass
    if not neural_ready(mid):
        raise RuntimeError("в архиве голоса нет нужных файлов")
    progress("%s: готово ✓" % title, 1.0)

def _short_path(p):
    """Короткий ASCII-путь Windows (8.3); если не получилось — как есть."""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 520)
        return buf.value if n else p
    except Exception:
        return p

def _neural_variant(d, mid, v):
    """Четыре способа передать голосу пути: библиотека фонем на Windows капризна к ним."""
    model, tokens, data = os.path.join(d, mid + ".onnx"), os.path.join(d, "tokens.txt"), os.path.join(d, "espeak-ng-data")
    if v == 1:
        return tuple(x.replace("\\", "/") for x in (model, tokens, data))
    if v == 2:
        return _short_path(model), _short_path(tokens), _short_path(data)
    if v == 3:
        os.chdir(d)                                              # относительные пути, как в примерах sherpa-onnx
        return mid + ".onnx", "tokens.txt", "espeak-ng-data"
    return model, tokens, data

def neural_worker_log_path():
    return os.path.join(DATA, "neural_worker.log")

def neural_worker_main(port):
    """Отдельный процесс озвучки: sherpa-onnx живёт здесь, чтобы не конфликтовать с остальными моделями программы."""
    import socket, wave
    import sherpa_onnx
    sock = socket.create_connection(("127.0.0.1", port))
    f = sock.makefile("rwb")
    cache, best = {}, {}
    ver = str(getattr(sherpa_onnx, "__version__", "0"))
    too_new = ver.split(".")[0].isdigit() and int(ver.split(".")[0]) >= 2      # в версии 2.x убран модуль фонем: Piper молчит
    for line in f:
        try:
            req = json.loads(line.decode("utf-8"))
            d, mid = req["dir"], req["id"]
            if too_new:
                raise RuntimeError("установлена библиотека озвучки sherpa-onnx %s: в версии 2.x убраны фонемы, нужен 1.x (пересоберите программу)" % ver)
            audio, notes = None, []
            for v in [best.get(d, 0)] + [x for x in range(4) if x != best.get(d, 0)]:
                try:
                    if (d, v) not in cache:
                        model, tokens, data = _neural_variant(d, mid, v)
                        cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
                            vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=model, lexicon="", data_dir=data, tokens=tokens),
                            num_threads=int(req.get("threads", 2)), debug=False, provider="cpu"))
                        cache[(d, v)] = sherpa_onnx.OfflineTts(cfg)
                    a = cache[(d, v)].generate(req["text"], sid=int(req.get("sid", 0)), speed=float(req.get("speed", 1.0)))
                    if len(a.samples) > 0 and int(a.sample_rate) > 0:
                        audio, best[d] = a, v
                        break
                    notes.append("способ %d: пустой звук" % v)
                except Exception as e:
                    notes.append("способ %d: %s" % (v, str(e)[:100]))
            if audio is None:
                names = ("phontab", "phondata", "phonindex", "intonations", "ru_dict")
                dd = os.path.join(d, "espeak-ng-data")
                have = {n: os.path.exists(os.path.join(dd, n)) for n in names}
                tail = ""
                try:
                    with open(neural_worker_log_path(), "rb") as lf:
                        lf.seek(0, 2)
                        lf.seek(max(0, lf.tell() - 700))
                        tail = lf.read().decode("utf-8", "replace")
                except Exception:
                    pass
                raise RuntimeError("голос не вернул звук. sherpa-onnx %s; %s; файлы данных: %s; в папке данных %d элементов; "
                                   "вывод библиотеки: %s" % (getattr(sherpa_onnx, "__version__", "?"), "; ".join(notes), have,
                                                             len(os.listdir(dd)) if os.path.isdir(dd) else -1, tail.strip()[-500:]))
            x = np.clip(np.asarray(audio.samples, dtype=np.float32) * float(req.get("gain", 1.0)), -1.0, 1.0)
            with wave.open(req["out"], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(audio.sample_rate))
                w.writeframes((x * 32767).astype("<i2").tobytes())
            reply = {"ok": True}
        except Exception as e:
            reply = {"ok": False, "error": str(e)[:1500]}
        f.write((json.dumps(reply) + "\n").encode("utf-8"))
        f.flush()

class NeuralWorker:
    """Клиент отдельного процесса озвучки. Запускается при первой фразе, закрывается вместе с программой."""
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.f = None
        self.sock = None

    def close(self):
        for o in (self.f, self.sock):
            try:
                o.close()
            except Exception:
                pass
        self.f = self.sock = None
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def _start(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--neural-worker", str(port)]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--neural-worker", str(port)]
        self.proc = subprocess.Popen(cmd, creationflags=0x08000000)
        srv.settimeout(90)
        self.sock, _ = srv.accept()
        srv.close()
        self.sock.settimeout(120)
        self.f = self.sock.makefile("rwb")

    def synth(self, d, mid, text, speed, gain, threads, out):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.proc is None or self.proc.poll() is not None or self.f is None:
                        self.close()
                        self._start()
                    req = {"dir": d, "id": mid, "text": text, "speed": speed, "gain": gain, "threads": threads, "out": out}
                    self.f.write((json.dumps(req) + "\n").encode("utf-8"))
                    self.f.flush()
                    line = self.f.readline()
                    if not line:
                        raise OSError("процесс озвучки закрылся")
                    r = json.loads(line.decode("utf-8"))
                    if not r.get("ok"):
                        raise RuntimeError(r.get("error") or "ошибка озвучки")
                    return
                except (OSError, ValueError) as e:
                    self.close()
                    if attempt == 2:
                        raise RuntimeError("нейроголос: %s" % e)

NEURAL = NeuralWorker()

# ---------------------------------------------------------------- озвучка
_mci = None

def mci(cmd):
    global _mci
    if _mci is None:
        _mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(256)
    err = _mci(cmd, buf, 255, 0)
    return err, buf.value

def mci_open(path, alias, volume):
    wav = path.lower().endswith(".wav")
    err, _ = mci('open "%s" type %s alias %s' % (path, "waveaudio" if wav else "mpegvideo", alias))
    if err:
        raise RuntimeError("не удалось открыть звук (MCI %d)" % err)
    if not wav:
        mci("setaudio %s volume to %d" % (alias, max(0, min(1000, int(volume) * 10))))

def play_mp3(path, volume, stop_evt):
    alias = "bab%d" % (time.time_ns() % 10 ** 9)
    mci_open(path, alias, volume)
    try:
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
    mci_open(path, alias, volume)
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

    def neural_file(self, text, lang, pct):
        mid = neural_id(lang)
        if not mid:
            raise RuntimeError("для этого языка нет нейросетевого голоса")
        if not neural_ready(mid):
            raise RuntimeError("голос %s ещё не скачан" % mid)
        path = os.path.join(tempfile.gettempdir(), "babelo_%d.wav" % time.time_ns())
        gain = max(0.0, min(1.5, int(CFG["volume"]) / 100.0))
        NEURAL.synth(neural_dir(mid), mid, text, max(0.5, min(2.5, int(pct) / 100.0)), gain, max(1, int(CFG["threads"]) // 2), path)
        return path

    def prefile(self, text, lang, pct):
        """Звуковой файл заранее (Edge или нейроголос); None — говорит голос Windows."""
        eng = CFG["engine"]
        try:
            if eng == "edge":
                return self.edge_file(text, lang, pct)
            if eng == "neural":
                return self.neural_file(text, lang, pct)
        except Exception as e:
            log("озвучка (%s): %s" % (eng, e))
        return None

    def speak(self, text, lang, stop_evt, pct=None):
        """Говорит и ждёт конца."""
        pct = int(pct or CFG["speed"])
        if CFG["engine"] == "neural":
            try:
                path = self.neural_file(text, lang, pct)
                try:
                    play_mp3(path, int(CFG["volume"]), stop_evt)
                finally:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                return
            except Exception as e:
                log("Нейроголос недоступен (%s), говорю голосом Windows" % e)
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

class Bridge:
    """Состояние видео и команды (пауза, пуск, перемотка) через расширение Babelo в браузере.
    Каждая вкладка присылает своё состояние отдельно; программа «прикрепляется» к той вкладке и тому видео, которое переводит,
    команды уходят только туда, а появление другого играющего видео (новая вкладка, следующее видео) замечается."""
    def __init__(self):
        self.lock = threading.Lock()
        self.tabs = {}              # id вкладки -> {"state": ..., "t": время}
        self.cmds = []
        self.done = set()
        self.next_id = 1
        self.results = {}
        self.pinned = None
        self.pinned_vkey = None
        self.session = int(time.time() * 1000) % 1000000007

    def update(self, st):
        tid = str(st.get("tab") or "legacy")
        ack = int(st.get("ack") or 0)
        with self.lock:
            now = time.time()
            self.tabs[tid] = {"state": st, "t": now}
            for k in [k for k, v in self.tabs.items() if now - v["t"] > 120]:
                del self.tabs[k]
            for c in self.cmds:
                if c["tab"] in (None, tid) and c["id"] <= ack:
                    self.done.add(c["id"])
            self.cmds = [c for c in self.cmds if c["id"] not in self.done]
            if len(self.done) > 1000:
                self.done = set(sorted(self.done)[-300:])
            for c in self.cmds:
                if c["tab"] in (None, tid) and c["id"] > ack:
                    return c
            return None

    def _best(self, now):
        fresh = [(k, v) for k, v in self.tabs.items() if now - v["t"] < 1.5]
        if not fresh:
            return None
        playing = [x for x in fresh if x[1]["state"].get("playing")]
        return max(playing or fresh, key=lambda x: x[1]["t"])[0]

    def active(self):
        with self.lock:
            now = time.time()
            k = self.pinned if self.pinned is not None else self._best(now)
            v = self.tabs.get(k) if k is not None else None
            if v is None or now - v["t"] >= 1.5:
                return None
            return v["state"], v["t"], k

    def fresh(self):
        return self.active() is not None

    def ad_active(self):
        """В переводимом видео сейчас идёт реклама."""
        a = self.active()
        return bool(a and a[0].get("ad"))

    def snapshot(self):
        a = self.active()
        return (dict(a[0]), a[1]) if a else ({}, 0.0)

    def pin_best(self):
        """Прикрепляемся к вкладке с играющим видео (или самой свежей): её и переводим."""
        with self.lock:
            k = self._best(time.time())
            self.pinned = k
            self.pinned_vkey = self.tabs[k]["state"].get("vkey") if k is not None else None
            return k

    def unpin(self):
        with self.lock:
            self.pinned = None
            self.pinned_vkey = None
            self.cmds = []

    def switch_candidate(self):
        """("video", вкладка) — в той же вкладке открыто другое видео; ("tab", вкладка) — заиграло видео в другой вкладке."""
        with self.lock:
            if self.pinned is None:
                return None
            now = time.time()
            p = self.tabs.get(self.pinned)
            pinned_fresh = p is not None and now - p["t"] < 1.5
            if pinned_fresh and p["state"].get("vkey") != self.pinned_vkey:
                return ("video", self.pinned)
            if pinned_fresh and p["state"].get("playing"):
                return None
            for k, v in self.tabs.items():
                if k != self.pinned and now - v["t"] < 1.5 and v["state"].get("playing"):
                    return ("tab", k)
            return None

    def playing_tab(self, prefix):
        """(вкладка, видео) играющей вкладки с таким началом номера — например "bi-" у встроенного браузера."""
        with self.lock:
            now = time.time()
            for k, v in self.tabs.items():
                if k.startswith(prefix) and now - v["t"] < 1.5 and v["state"].get("playing"):
                    return (k, v["state"].get("vkey"))
            return None

    def send(self, typ, t=None, extra=None):
        with self.lock:
            cid = self.next_id
            self.next_id += 1
            cmd = {"id": cid, "type": typ, "t": t, "tab": self.pinned if self.pinned is not None else self._best(time.time())}
            if extra:
                cmd.update(extra)
            self.cmds.append(cmd)
            return cid

    def put_result(self, cid, data):
        with self.lock:
            self.results[cid] = data

    def request(self, typ, payload=None, timeout=15.0):
        """Команда расширению с ответом (например, получить субтитры видео)."""
        cid = self.send(typ, None, payload or {})
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                if cid in self.results:
                    return self.results.pop(cid)
            time.sleep(0.05)
        return None

    def wait_ack(self, cid, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                if cid in self.done:
                    return True
            time.sleep(0.03)
        return False

BRIDGE = Bridge()

class MediaCtl:
    """Управление плеером Windows (YouTube в Chrome/Edge, Spotify и др.): позиция, пауза, перемотка."""
    def __init__(self):
        self.err = ""
        self.src = "win"
        self.seek_broken = False
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
        if BRIDGE.fresh():                                   # расширение браузера: точная позиция и перемотка
            snap, t = BRIDGE.snapshot()
            self.state = {"pos": int(float(snap.get("pos", 0)) * 1000), "playing": bool(snap.get("playing")),
                          "seek": bool(snap.get("seek")), "rate": float(snap.get("rate") or 1.0), "ad": bool(snap.get("ad"))}
            self.state_t = t
            if self.src != "ext":
                self.src = "ext"
                self.seek_broken = False
            self.err = ""
            return
        self.src = "win"
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
        if self.src == "ext" and BRIDGE.fresh():
            typ = {"try_play_async": "play", "try_pause_async": "pause",
                   "try_change_playback_position_async": "seek"}[name]
            cid = BRIDGE.send(typ, (args[0] / 1e7) if args else None)      # 100-нс тики -> секунды
            ok = BRIDGE.wait_ack(cid, 1.2)
            self.refresh()
            return ok

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

    def wait_near(self, ms, timeout=3.0, tol=None):
        """Ждёт, пока позиция плеера окажется рядом с ms (после перемотки). False — перемотка не сработала."""
        tol = tol if tol is not None else (700 if self.src == "ext" else 2500)
        t0 = time.time()
        while time.time() - t0 < timeout:
            p = self.position_ms()
            if p is not None and abs(p - ms) < tol:
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

SENT_END_RE = re.compile(r'[.!?…。！？]["\')»”]*\s*$')

class Phrase:
    def __init__(self, audio, t, prep, start, end):
        self.audio, self.t, self.prep, self.start, self.end = audio, t, prep, start, end

class AnyEvent:
    """Событие «остановить»: срабатывает, если сработало любое из переданных."""
    def __init__(self, *evts):
        self.evts = evts

    def is_set(self):
        return any(e.is_set() for e in self.evts)

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
        self.hold = threading.Event()      # видео поставил на паузу пользователь: перевод ждёт
        self.halt = AnyEvent(self.stop_evt, self.hold)
        self.autoplay = True               # при включении перевода видео запускается само (один раз)

    def start(self):
        for fn in (self.capture, self.worker, self.speaker, self.controller, self.pause_watch):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.stop_evt.set()

    def wait(self, sec):
        t0 = time.time()
        while time.time() - t0 < sec and not self.halt.is_set():
            time.sleep(0.05)

    # --- пауза пользователя: перевод останавливается и ждёт, пока видео не включат снова
    def clear_queues(self):
        for q in (self.phrase_q, self.speak_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass

    def pause_watch(self):
        com_init()
        media = self.app.media
        was = None
        while not self.stop_evt.is_set():
            st = media.info()
            if st is not None:
                playing = st["playing"]
                if was is True and not playing and time.time() >= self.guard_until and not self.hold.is_set():
                    self.hold.set()                                   # пользователь поставил видео на паузу
                    self.clear_queues()
                    self.app.muter.restore()
                    self.app.master.set(False)
                    self.app.ui(self.app.subs.hide)
                    self.app.ui(self.app.banner.status, "Видео на паузе — перевод ждёт. Включите воспроизведение, и перевод продолжится.")
                    log("пользователь поставил видео на паузу — перевод ждёт")
                elif was is False and playing and self.hold.is_set():
                    self.hold.clear()                                 # пользователь продолжил просмотр
                    self.app.ui(self.app.banner.status, None)
                    log("видео снова идёт — перевод продолжается")
                was = playing
            time.sleep(0.12)

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
                if self.gate or self.hold.is_set() or BRIDGE.ad_active():      # свой голос, пауза или реклама
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
                max_sec = max(int(CFG["phrase_sec"]), 12) if (prep or CFG["mode"] == "interrupted") else int(CFG["phrase_sec"])
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
                if self.hold.is_set():
                    pass
                elif ph.prep:
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
            if time.time() - t > 20 or self.hold.is_set():
                continue
            try:
                self.gate = True
                if CFG["mute_original"]:
                    self.app.muter.mute()
                self.app.ui(self.app.subs.show_sub, sub, "tr")
                tts.speak(text, CFG["target"], self.halt)
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
    def notice(self, text, sec=9):
        self.app.ui(self.app.banner.status, text)
        t = threading.Timer(sec, lambda: self.app.ui(self.app.banner.status, None))
        t.daemon = True
        t.start()

    def seek_to(self, ms, tol=None):
        """Перемотка с проверкой: возвращает False, если плеер остался на прежнем месте."""
        media = self.app.media
        self.guard(3.0)
        media.seek(ms)
        ok = media.wait_near(ms, 3.0, tol)
        self.guard(1.5)
        if not ok:
            media.seek_broken = True
            log("перемотка не сработала (источник: %s)" % media.src)
            self.notice("Перемотка видео не сработала. Установите расширение Babelo в браузер или откройте видео в проигрывателе Babelo.")
        return ok

    def fragment(self, start, end, say, tts, mute_others):
        media = self.app.media
        if not self.seek_to(start, max(500, min(2500, int((end - start) * 0.45)))):
            return False
        if mute_others and CFG["mute_original"]:
            self.app.muter.mute()
        if say:
            try:
                tts.say_now(say[0], CFG["target"], int(CFG["speed"]), say[1])
            except Exception as e:
                log("озвучка отрывка: %s" % e)
        self.guard(1.5)
        media.play()
        t0 = time.time()
        limit = (end - start) / 1000.0 + 4
        while not self.halt.is_set() and time.time() - t0 < limit:
            pos = media.position_ms()
            if pos is not None and pos >= end:
                break
            time.sleep(0.03)
        self.guard(2.0)
        media.pause()
        if say:
            tts.wait_done(self.halt, 40)
        if mute_others:
            self.app.muter.restore()
        return True

    def fragment_full(self, start, end, say, tts, mute_others):
        """Отрывок с переводом; если пользователь поставил паузу — ждём «Пуск» и проходим отрывок заново целиком."""
        while True:
            ok = self.fragment(start, end, say, tts, mute_others)
            if not self.hold.is_set():
                return ok
            tts.stop_now()
            self.notice("Пауза. После «Пуск» отрывок с переводом прозвучит заново, целиком.", 30)
            while self.hold.is_set() and not self.stop_evt.is_set():
                time.sleep(0.1)
            if self.stop_evt.is_set():
                return False

    def process_interrupted(self, ph, tts):
        media = self.app.media
        target = CFG["target"]
        self.gate = True
        mp3 = None
        try:
            self.guard(3.0)
            media.pause()
            res = self.understand(ph.audio)
            if not res:
                return
            text0, frm0 = res
            carry = getattr(self, "carry", None)
            if carry and time.time() - carry["t"] > 8:
                carry = None
            full = (carry["text"] + " " + text0) if carry else text0
            if not SENT_END_RE.search(full.strip()) and len(full) < 220:   # предложение не закончено: ждём продолжение
                self.carry = {"text": full, "start": carry["start"] if carry else ph.start, "t": time.time()}
                return
            if carry:
                ph.start = carry["start"]                                    # отрывок для повтора — с начала предложения
            self.carry = None
            made = self.make_sub(full, frm0)
            if not made:
                return
            sub, speak = made
            n = max(1, int(CFG["repeats"]))
            gap = float(CFG["pause_s"])
            seek_ok = (media.can_seek() and not media.seek_broken and ph.start is not None and ph.end is not None
                       and ph.end > ph.start)
            if not seek_ok:
                self.notice("Нельзя перемотать видео назад (плеер: %s). Перевод звучит на паузе. Установите расширение Babelo "
                            "в браузер или откройте файл в проигрывателе Babelo." % ("расширение" if media.src == "ext" else "Windows"))
            mp3 = tts.prefile(speak, target, int(CFG["speed"]))
            for i in range(n):
                if self.halt.is_set():
                    break
                if seek_ok and i > 0:                            # оригинал ещё раз
                    self.app.ui(self.app.subs.show_sub, sub, "orig")
                    if not self.fragment_full(ph.start, ph.end, None, tts, False):
                        seek_ok = False
                self.wait(gap)                                   # пауза между фразой и переводом
                self.app.ui(self.app.subs.show_sub, sub, "tr")
                spoke = False
                if seek_ok:                                      # тот же отрывок, но с голосом перевода
                    spoke = self.fragment_full(ph.start, ph.end, (speak, mp3), tts, True)
                    if not spoke:
                        seek_ok = False
                if not spoke:                                    # перематывать нельзя: говорим на паузе
                    if CFG["mute_original"]:
                        self.app.muter.mute()
                    tts.speak(speak, target, self.halt)
                    self.app.muter.restore()
                self.stats["spoken"] += 1
                self.wait(gap)
            self.app.ui(self.app.subs.hide)
            if seek_ok and not self.hold.is_set():
                self.seek_to(ph.end, 1500)
        finally:
            self.app.muter.restore()
            if not self.hold.is_set():                           # на паузе пользователя видео не трогаем
                self.guard(2.0)
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
        mp3 = tts.prefile(speak, CFG["target"], pct)
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
            if self.hold.is_set():
                return "пауза"
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
            app.ui(app.subs.flash, "Непрерывный режим: нужен плеер с перемоткой. Установите расширение Babelo в Chrome или Edge "
                                   "(YouTube и другие сайты) либо откройте файл в проигрывателе Babelo. Включён обычный режим.", 8000)
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
                    if str(r) == "пауза":                            # ждём, пока пользователь сам включит видео
                        while not self.stop_evt.is_set() and CFG["mode"] == "continuous" and self.hold.is_set():
                            time.sleep(0.15)
                        self.wait(0.3)
                    else:
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
                if not self.seek_to(start_at, 1500):
                    raise RuntimeError("перемотка видео не сработала")
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
        if first and self.autoplay:
            self.autoplay = False
            self.guard(2.0)
            media.play()                                           # при включении перевода видео запускается само
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
            app.ui(app.banner.status, "Непрерывный перевод: подгрузка %d %% %s" % (
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
            app.ui(app.banner.status, "Непрерывный перевод: перевожу первые фразы…")
            time.sleep(0.2)
        app.master.set(False)
        self.phase = 2
        self.gate = True
        app.muter.mute()                                           # оригинал выключен, остаётся только голос перевода
        app.ui(app.subs.hide)
        if not self.seek_to(p0, 1500):
            raise RuntimeError("перемотка видео не сработала: вернуться к началу отрезка не удалось")
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
        tts.wait_done(self.halt, 15)                               # договорить последнюю фразу, пока видео идёт дальше
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
TEXT_SRC = [("auto", "Авто: субтитры видео, а если их нет — распознавание звука"),
            ("captions", "Только субтитры видео (нужно расширение в браузере)"), ("sound", "Только распознавание звука")]
BR_MODES = [("full", "Полный браузер: вкладки, адресная строка, закладки, загрузки (как Chrome)"),
            ("app", "Компактное окно: только страница")]
BR_WHICH = [("auto", "Авто: Google Chrome, а если его нет — Microsoft Edge"), ("chrome", "Google Chrome"), ("edge", "Microsoft Edge")]
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

    lab("Откуда брать текст для перевода")
    ct = ttk.Combobox(parent, state="readonly", values=[t for _, t in TEXT_SRC])
    ct.pack(fill="x", padx=12)

    def pick_text(_=None):
        CFG["text_src"] = key_of(TEXT_SRC, ct.get())
        save_settings()
    ct.bind("<<ComboboxSelected>>", pick_text)
    refresh.append(lambda: ct.set(label_of(TEXT_SRC, CFG["text_src"])))

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
    refresh.append(add_slider(parent, dark, "Громкость оригинала в обычном режиме (проигрыватель), %", 0, 100, "orig_vol"))
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

HINT = "Перевод включён · F10 — меню · Esc — выключить · дважды F10 — перезапуск программы"

class Banner:
    """Подсказка самого вверху экрана (всё время, пока идёт перевод) и под ней строка состояния."""
    def __init__(self, root):
        self.root = root
        self.win = None
        self.hint = None
        self.stat = None
        self.active = False
        self.stat_text = None

    def _ensure(self):
        if self.win is not None:
            return
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.97)
        w.configure(bg="black")
        self.hint = tk.Label(w, text="", fg="white", bg="black", font=("Segoe UI", 14, "bold"), padx=18, pady=6)
        self.hint.pack()
        self.stat = tk.Label(w, text="", fg="#ffd400", bg="black", font=("Segoe UI", 12, "bold"), padx=18, pady=2)
        w.withdraw()
        w.update_idletasks()
        exstyle(w, True)
        self.win = w

    def _place(self):
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw = self.win.winfo_screenwidth()
        self.win.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), 4))
        self.win.deiconify()
        self.win.lift()

    def on(self, text):
        """Включить подсказку: она видна, пока идёт перевод."""
        self._ensure()
        self.active = True
        self.hint.config(text=text)
        self.status(self.stat_text)

    def status(self, text):
        self.stat_text = text
        if not self.active or self.win is None:
            return
        if text:
            self.stat.config(text=text)
            self.stat.pack()
        else:
            self.stat.pack_forget()
        self._place()

    def hide(self):
        self.active = False
        self.stat_text = None
        if self.win is not None:
            self.stat.pack_forget()
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

class SettingsView:
    """Виджеты одного экземпляра панели настроек (главное окно или меню по F10)."""
    dead = False

def restyle(w):
    """Тёмная тема для ttk-виджетов внутри меню."""
    for c in w.winfo_children():
        try:
            k = c.winfo_class()
            if k == "TLabel":
                c.configure(style="D.TLabel")
            elif k == "TFrame":
                c.configure(style="D.TFrame")
            elif k == "TCheckbutton":
                c.configure(style="D.TCheckbutton")
        except Exception:
            pass
        restyle(c)

class Menu:
    """Короткое меню, выезжающее справа. Заголовок и нижние кнопки всегда видны, середина прокручивается."""
    W = 560

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
        tk.Button(foot, text="Полное закрытие программы", command=self.app.full_close, bg="#333", fg="white",
                  activebackground="#444", activeforeground="white", relief="flat", pady=5).pack(fill="x", pady=2)
        outer, inner, canvas = make_scroll(w, bg=BG)
        outer.pack(side="top", fill="both", expand=True)
        self.view = self.app.build_settings(inner, True)             # те же настройки, что в главном окне
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        h = min(inner.winfo_reqheight() + 120, sh - 60)
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
        v = getattr(self, "view", None)
        if v is not None:
            v.dead = True
            try:
                self.app.views.remove(v)
            except ValueError:
                pass
            self.view = None
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

# ---------------------------------------------------------------- расширение браузера: файлы и версия
def ext_version(path=None):
    try:
        with open(os.path.join(path or EXT_DIR, "manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("version", "")
    except Exception:
        return ""

def install_extension_files():
    """Кладёт актуальные файлы расширения в %LOCALAPPDATA%\\Babelo\\extension (при установке и обновлении программы)."""
    import shutil
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for src in (os.path.join(getattr(sys, "_MEIPASS", ""), "extension"), os.path.join(exe_dir, "extension"),
                os.path.join(exe_dir, "_internal", "extension"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension")):
        if os.path.exists(os.path.join(src, "manifest.json")):
            if ext_version(src) != ext_version():
                shutil.copytree(src, EXT_DIR, dirs_exist_ok=True)
                log("файлы расширения браузера обновлены: %s (версия %s)" % (EXT_DIR, ext_version()))
            return True
    return False

# ---------------------------------------------------------------- сервер для расширения браузера
class LocalServer:
    """Расширение Chrome/Edge просит у Babelo перевод (NLLB) с выравниванием слов. Только 127.0.0.1."""
    PORT = 47831

    def __init__(self):
        self.httpd = None

    def translate(self, body):
        src, tgt = body.get("src"), body.get("tgt")
        if src not in LANGS or tgt not in LANGS:
            raise ValueError("язык не поддерживается")
        if not os.path.exists(os.path.join(NLLB_DIR, "model.bin")):
            raise RuntimeError("модель перевода ещё не скачана — нажмите «Скачать модели» в Babelo")
        HUB.load_nllb(int(CFG["threads"]))
        out = []
        for t in body.get("texts", []):
            tr, pairs = HUB.translate_aligned(str(t)[:1500], src, tgt)
            out.append({"tr": tr, "pairs": pairs})
        return {"results": out}

    def start(self):
        import http.server
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/ping":
                    self._send(200, {"ok": True, "build": APP_BUILD, "ext": ext_version(),
                                     "models": os.path.exists(os.path.join(NLLB_DIR, "model.bin"))})
                else:
                    self._send(404, {})

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if self.path == "/translate":
                        self._send(200, outer.translate(body))
                    elif self.path == "/media/state":
                        cmd = BRIDGE.update(body)
                        self._send(200, {"cmd": cmd, "session": BRIDGE.session, "ext": ext_version(), "pinned": BRIDGE.pinned})
                    elif self.path == "/media/result":
                        BRIDGE.put_result(int(body.get("id", 0)), body.get("data"))
                        self._send(200, {})
                    elif self.path == "/reverse":
                        HUB.load_nllb(int(CFG["threads"]))
                        self._send(200, {"word": HUB.reverse_word(str(body.get("word", ""))[:80], body["src"], body["tgt"])})
                    else:
                        self._send(404, {})
                except Exception as e:
                    log("сервер расширения: %s" % e)
                    self._send(500, {"error": str(e)})
        try:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.PORT), Handler)
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            log("сервер для расширения браузера: http://127.0.0.1:%d" % self.PORT)
        except Exception as e:
            log("сервер для расширения не запущен: %s" % e)

# ---------------------------------------------------------------- проигрыватель файлов (libmpv)
class Mpv:
    """Минимальная обёртка над libmpv-2.dll через ctypes (без python-mpv)."""
    def __init__(self, wid):
        dll = None
        dirs = [os.path.dirname(os.path.abspath(sys.executable)), getattr(sys, "_MEIPASS", ""),
                os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "_internal"),
                os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
        for d in dirs:
            if d and os.path.exists(os.path.join(d, "libmpv-2.dll")):
                dll = os.path.join(d, "libmpv-2.dll")
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                break
        if dll is None:
            raise RuntimeError("не найден libmpv-2.dll: проигрыватель недоступен в этой сборке")
        L = ctypes.CDLL(dll)
        c_void_p, c_char_p = ctypes.c_void_p, ctypes.c_char_p
        L.mpv_create.restype = c_void_p
        L.mpv_initialize.argtypes = [c_void_p]
        L.mpv_set_option_string.argtypes = [c_void_p, c_char_p, c_char_p]
        L.mpv_set_property_string.argtypes = [c_void_p, c_char_p, c_char_p]
        L.mpv_get_property_string.argtypes = [c_void_p, c_char_p]
        L.mpv_get_property_string.restype = c_void_p
        L.mpv_free.argtypes = [c_void_p]
        L.mpv_command.argtypes = [c_void_p, ctypes.POINTER(c_char_p)]
        L.mpv_terminate_destroy.argtypes = [c_void_p]
        self.L = L
        self.h = L.mpv_create()
        if not self.h:
            raise RuntimeError("libmpv не создан")
        for k, v in (("wid", str(int(wid))), ("keep-open", "yes"), ("osc", "no"), ("input-default-bindings", "no"),
                     ("input-vo-keyboard", "no"), ("sub-auto", "no"), ("hwdec", "auto-safe"), ("idle", "yes")):
            L.mpv_set_option_string(self.h, k.encode(), v.encode())
        if L.mpv_initialize(self.h) < 0:
            raise RuntimeError("libmpv не запустился")

    def get(self, name):
        p = self.L.mpv_get_property_string(self.h, name.encode())
        if not p:
            return None
        s = ctypes.cast(p, ctypes.c_char_p).value.decode("utf-8", "ignore")
        self.L.mpv_free(p)
        return s

    def getf(self, name):
        try:
            return float(self.get(name))
        except (TypeError, ValueError):
            return None

    def set(self, name, value):
        self.L.mpv_set_property_string(self.h, name.encode(), str(value).encode("utf-8"))

    def command(self, *args):
        arr = (ctypes.c_char_p * (len(args) + 1))(*[str(a).encode("utf-8") for a in args], None)
        return self.L.mpv_command(self.h, arr)

    def destroy(self):
        try:
            if self.h:
                self.L.mpv_terminate_destroy(self.h)
        finally:
            self.h = None

class MpvBackend:
    """Управление видео в проигрывателе Babelo (libmpv)."""
    def __init__(self, mpv):
        self.m = mpv

    def pos(self):
        return self.m.getf("time-pos")

    def paused(self):
        return self.m.get("pause") == "yes"

    def set_pause(self, on):
        self.m.set("pause", "yes" if on else "no")

    def seek(self, sec):
        self.m.command("seek", sec, "absolute+exact")
        time.sleep(0.15)
        return True

    def volume(self, v):
        self.m.set("volume", int(v))

    def rate(self):
        return self.m.getf("speed") or 1.0

    def in_ad(self):
        return False

    def restore(self):
        pass

class BridgeBackend:
    """Управление видео на странице в браузере через расширение Babelo."""
    def __init__(self, media):
        self.media = media

    def pos(self):
        p = self.media.position_ms()
        return None if p is None else p / 1000.0

    def paused(self):
        st = self.media.info()
        return st is None or not st["playing"]

    def set_pause(self, on):
        if on:
            self.media.pause()
        else:
            self.media.play()

    def seek(self, sec):
        ms = int(sec * 1000)
        self.media.seek(ms)
        return self.media.wait_near(ms, 3.0, 900)

    def volume(self, v):
        BRIDGE.send("volume", None, {"v": None if v is None else v / 100.0})

    def rate(self):
        st = self.media.info()
        return (st or {}).get("rate") or 1.0

    def in_ad(self):
        return BRIDGE.ad_active()

    def restore(self):
        BRIDGE.send("volume", None, {"v": None})

class ItemPlayer:
    """Общая часть проигрывателя файла и перевода по субтитрам видео: переведённые фразы готовятся ВПЕРЁД,
    видео идёт без повторов. Режимы: обычный, непрерывный, прерывистый."""
    def init_player(self, app, be):
        self.app = app
        self.be = be
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.items = {}
        self.ver = 0
        self.cov = []
        self.done = False
        self.duration = 0.0
        self.pipe_id = 0
        self.last_restart = 0.0
        self.buffering = True
        self.elastic = False
        self.guard_until = 0.0
        self.closed = False
        self.error = None
        self.status = "Подготовка перевода…"
        self.src_lang = "…"

    def guard(self, sec=1.5):
        self.guard_until = time.time() + sec

    # --- покрытие: какие участки уже переведены
    def extend_cov(self, a, b):
        with self.lock:
            for iv in self.cov:
                if iv[0] - 0.5 <= a <= iv[1] + 0.5:
                    iv[0] = min(iv[0], a)
                    iv[1] = max(iv[1], b)
                    return
            self.cov.append([a, b])

    def in_cov(self, pos):
        with self.lock:
            return any(iv[0] - 0.6 <= pos <= iv[1] for iv in self.cov)

    def covered(self, pos, lead):
        with self.lock:
            return any(iv[0] - 0.6 <= pos and (iv[1] >= pos + lead or (self.duration and iv[1] >= self.duration - 0.5))
                       for iv in self.cov)

    def restart_pipeline(self, from_s):
        raise NotImplementedError

    def add_segment(self, s0, s1, text, frm, tts):
        target = CFG["target"]
        sents = HUB._sentences(text) or [text]
        lens = [max(1, len(s)) for s in sents]
        total = float(sum(lens))
        t = s0
        for s, ln in zip(sents, lens):
            a, b = t, t + (s1 - s0) * ln / total
            t = b
            try:
                tr, pairs = HUB.translate_aligned(s, frm, target)
            except Exception as e:
                log("перевод фразы: %s" % e)
                continue
            speak = clean_text(tr)[:500]
            if not any(c.isalpha() for c in speak):
                continue
            win = max(1.0, b - a + 0.8)
            pct = int(min(220, max(int(CFG["speed"]), len(speak) / (13.0 * win) * 100)))
            mp3 = tts.prefile(speak, target, pct)
            sub = {"orig": s, "tr": tr, "pairs": pairs, "frm": frm, "to": target}
            it = Item(int(a * 1000), int(b * 1000), sub, speak, mp3, pct)
            with self.lock:
                self.items[it.start] = it
                self.ver += 1

    def wait(self, sec):
        t0 = time.time()
        while time.time() - t0 < sec and not self.stop_evt.is_set():
            time.sleep(0.05)

    def play_until(self, end_s, timeout):
        """Проигрывает до end_s. Возвращает "ok", "paused" (пользователь поставил паузу) или "stop"."""
        t0 = time.time()
        while not self.stop_evt.is_set() and time.time() - t0 < timeout:
            p = self.be.pos()
            if p is not None and p >= end_s:
                return "ok"
            if time.time() - t0 > 0.8 and self.be.paused():
                return "paused"
            time.sleep(0.03)
        return "stop" if self.stop_evt.is_set() else "ok"

    def wait_resume(self):
        """Пользователь на паузе: ждём, пока он снова включит воспроизведение."""
        while not self.stop_evt.is_set():
            if not self.be.paused():
                return True
            time.sleep(0.1)
        return False

    def seek_failed(self):
        log("перемотка видео не сработала")
        self.app.ui(self.app.banner.status, "Перемотка видео не сработала — повтор отрывка пропущен")

    def replay(self, it, tts, speak):
        """Один проход отрывка (с голосом перевода или с оригиналом). Если пользователь ставит паузу, стоим на паузе,
        а после «Пуск» проходим отрывок заново ПОЛНОСТЬЮ — перевод не обрывается."""
        be = self.be
        st, en = it.start / 1000.0, it.end / 1000.0
        target = CFG["target"]
        while not self.stop_evt.is_set():
            self.guard(4)
            be.volume(0 if (speak and CFG["mute_original"]) else 100)
            if not be.seek(st):
                self.seek_failed()
                return False
            if speak:
                try:
                    tts.say_now(it.speak, target, it.pct, it.mp3)
                except Exception as e:
                    log("озвучка: %s" % e)
            be.set_pause(False)
            r = self.play_until(en, en - st + 4)
            if r == "paused":
                tts.stop_now()
                self.app.ui(self.app.banner.status, "Пауза. После «Пуск» отрывок с переводом прозвучит заново, целиком.")
                if not self.wait_resume():
                    return False
                continue
            if r == "stop":
                return False
            be.set_pause(True)
            if speak:
                tts.wait_done(self.stop_evt, 40)                       # перевод договаривается до конца
            be.volume(100)
            return True
        return False

    def run_interrupted(self, it, tts):
        be, app = self.be, self.app
        n = max(1, int(CFG["repeats"]))
        gap = float(CFG["pause_s"])
        self.guard(6)
        be.set_pause(True)
        for r in range(n):
            if self.stop_evt.is_set():
                break
            if r > 0:                                                   # оригинал ещё раз
                app.ui(app.subs.show_sub, it.sub, "orig")
                if not self.replay(it, tts, False):
                    break
            self.wait(gap)                                              # пауза между фразой и переводом
            app.ui(app.subs.show_sub, it.sub, "tr")
            if not self.replay(it, tts, True):
                break
            self.wait(gap)
        app.ui(app.subs.hide)
        app.ui(app.banner.status, None)
        if self.stop_evt.is_set():
            return
        self.guard(4)
        be.seek(it.end / 1000.0)
        be.set_pause(False)

    def play_loop(self):
        com_init()
        tts = Tts()
        be, app = self.be, self.app
        items, starts, ptr, last_ver = [], [], 0, -1
        last_pos, last_t, shown = None, time.time(), None
        elastic_t, last_status, vol_set, pause_item = 0.0, None, None, None
        while not self.stop_evt.is_set():
            time.sleep(0.04)
            try:
                pos = be.pos()
                if pos is None:
                    if self.error:
                        app.ui(app.banner.status, "Ошибка: %s" % self.error)
                        time.sleep(0.5)
                    continue
                if be.in_ad():                                               # реклама: не переводим, время не сравниваем
                    tts.stop_now()
                    if shown is not None:
                        app.ui(app.subs.hide)
                        shown = None
                    last_pos = None
                    if last_status != "реклама":
                        last_status = "реклама"
                        app.ui(app.banner.status, "Реклама — перевод ждёт основное видео")
                    continue
                now = time.time()
                paused = be.paused()
                mode = CFG["mode"]
                if last_ver != self.ver:
                    with self.lock:
                        items = sorted(self.items.values(), key=lambda x: x.start)
                    starts = [i.start for i in items]
                    last_ver = self.ver
                    ptr = bisect.bisect_left(starts, int(pos * 1000) - 250)
                if last_pos is not None and now > self.guard_until:       # перемотка пользователя
                    expected = last_pos if (paused or self.buffering) else last_pos + (now - last_t) * be.rate()
                    if abs(pos - expected) > 1.5:
                        tts.stop_now()
                        app.ui(app.subs.hide)
                        shown = None
                        ptr = bisect.bisect_left(starts, int(pos * 1000) - 250)
                        if not self.in_cov(pos) and now - self.last_restart > 2:
                            self.restart_pipeline(pos)
                last_pos, last_t = pos, now
                # --- запас вперёд: без готового перевода видео ждёт
                lead = max(3, min(int(CFG["block_sec"]), 60))
                if not self.done and not self.error:
                    if not self.covered(pos, 1.0):
                        if not self.buffering:
                            self.buffering = True
                            self.guard(2.0)
                            be.set_pause(True)
                            tts.stop_now()
                        if not self.in_cov(pos) and now - self.last_restart > 3:
                            self.restart_pipeline(pos)
                    elif self.buffering and self.covered(pos, lead):
                        self.buffering = False
                        self.guard(2.0)
                        be.set_pause(False)
                elif self.buffering and (self.done or self.error):
                    self.buffering = False
                    self.guard(2.0)
                    be.set_pause(False)
                if self.buffering:
                    with self.lock:
                        top = max([iv[1] for iv in self.cov] or [0.0])
                    pct = int(top * 100 / self.duration) if self.duration else 0
                    text = "%s %d %%" % (self.status if not self.cov else "Подготовка перевода", pct)
                else:
                    text = None
                if text != last_status:
                    last_status = text
                    app.ui(app.banner.status, text)
                if self.buffering:
                    continue
                if paused and not self.elastic:                              # пользователь на паузе: перевод ждёт
                    if shown is not None and pause_item is None:
                        pause_item = shown                                    # эта фраза должна прозвучать целиком после «Пуск»
                    tts.stop_now()
                    continue
                if pause_item is not None:
                    it, pause_item = pause_item, None
                    if pos * 1000 <= it.end + 1500:
                        try:
                            tts.say_now(it.speak, CFG["target"], int(min(220, it.pct * max(1.0, be.rate()))), it.mp3)
                        except Exception as e:
                            log("озвучка: %s" % e)
                target = CFG["target"]
                if mode == "interrupted":
                    if vol_set != 100:
                        be.volume(100)
                        vol_set = 100
                    if ptr < len(items) and pos * 1000 >= items[ptr].end:
                        it = items[ptr]
                        ptr += 1
                        if pos * 1000 - it.end < 4000:                 # ни одну фразу не пропускаем
                            self.run_interrupted(it, tts)
                            last_pos = None
                    continue
                v = int(CFG["orig_vol"]) if (mode == "live" and CFG["mute_original"]) else (0 if mode == "continuous" or CFG["mute_original"] else 100)
                if vol_set != v:
                    be.volume(v)
                    vol_set = v
                while ptr < len(items) and pos * 1000 >= items[ptr].start - 250:
                    it = items[ptr]
                    ptr += 1
                    if pos * 1000 <= it.end + 1500:
                        shown = it
                        app.ui(app.subs.show_sub, it.sub, "tr")
                        try:
                            tts.say_now(it.speak, target, int(min(220, it.pct * max(1.0, be.rate()))), it.mp3)   # видео ускорено — речь тоже
                        except Exception as e:
                            log("озвучка: %s" % e)
                if shown is not None and pos * 1000 > shown.end + 200:
                    if tts.busy():                                          # речь длиннее фразы: видео чуть ждёт
                        if not self.elastic:
                            self.elastic, elastic_t = True, now
                            self.guard(2.0)
                            be.set_pause(True)
                        elif now - elastic_t > 6:
                            tts.stop_now()
                    else:
                        if self.elastic:
                            self.elastic = False
                            self.guard(2.0)
                            be.set_pause(False)
                        app.ui(app.subs.hide)
                        shown = None
            except Exception as e:
                log("воспроизведение: %s" % e)
                time.sleep(0.3)
        tts.stop_now()

class CaptionSession(ItemPlayer):
    """Перевод по субтитрам видео из браузера: субтитры известны заранее, перевод готовится вперёд, видео идёт без повторов.
    Расширение отдаёт субтитры и управляет видео, вся озвучка, субтитры и меню — в программе Babelo."""
    def __init__(self, app, cues, lang):
        self.init_player(app, BridgeBackend(app.media))
        self.cues = sorted([c for c in cues if not c.get("skip")], key=lambda c: c["start"])
        self.src_lang = lang
        self.duration = max([c["end"] for c in self.cues] or [0.0]) + 1.0
        self.status = "Перевожу субтитры вперёд…"
        start = self.be.pos() or 0.0
        self.guard(3.0)
        if not self.be.in_ad():
            self.be.set_pause(True)                             # видео ждёт, пока готов первый запас перевода
        threading.Thread(target=self.pipeline, args=(max(0.0, start - 1.0), self.pipe_id), daemon=True).start()
        threading.Thread(target=self.play_loop, daemon=True).start()

    def restart_pipeline(self, from_s):
        self.pipe_id += 1
        self.last_restart = time.time()
        threading.Thread(target=self.pipeline, args=(max(0.0, from_s - 1.0), self.pipe_id), daemon=True).start()

    def pipeline(self, from_s, pid):
        try:
            if self.src_lang not in LANGS:
                raise RuntimeError("язык видео «%s» не поддерживается" % self.src_lang)
            if self.src_lang == CFG["target"]:
                raise RuntimeError("язык видео уже совпадает с языком перевода")
            HUB.load_nllb(int(CFG["threads"]))
            com_init()
            tts = Tts()
            for c in self.cues:
                if c["end"] < from_s - 0.5:
                    continue
                if self.stop_evt.is_set() or pid != self.pipe_id:
                    return
                self.add_segment(c["start"], c["end"], c["text"], self.src_lang, tts)
                self.extend_cov(from_s, c["end"])
            self.extend_cov(from_s, self.duration + 1.0)
            if from_s <= 1.0:
                self.done = True
            self.status = "Перевод готов"
        except Exception as e:
            self.error = str(e)
            log("субтитры: %s" % e)
            log(traceback.format_exc())
            self.app.ui(self.app.subs.flash, "Субтитры: %s" % str(e)[:100], 6000)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_evt.set()
        try:
            self.be.restore()
            if self.buffering or self.elastic:
                self.be.set_pause(False)                       # не оставляем видео на паузе
        except Exception:
            pass

class FileSession(ItemPlayer):
    """Проигрыватель файла: звук читается вперёд быстрее реального времени (Whisper), перевод готов заранее."""
    def __init__(self, app, source):
        self.source = source
        self.audio = None
        self.dragging = False
        self.app = app
        self._build_window()
        self.mpv = Mpv(self.vid.winfo_id())
        self.init_player(app, MpvBackend(self.mpv))
        self.mpv.command("loadfile", source)
        self.mpv.set("pause", "yes")
        self.mpv.set("volume", 0)
        threading.Thread(target=self.pipeline, args=(0.0, self.pipe_id), daemon=True).start()
        threading.Thread(target=self.play_loop, daemon=True).start()
        self.app.root.after(300, self._tick_ui)

    # --- окно
    def _build_window(self):
        root = self.app.root
        w = tk.Toplevel(root)
        w.title("Babelo — проигрыватель")
        w.geometry("1000x640")
        w.configure(bg="black")
        self.win = w
        self.vid = tk.Frame(w, bg="black")
        self.vid.pack(fill="both", expand=True)
        bar = tk.Frame(w, bg="#222")
        bar.pack(fill="x")
        tk.Button(bar, text="⏯", width=4, command=self.toggle_pause).pack(side="left", padx=4, pady=4)
        self.scale = ttk.Scale(bar, from_=0, to=1000, orient="horizontal")
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "dragging", True))
        self.scale.bind("<ButtonRelease-1>", self._seek_release)
        self.lbl = tk.Label(bar, text="0:00 / 0:00", fg="white", bg="#222", width=14)
        self.lbl.pack(side="left")
        tk.Button(bar, text="Меню F10", command=self.app.menu.toggle).pack(side="left", padx=4)
        tk.Button(bar, text="⛶", width=3, command=self.fullscreen).pack(side="left", padx=4)
        w.protocol("WM_DELETE_WINDOW", self.app.stop)
        w.update_idletasks()

    def fullscreen(self):
        self.win.attributes("-fullscreen", not bool(self.win.attributes("-fullscreen")))

    def toggle_pause(self):
        if self.buffering:
            return
        self.be.set_pause(not self.be.paused())

    def _seek_release(self, _e):
        self.dragging = False
        if self.duration > 0:
            self.guard()
            self.mpv.command("seek", self.scale.get() / 1000.0 * self.duration, "absolute+exact")

    def _tick_ui(self):
        if self.closed:
            return
        try:
            pos = self.mpv.getf("time-pos") or 0.0
            dur = self.duration or (self.mpv.getf("duration") or 0.0)
            if not self.dragging and dur > 0:
                self.scale.set(pos / dur * 1000)
            fmt = lambda s: "%d:%02d" % (int(s) // 60, int(s) % 60)
            self.lbl.config(text="%s / %s" % (fmt(pos), fmt(dur)))
            self.win.after(300, self._tick_ui)
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_evt.set()
        try:
            self.mpv.destroy()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def restart_pipeline(self, from_s):
        self.pipe_id += 1
        self.last_restart = time.time()
        threading.Thread(target=self.pipeline, args=(max(0.0, from_s - 1.0), self.pipe_id), daemon=True).start()

    def pipeline(self, from_s, pid):
        try:
            self.status = "Загружаю модели…"
            HUB.load(CFG["whisper"], int(CFG["threads"]))
            if self.audio is None:
                self.status = "Читаю звук файла…"
                from faster_whisper.audio import decode_audio
                self.audio = decode_audio(self.source, sampling_rate=16000)
                self.duration = len(self.audio) / 16000.0
            forced = None if CFG["source"] == "auto" else CFG["source"]
            audio = self.audio[int(from_s * 16000):]
            kw = dict(language=forced, beam_size=1, best_of=1, temperature=0.0, condition_on_previous_text=False)
            try:
                segs, info = HUB.whisper.transcribe(audio, vad_filter=True,
                                                    vad_parameters={"min_silence_duration_ms": 350}, **kw)
            except Exception as e:
                log("без VAD: %s" % e)
                segs, info = HUB.whisper.transcribe(audio, **kw)
            frm = forced or info.language
            self.src_lang = frm
            if frm not in LANGS:
                raise RuntimeError("язык файла «%s» не поддерживается" % frm)
            if frm == CFG["target"]:
                raise RuntimeError("язык файла уже совпадает с языком перевода")
            com_init()
            tts = Tts()
            cov_a = from_s
            for seg in segs:
                if self.stop_evt.is_set() or pid != self.pipe_id:
                    return
                s0, s1 = from_s + seg.start, from_s + seg.end
                text = seg.text.strip()
                if text and len(text) <= (s1 - s0) * 25 + 40:
                    self.add_segment(s0, s1, text, frm, tts)
                self.extend_cov(cov_a, s1)
            self.extend_cov(cov_a, self.duration)
            if from_s <= 1.0:
                self.done = True
            self.status = "Перевод файла готов"
        except Exception as e:
            self.error = str(e)
            log("файл: %s" % e)
            log(traceback.format_exc())
            self.app.ui(self.app.subs.flash, "Файл: %s" % str(e)[:100], 6000)

# ---------------------------------------------------------------- полноценная блокировка рекламы (те же списки фильтров, что у AdBlock)
AD_LISTS = [
    ("easylist", ["https://easylist.to/easylist/easylist.txt", "https://easylist-downloads.adblockplus.org/easylist.txt"]),
    ("ruadlist", ["https://easylist-downloads.adblockplus.org/advblock.txt"]),
    ("ublock", ["https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/filters.txt"]),
    ("unbreak", ["https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/unbreak.txt"]),
]
AD_TYPE_MAP = {"Stylesheet": "stylesheet", "Image": "image", "Media": "media", "Font": "font", "Script": "script",
               "XHR": "xmlhttprequest", "Fetch": "xmlhttprequest", "Ping": "ping", "WebSocket": "websocket"}
AD_OPT_TYPES = {"script", "image", "stylesheet", "object", "xmlhttprequest", "subdocument", "ping", "media", "font", "websocket",
                "other", "document"}
AD_BAD_SELECTOR = (":has-text", ":-abp-", ":xpath", ":matches-", ":upward", ":remove", ":style(", ":nth-ancestor",
                   ":min-text-length", ":watch-attr", ":others", ":contains(", ":-ms-")
_AD_TOKEN = re.compile(r"[a-z0-9%]{3,}")
_AD_COMMON = {"http", "https", "www", "com", "html", "net", "org"}

def reg_domain(host):
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])

def host_suffixes(host):
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]

def abp_regex(pat, left, right):
    rx = re.escape(pat).replace(r"\*", ".*").replace(r"\^", r"(?:[^\w.%-]|$)")
    prefix = r"^(?:[\w-]+:)?//(?:[^/?#]*\.)?" if left == "host" else ("^" if left == "start" else "")
    return re.compile(prefix + rx + ("$" if right else ""), re.I)

def abp_token(pat, left, right):
    """Самый длинный «безопасный» кусок правила: по нему правило ищется быстро, а не перебором всех."""
    best = ""
    for m in re.finditer(r"[a-z0-9%]+", pat):
        tok, a, b = m.group(0), m.start(), m.end()
        if len(tok) < 3 or tok in _AD_COMMON:
            continue
        if a == 0 and not left:
            continue
        if a > 0 and pat[a - 1] == "*":
            continue
        if b == len(pat) and not right:
            continue
        if b < len(pat) and pat[b] == "*":
            continue
        if len(tok) > len(best):
            best = tok
    return best

def abp_opts(o):
    third, inc, exc, dinc, dexc = None, set(), set(), set(), set()
    for x in o.split(","):
        x = x.strip().lower()
        if not x:
            continue
        neg = x.startswith("~")
        k = x[1:] if neg else x
        if k in ("third-party", "3p"):
            third = not neg
        elif k in ("first-party", "1p"):
            third = neg
        elif k in AD_OPT_TYPES:
            (exc if neg else inc).add(k)
        elif k in ("important", "match-case"):
            pass
        elif k.startswith("domain="):
            for d in k[7:].split("|"):
                if d.startswith("~"):
                    dexc.add(d[1:])
                elif d:
                    dinc.add(d)
        else:
            return None                                             # неподдерживаемый параметр — правило пропускаем
    return (third, frozenset(inc) or None, frozenset(exc), frozenset(dinc), frozenset(dexc))

def abp_opts_ok(o, third, kind, page_host):
    t, inc, exc, dinc, dexc = o
    if t is not None and t != third:
        return False
    if inc is not None and kind not in inc:
        return False
    if kind in exc:
        return False
    if dinc or dexc:
        def on(dset):
            return any(page_host == d or page_host.endswith("." + d) for d in dset)
        if dinc and not (page_host and on(dinc)):
            return False
        if dexc and page_host and on(dexc):
            return False
    return True

class AdEngine:
    """Блокировщик рекламы: сетевые правила EasyList/RU AdList/uBlock (подмножество синтаксиса Adblock Plus) и скрытие блоков."""
    def __init__(self):
        self.ready = False
        self.loading = False
        self.info = "Фильтры ещё не загружены"
        self.blocked = 0
        self._reset()

    def _reset(self):
        self.hosts, self.idx, self.rest = {}, {}, []
        self.ex_hosts, self.ex_idx, self.ex_rest = {}, {}, []
        self.c_idx, self.c_always, self.c_dom, self.c_exc = {}, [], {}, {}
        self.site_allow = set()
        self.n_net = self.n_cos = 0

    # ---------- разбор строки списка
    def add_line(self, line):
        if not line or line[0] in "![":
            return
        if "##" in line or "#@#" in line:
            self._cosmetic(line)
            return
        if "#?#" in line or "#$#" in line or "#%#" in line:
            return
        exc = line.startswith("@@")
        if exc:
            line = line[2:]
        opts = None
        i = line.rfind("$")
        if i > 0 and re.match(r"^[~\w\-,=|.*:/]+$", line[i + 1:]) and not line.endswith("/"):
            opts = abp_opts(line[i + 1:])
            if opts is None:
                return
            line = line[:i]
        elif i == 0:
            return
        if opts is None:
            opts = (None, None, frozenset(), frozenset(), frozenset())
        if len(line) >= 2 and line[0] == "/" and line[-1] == "/":
            return                                                   # регулярные выражения не поддерживаем
        left = "host" if line.startswith("||") else ("start" if line.startswith("|") else "")
        core = line[2:] if left == "host" else (line[1:] if left == "start" else line)
        right = core.endswith("|")
        if right:
            core = core[:-1]
        core = core.lower()
        if not core:
            return
        if exc and opts[1] and ("document" in opts[1]) and left == "host":     # @@||сайт^$document — сайт без блокировки
            m = re.match(r"^([a-z0-9.-]+)\^?$", core)
            if m:
                self.site_allow.add(m.group(1))
            return
        hosts, idx, rest = (self.ex_hosts, self.ex_idx, self.ex_rest) if exc else (self.hosts, self.idx, self.rest)
        self.n_net += 1
        m = re.match(r"^([a-z0-9.-]+)\^?$", core) if left == "host" and not right else None
        if m and "." in m.group(1):
            hosts.setdefault(m.group(1), []).append(opts)
            return
        rule = [core, left, right, opts, None]                       # [шаблон, якорь, конец, параметры, скомпилированный]
        tok = abp_token(core, left, right)
        if tok:
            idx.setdefault(tok, []).append(rule)
        else:
            rest.append(rule)

    def _cosmetic(self, line):
        exc = "#@#" in line
        dom, sel = line.split("#@#" if exc else "##", 1)
        sel = sel.strip()
        if not sel or sel.startswith("+js") or any(b in sel for b in AD_BAD_SELECTOR):
            return
        domains = [d.lower() for d in dom.split(",") if d and not d.startswith("~")]
        if exc:
            for d in domains or ["*"]:
                self.c_exc.setdefault(d, set()).add(sel)
            return
        self.n_cos += 1
        if domains:
            for d in domains:
                self.c_dom.setdefault(d, []).append(sel)
            return
        m = re.search(r"[.#]([A-Za-z_][\w-]*)", sel)
        if m:
            self.c_idx.setdefault(sel[m.start()] + m.group(1), []).append(sel)
        elif len(self.c_always) < 4000:
            self.c_always.append(sel)

    # ---------- проверка запроса
    def _match(self, low, host, kind, third, page_host, hosts, idx, rest):
        for suf in host_suffixes(host):
            for o in hosts.get(suf, ()):
                if abp_opts_ok(o, third, kind, page_host):
                    return True
        cands = []
        for tok in set(_AD_TOKEN.findall(low)):
            r = idx.get(tok)
            if r:
                cands.extend(r)
        for rule in cands + rest:
            if not abp_opts_ok(rule[3], third, kind, page_host):
                continue
            if rule[4] is None:
                rule[4] = abp_regex(rule[0], rule[1], rule[2])
            if rule[4].search(low):
                return True
        return False

    def site_allowed(self, page_host):
        return bool(page_host) and any(x in self.site_allow for x in host_suffixes(page_host))

    def should_block(self, url, kind, page_host):
        """True — запрос нужно заблокировать. Главные документы вкладки не блокируются."""
        if not self.ready or kind == "document" or self.site_allowed(page_host):
            return False
        import urllib.parse
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if not host:
            return False
        third = bool(page_host) and reg_domain(host) != reg_domain(page_host)
        low = url.lower()
        if not self._match(low, host, kind, third, page_host, self.hosts, self.idx, self.rest):
            return False
        return not self._match(low, host, kind, third, page_host, self.ex_hosts, self.ex_idx, self.ex_rest)

    def cosmetic(self, host, classes, ids, first):
        """Селекторы скрытия для страницы: по классам и id, которые на ней есть, плюс правила для этого сайта."""
        host = (host or "").lower()
        if not self.ready or self.site_allowed(host):
            return []
        out = []
        sufs = host_suffixes(host) if host else []
        if first:
            out += self.c_always
            for d in sufs:
                out += self.c_dom.get(d, [])
        for c in classes[:3000]:
            out += self.c_idx.get("." + c, [])
        for i in ids[:3000]:
            out += self.c_idx.get("#" + i, [])
        exc = set(self.c_exc.get("*", set()))
        for d in sufs:
            exc |= self.c_exc.get(d, set())
        return [x for x in out if x not in exc][:8000]

    # ---------- загрузка и обновление списков (в фоне; сначала берутся сохранённые)
    def load(self, force=False):
        if self.loading:
            return
        self.loading = True
        try:
            import requests
            d = os.path.join(DATA, "adblock")
            os.makedirs(d, exist_ok=True)
            texts, newest = [], 0.0
            for name, urls in AD_LISTS:
                path = os.path.join(d, name + ".txt")
                stale = force or not os.path.exists(path) or time.time() - os.path.getmtime(path) > 3 * 86400
                if stale:
                    for u in urls:
                        try:
                            r = requests.get(u, timeout=60, headers={"User-Agent": "Babelo-adblock/1"})
                            r.raise_for_status()
                            if len(r.content) < 2000:
                                continue
                            with open(path + ".tmp", "wb") as f:
                                f.write(r.content)
                            os.replace(path + ".tmp", path)
                            break
                        except Exception as e:
                            log("список фильтров %s: %s" % (name, str(e)[:80]))
                if os.path.exists(path):
                    with open(path, encoding="utf-8", errors="replace") as f:
                        texts.append(f.read())
                    newest = max(newest, os.path.getmtime(path))
            self.ready = False
            self._reset()
            for text in texts:
                for line in text.splitlines():
                    try:
                        self.add_line(line.strip())
                    except Exception:
                        pass
            for fn in ("adblock-user.txt",):                          # свои правила в синтаксисе Adblock Plus
                try:
                    with open(os.path.join(DATA, fn), encoding="utf-8") as f:
                        for line in f:
                            self.add_line(line.strip())
                except OSError:
                    pass
            try:
                with open(os.path.join(DATA, "adblock-allow.txt"), encoding="utf-8") as f:
                    for line in f:
                        h = line.split("#")[0].strip().lower()
                        if h:
                            self.site_allow.add(h)
            except OSError:
                pass
            for dom in ad_domains():
                self.hosts.setdefault(dom, []).append((None, None, frozenset(), frozenset(), frozenset()))
            self.ready = self.n_net > 100 or bool(self.hosts)
            when = time.strftime("%d.%m.%Y", time.localtime(newest)) if newest else "—"
            self.info = ("Фильтры: %d правил блокировки, %d правил скрытия, списки от %s" % (self.n_net, self.n_cos, when)
                         if self.n_net > 100 else "Списки фильтров не скачались — работает запасной список доменов")
            log(self.info)
        except Exception as e:
            self.info = "Фильтры: ошибка (%s)" % str(e)[:80]
            log(traceback.format_exc())
        finally:
            self.loading = False

ADBLOCK = AdEngine()

# ---------------------------------------------------------------- свой браузер Babelo
BROWSER_SHIM = r"""
const pend = {};
let seq = 0;
window.__babeloReply = (id, js) => { const cb = pend[id]; if (cb) { delete pend[id]; try { cb(JSON.parse(js)); } catch (e) { cb(null); } } };
const chrome = {
  runtime: {
    lastError: null,
    getManifest: () => ({ version: "__EXTVER__" }),
    sendMessage: (msg, cb) => {
      cb = cb || (() => {});
      if (!msg || msg.type !== "local") { cb({ ok: false }); return; }
      const id = ++seq;
      pend[id] = (data) => cb(data ? { ok: true, data } : { ok: false });
      try { window.babeloPost(JSON.stringify({ id, path: msg.path, body: msg.body || null })); }
      catch (e) { delete pend[id]; cb({ ok: false, error: String(e) }); return; }
      setTimeout(() => { if (pend[id]) { delete pend[id]; cb({ ok: false, error: "timeout" }); } }, 20000);
    }
  },
  storage: { local: { get: (k, cb) => cb({}), set: (o, cb) => { if (cb) cb(); } } }
};
"""

def media_endpoint(path, body):
    """Тот же обмен, что у расширения: состояние видео -> команда; результат команды; проверка связи."""
    if path == "/media/state":
        cmd = BRIDGE.update(body)
        return {"cmd": cmd, "session": BRIDGE.session, "ext": ext_version(), "pinned": BRIDGE.pinned}
    if path == "/media/result":
        BRIDGE.put_result(int(body.get("id", 0)), body.get("data"))
        return {}
    if path == "/adblock/cosmetic":
        return {"sel": ADBLOCK.cosmetic(body.get("host", ""), body.get("classes") or [], body.get("ids") or [], bool(body.get("first")))}
    if path == "/ping":
        return {"ok": True, "build": APP_BUILD, "ext": ext_version()}
    return None

AD_DOMAINS = [
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "googletagservices.com", "adservice.google.com",
    "2mdn.net", "adnxs.com", "adsrvr.org", "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "rubiconproject.com",
    "pubmatic.com", "openx.net", "casalemedia.com", "amazon-adsystem.com", "moatads.com", "advertising.com", "adform.net",
    "smartadserver.com", "teads.tv", "mgid.com", "revcontent.com", "bidswitch.net", "contextweb.com", "lijit.com",
    "sharethrough.com", "33across.com", "indexww.com", "triplelift.com", "media.net", "adcolony.com", "applovin.com",
    "inmobi.com", "vungle.com", "chartboost.com", "adsafeprotected.com", "doubleverify.com", "serving-sys.com",
    "popads.net", "popcash.net", "propellerads.com", "exoclick.com", "trafficjunky.com", "juicyads.com", "adsterra.com",
    "clickadu.com", "onclickads.net", "hilltopads.net", "yandexadexchange.net", "an.yandex.ru", "adfox.ru", "begun.ru",
    "adriver.ru", "ad.mail.ru"]

def ad_domains():
    """Встроенный список + свои домены из %LOCALAPPDATA%\\Babelo\\adblock-extra.txt (по одному в строке)."""
    doms = list(AD_DOMAINS)
    try:
        with open(os.path.join(DATA, "adblock-extra.txt"), encoding="utf-8") as f:
            for line in f:
                d = line.split("#")[0].strip().lower()
                if d:
                    doms.append(d)
    except OSError:
        pass
    return doms

def ad_block_patterns():
    pats = []
    for d in ad_domains():
        pats += ["*://%s/*" % d, "*://*.%s/*" % d]
    return pats + ["*://*.youtube.com/pagead/*", "*://www.youtube.com/api/stats/ads*", "*://www.youtube.com/ptracking*",
                   "*://www.youtube.com/get_midroll_info*", "*://*/pagead/ads?*"]

def is_ad_url(url):
    import urllib.parse
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in ad_domains()):
        return True
    try:
        return ADBLOCK.should_block(url, "other", "")
    except Exception:
        return False

def browser_inject_script():
    """Тот же код, что у расширения (субтитры, управление видео), но внутри собственного окна: без расширений."""
    def rd(name):
        with open(os.path.join(EXT_DIR, name), encoding="utf-8") as f:
            return f.read()
    ads = ""
    if CFG.get("br_adblock", True):
        try:
            ads = rd("adblock.js") + "\n"
        except OSError:
            log("adblock.js не найден")
    return ("window.__babeloBuiltin = true;\n(function(){\n" + BROWSER_SHIM.replace("__EXTVER__", ext_version()) + "\n" +
            rd("core.js") + "\n" + rd("content_main.js") + "\n" + ads + rd("content.js") + "\n})();")

def find_browser_exe(which="auto"):
    rels = {"chrome": r"Google\Chrome\Application\chrome.exe", "edge": r"Microsoft\Edge\Application\msedge.exe"}
    order = ["chrome", "edge"] if which in ("auto", "chrome") else ["edge", "chrome"]
    if which in ("chrome", "edge"):
        order = [which] + [x for x in order if x != which]          # выбранный первым, второй — запасной
    for name in order:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            if base and os.path.exists(os.path.join(base, rels[name])):
                return os.path.join(base, rels[name])
    return None

def normalize_url(text, search="google"):
    """Адрес, домен или слова -> ссылка; слова ищутся в Google (или на YouTube)."""
    import urllib.parse
    t = (text or "").strip()
    if not t:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return t
    if " " not in t and "." in t:
        return "https://" + t
    if search == "youtube":
        return "https://www.youtube.com/results?search_query=" + urllib.parse.quote(t)
    return "https://www.google.com/search?q=" + urllib.parse.quote(t)

def prepare_browser_profile(profile):
    """Настройки профиля до запуска: закладки на виду, вопрос «куда сохранить» при скачивании, своя папка загрузок."""
    d = os.path.join(profile, "Default")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "Preferences")
    try:
        with open(path, encoding="utf-8") as f:
            prefs = json.load(f)
    except Exception:
        prefs = {}

    def setp(dotted, value):
        cur = prefs
        parts = dotted.split(".")
        for k in parts[:-1]:
            cur = cur.setdefault(k, {})
        cur[parts[-1]] = value
    setp("bookmark_bar.show_on_all_tabs", True)
    setp("translate.enabled", True)
    if CFG.get("br_adblock", True):
        setp("profile.default_content_setting_values.popups", 2)               # всплывающие окна запрещены
    setp("download.prompt_for_download", bool(CFG.get("br_ask_dir", True)))
    folder = (CFG.get("br_dl_dir") or "").strip()
    if folder:
        os.makedirs(folder, exist_ok=True)
        setp("download.default_directory", folder)
        setp("savefile.default_directory", folder)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False)

class PageConn(threading.Thread):
    """Одна страница встроенного браузера: подключаемся по протоколу отладки, вставляем наш код, отвечаем на его запросы."""
    def __init__(self, br, target):
        super().__init__(daemon=True)
        self.br, self.target, self.ws = br, target, None
        self.lock = threading.Lock()
        self.n = 0
        self.main_frame = None
        self.page_url = ""

    def page_host(self):
        import urllib.parse
        return (urllib.parse.urlparse(self.page_url).hostname or "").lower()

    def on_paused(self, p):
        """Каждый запрос страницы либо блокируется правилами списков, либо пропускается."""
        rid, rtype = p["requestId"], p.get("resourceType", "Other")
        url = p.get("request", {}).get("url", "")
        if rtype == "Document" and self.main_frame is None:
            self.main_frame, self.page_url = p.get("frameId"), url
        kind = AD_TYPE_MAP.get(rtype, "other")
        if rtype == "Document":
            kind = "document" if p.get("frameId") == self.main_frame else "subdocument"
        block = False
        try:
            block = ADBLOCK.should_block(url, kind, self.page_host())
        except Exception as e:
            log("блокировщик: %s" % e)
        if block:
            ADBLOCK.blocked += 1
            self.send("Fetch.failRequest", {"requestId": rid, "errorReason": "BlockedByClient"})
        else:
            self.send("Fetch.continueRequest", {"requestId": rid})

    def send(self, method, params=None):
        with self.lock:
            self.n += 1
            self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))

    def handle(self, payload, ctx):
        req = json.loads(payload)
        data = media_endpoint(req["path"], req.get("body") or {})
        js = "window.__babeloReply(%d, %s)" % (req["id"], json.dumps(json.dumps(data if data is not None else {})))
        self.send("Runtime.evaluate", {"expression": js, "contextId": ctx} if ctx else {"expression": js})

    def run(self):
        import websocket
        try:
            self.ws = websocket.create_connection(self.target["webSocketDebuggerUrl"], suppress_origin=True, timeout=30,
                                                  enable_multithread=True)
            script = self.br.script()
            if CFG.get("br_adblock", True):
                self.send("Network.enable")
                self.send("Network.setBlockedURLs", {"urls": ad_block_patterns()})     # запросы к рекламным доменам
            self.send("Runtime.enable")
            self.send("Page.enable")
            if CFG.get("br_adblock", True) and ADBLOCK.ready:
                self.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
            self.send("Runtime.addBinding", {"name": "babeloPost"})
            self.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
            self.send("Runtime.evaluate", {"expression": script})           # страница уже загружена — вставляем сразу
            while not self.br.stop_evt.is_set():
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                msg = json.loads(raw)
                method = msg.get("method")
                if method == "Fetch.requestPaused":
                    try:
                        self.on_paused(msg["params"])
                    except Exception as e:
                        log("перехват запроса: %s" % e)
                    continue
                if method == "Page.frameNavigated" and not msg["params"]["frame"].get("parentId"):
                    self.main_frame, self.page_url = msg["params"]["frame"]["id"], msg["params"]["frame"].get("url", "")
                    continue
                if method == "Runtime.bindingCalled" and msg["params"].get("name") == "babeloPost":
                    try:
                        self.handle(msg["params"]["payload"], msg["params"].get("executionContextId"))
                    except Exception as e:
                        log("браузер: запрос страницы: %s" % e)
        except Exception as e:
            log("браузер: страница отключилась (%s)" % str(e)[:100])
        finally:
            try:
                self.ws.close()
            except Exception:
                pass
            self.br.pages.pop(self.target.get("id"), None)

class BuiltinBrowser:
    """Собственное окно браузера Babelo: Microsoft Edge в режиме приложения со своим профилем, под управлением программы.
    Видео играет штатным плеером сайта (ничего не скачивается), а программа готовит перевод вперёд и управляет паузой/перемоткой."""
    def __init__(self, app):
        self.app = app
        self.proc = None
        self.port = 0
        self.pages = {}
        self.alive = False
        self.exe = None
        self.profile = None
        self.closed = set()
        self.stop_evt = threading.Event()
        self._script = None

    def script(self):
        if self._script is None:
            self._script = browser_inject_script()
        return self._script

    def open(self, text, search=None):
        url = normalize_url(text, search or CFG.get("br_search", "google")) or CFG["br_home"]
        if self.alive:
            if CFG.get("br_mode") == "app" and self._page() is not None:
                self.navigate(url)
            else:                                       # новая вкладка в уже открытом браузере
                subprocess.Popen([self.exe, "--user-data-dir=" + self.profile, url])
            return
        exe = find_browser_exe(CFG.get("br_which", "auto"))
        if not exe:
            raise RuntimeError("не найден Google Chrome или Microsoft Edge (нужен один из них)")
        install_extension_files()
        self._script = None
        import socket
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        self.port = sk.getsockname()[1]
        sk.close()
        self.exe = exe
        self.profile = os.path.join(DATA, "browser-profile") if CFG.get("br_remember", True) else \
            os.path.join(tempfile.gettempdir(), "babelo-browser-%d" % os.getpid())
        try:
            prepare_browser_profile(self.profile)
        except Exception as e:
            log("профиль браузера: %s" % e)
        args = [exe, "--user-data-dir=" + self.profile, "--remote-debugging-port=%d" % self.port, "--remote-allow-origins=*",
                "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check",
                "--disable-background-mode", "--window-size=1280,800", "--disable-session-crashed-bubble",
                "--disable-features=IsolateOrigins,site-per-process", "--disable-site-isolation-trials"]
        args += ["--app=" + url] if CFG.get("br_mode") == "app" else [url]
        self.stop_evt.clear()
        self.pages = {}
        self.proc = subprocess.Popen(args)
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        import requests
        misses = 0
        while not self.stop_evt.is_set():
            try:
                for t in requests.get("http://127.0.0.1:%d/json" % self.port, timeout=2).json():
                    url = t.get("url", "")
                    if CFG.get("br_adblock", True) and t.get("type") == "page" and is_ad_url(url):
                        if t.get("id") not in self.closed:                              # всплывающее окно с рекламой
                            self.closed.add(t.get("id"))
                            try:
                                requests.get("http://127.0.0.1:%d/json/close/%s" % (self.port, t["id"]), timeout=2)
                                log("закрыто рекламное окно: %s" % url[:80])
                            except Exception:
                                pass
                        continue
                    if (t.get("type") == "page" and t.get("id") not in self.pages and t.get("webSocketDebuggerUrl")
                            and not url.startswith(("devtools://", "chrome-extension://", "edge://", "chrome://", "chrome-untrusted://", "about:"))):
                        c = PageConn(self, t)
                        self.pages[t["id"]] = c
                        c.start()
                self.alive = True
                misses = 0
            except Exception:
                misses += 1
                if misses >= 6 and (self.proc is None or self.proc.poll() is not None):
                    break
            time.sleep(0.8)
        self.alive = False
        log("встроенный браузер закрыт")

    def _page(self):
        pages = [p for p in self.pages.values() if p.ws is not None]
        return pages[-1] if pages else None

    def navigate(self, url):
        p = self._page()
        if p is None:
            raise RuntimeError("окно браузера ещё не готово, повторите через секунду")
        p.send("Page.navigate", {"url": url})

    def eval(self, js):
        p = self._page()
        if p is not None:
            p.send("Runtime.evaluate", {"expression": js})

    def close(self):
        self.stop_evt.set()
        self.alive = False
        try:
            if self.proc is not None and self.proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], creationflags=0x08000000,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.filesess = None
        self.starting = False
        self.views = []
        self.voice_thread = None
        self.voice_cancel = threading.Event()
        self._switch_seen = None
        self._auto_key = None
        self.browser = BuiltinBrowser(self)
        try:
            st = ttk.Style(self.root)
            st.configure("D.TLabel", background=BG, foreground="white")
            st.configure("D.TFrame", background=BG)
            st.configure("D.TCheckbutton", background=BG, foreground="white")
        except Exception:
            pass
        try:
            install_extension_files()
        except Exception as e:
            log("расширение: %s" % e)
        self.server = LocalServer()
        self.server.start()
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
        self.root.after(9000, self._maybe_prompt_ext)
        self.root.after(1500, self._bridge_watch)
        if CFG.get("br_adblock", True):
            threading.Thread(target=ADBLOCK.load, daemon=True).start()
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
        if self.engine is not None or self.filesess is not None:
            self.stop()

    def banner_idle(self):
        self.banner.status(None)                                  # подсказка остаётся, строка состояния очищается

    def on_f10(self):
        now = time.time()
        quick = now - self.last_f10 < 0.45
        self.last_f10 = now
        if quick:                                                 # двойное F10: полный перезапуск программы
            self.restart_program()
        elif self.engine is None and self.filesess is None and not self.starting:
            self.start()
        else:
            self.menu.toggle()

    def restart_program(self):
        """Принудительно закрывает программу целиком (перевод, голос, звук, фоновые процессы) и запускает её заново."""
        log("двойное F10: полный перезапуск программы")
        try:
            self.stop()
            self.muter.restore()
            self.master.set(False)
            self.subs.hide()
            self.browser.close()
            NEURAL.close()
        except Exception as e:
            log("перезапуск: %s" % e)
        try:
            q = lambda x: x.replace("'", "''")
            if getattr(sys, "frozen", False):
                cmd = "Start-Process -FilePath '%s' -WorkingDirectory '%s'" % (q(sys.executable), q(os.path.dirname(sys.executable)))
            else:
                cmd = "Start-Process -FilePath '%s' -ArgumentList '\"%s\"'" % (q(sys.executable), q(os.path.abspath(__file__)))
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                              "Start-Sleep -Milliseconds 2500; " + cmd], creationflags=0x08000000, close_fds=True)
        except Exception as e:
            log("не удалось запустить заново: %s" % e)
        self.root.after(300, lambda: os._exit(0))

    def restart_session(self, why):
        """Открыто другое видео или другая вкладка: перевод полностью перезапускается для нового видео."""
        log("перезапуск перевода: %s" % why)
        self.stop()
        self.set_status("%s — перевод запускается заново…" % why)
        self.root.after(900, self.start)

    def _bridge_watch(self):
        try:
            active = (self.engine is not None or isinstance(self.filesess, CaptionSession)) and not self.starting
            if (CFG.get("br_auto") and self.browser.alive and self.engine is None and self.filesess is None
                    and not self.starting):
                key = BRIDGE.playing_tab("bi-")                       # во встроенном браузере заиграло видео
                if key and key != self._auto_key:
                    self._auto_key = key
                    log("встроенный браузер: видео заиграло — включаю перевод")
                    self.start()
            cand = BRIDGE.switch_candidate() if active else None
            now = time.time()
            if cand is None:
                self._switch_seen = None
            elif self._switch_seen is None or self._switch_seen[0] != cand:
                self._switch_seen = (cand, now)
            elif now - self._switch_seen[1] >= 1.2:               # устойчиво, не мигание
                self._switch_seen = None
                self.restart_session("Открыто другое видео" if cand[0] == "tab" else "Переключено видео в этой вкладке")
        except Exception as e:
            log("наблюдение за вкладками: %s" % e)
        self.root.after(500, self._bridge_watch)

    def on_mode_changed(self):
        if CFG["mode"] != "live" and not self.media.available():
            self.subs.flash("Для этого режима нужен плеер Windows с управлением (YouTube в Chrome или Edge). "
                            "Сейчас такой плеер не найден — включите видео.", 6000)

    def sync_ui(self):
        for v in list(self.views):
            try:
                v.refresh_controls()
            except Exception:
                pass

    def start(self):
        if self.engine is not None or self.filesess is not None:
            return
        self.ensure_voice_async()
        BRIDGE.pin_best()                                         # переводим то видео, которое играет сейчас
        if not HUB.ready(CFG["whisper"]):
            self.show_main()
            self.set_status("Сначала скачайте модели (кнопка «Скачать модели»), потом нажмите F10.")
            return
        if self.release_job:
            self.root.after_cancel(self.release_job)
            self.release_job = None
        if self.starting:
            return
        want = CFG.get("text_src", "auto")
        if want != "sound" and BRIDGE.fresh():                    # браузер подключён: сначала ищем субтитры видео
            self.starting = True
            self.banner.on(HINT)
            self.banner.status("Ищу субтитры видео…")
            if CFG.get("esc_stops"):
                self.esc_hotkey(True)
            self.set_status("Ищу субтитры видео в браузере…")
            threading.Thread(target=self._find_captions, args=(want,), daemon=True).start()
            return
        if want == "captions":
            self.set_status("Для перевода по субтитрам нужно расширение Babelo в браузере (кнопка «Установить расширение»).")
            return
        self.start_engine()

    def _find_captions(self, want):
        res = BRIDGE.request("captions", {"lang": None if CFG["source"] == "auto" else CFG["source"]}, 15.0)
        self.ui(self._captions_ready, want, res)

    def _captions_ready(self, want, res):
        self.starting = False
        if self.engine is not None or self.filesess is not None:
            return
        cues = res.get("cues") if isinstance(res, dict) else None
        why = (res.get("error") if isinstance(res, dict) else None) or "расширение не ответило"
        if cues:
            try:
                self.filesess = CaptionSession(self, cues, res.get("lang") or "en")
                self.set_status("Перевод по субтитрам видео: перевод готовится вперёд, потом видео пойдёт без повторов. F10 — меню.")
                return
            except Exception as e:
                why = str(e)
                log(traceback.format_exc())
        if want == "captions":
            self.banner.hide()
            self.esc_hotkey(False)
            self.set_status("Субтитры не найдены: %s" % why)
            return
        self.set_status("Субтитров нет (%s) — перевожу по звуку." % why)
        self.start_engine()

    def start_engine(self):
        self.engine = Engine(self)
        self.engine.start()
        self.set_status("Перевод включён. F10 — меню, Esc — выключить, F10 два раза подряд — перезапуск программы.")
        self.banner.on(HINT)                                      # видна всё время, пока идёт перевод
        if CFG.get("esc_stops"):
            self.esc_hotkey(True)
        self.on_mode_changed()

    def install_extension(self):
        """Расширение нельзя поставить в Chrome/Edge без участия пользователя: готовим папку и ведём по шагам."""
        install_extension_files()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(EXT_DIR)
            self.root.update()
        except Exception:
            pass
        chrome = messagebox.askyesno("Расширение для браузера",
                                     "Папка расширения скопирована в буфер обмена:\n%s\n\nОткрыть страницу расширений в Google Chrome?\n"
                                     "(Нет — открыть в Microsoft Edge)" % EXT_DIR)
        url, exe = ("chrome://extensions", "chrome") if chrome else ("edge://extensions", "msedge")
        try:
            subprocess.Popen(["cmd", "/c", "start", "", exe, url], creationflags=0x08000000)
        except Exception as e:
            self.set_status("Не удалось открыть браузер: %s. Откройте %s вручную." % (e, url))
        messagebox.showinfo("Установка расширения (один раз)",
                            "1. Включите «Режим разработчика» (Chrome: справа вверху, Edge: слева внизу).\n"
                            "2. Нажмите «Загрузить распакованное расширение».\n"
                            "3. В окне выбора папки нажмите Ctrl+V (путь уже в буфере обмена), потом Enter и «Выбор папки».\n"
                            "4. Обновите вкладку с видео (F5).\n\n"
                            "Дальше расширение обновляется само вместе с программой.")

    def _maybe_prompt_ext(self):
        if BRIDGE.fresh() or CFG.get("ext_prompted") or self.root.state() == "withdrawn":
            return
        CFG["ext_prompted"] = True
        save_settings()
        if messagebox.askyesno("Babelo", "Установить расширение для браузера?\nОно нужно, чтобы переводить видео онлайн по субтитрам "
                                         "(без повторов) и перематывать его. Сделать это сейчас?"):
            self.install_extension()

    def stop(self):
        self.starting = False
        self._auto_key = BRIDGE.playing_tab("bi-") or self._auto_key      # видео играет дальше — само не включать
        BRIDGE.unpin()
        had_engine = self.engine is not None
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        fs, self.filesess = self.filesess, None
        if fs is not None:
            fs.close()
        self.muter.restore()
        self.master.set(False)
        self.esc_hotkey(False)
        self.menu.hide()
        self.subs.hide()
        self.banner.hide()
        try:
            if had_engine:
                self.media.play()                                 # не оставляем видео на паузе
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
            self.browser.close()
            NEURAL.close()
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
        ttk.Label(r, text="F10: включить · ещё раз: меню · Esc: выключить · два раза подряд: перезапуск программы").pack(side="bottom", pady=3)
        self.logbox = tk.Text(r, height=7, state="disabled", bg="#111", fg="#ddd", font=("Consolas", 9))
        self.logbox.pack(side="bottom", fill="x", padx=10, pady=4)
        outer, inner, canvas = make_scroll(r)
        outer.pack(side="top", fill="both", expand=True)
        self.main_view = self.build_settings(inner, False)

    def build_settings(self, parent, dark=False):
        """ВСЕ настройки программы. Одно и то же строится в главном окне и в меню по F10 — меню единое."""
        V = SettingsView()
        fb = ttk.Frame(parent)
        fb.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Button(fb, text="Открыть видео/аудио файл (проигрыватель с непрерывным переводом)", command=self.pick_file).pack(side="left", padx=2)
        ttk.Button(fb, text="Открыть ссылку…", command=self.pick_url).pack(side="left", padx=2)
        ttk.Button(fb, text="Установить расширение для браузера", command=self.install_extension).pack(side="left", padx=2)
        fb2 = ttk.Frame(parent)
        fb2.pack(fill="x", padx=10, pady=(2, 2))
        ttk.Button(fb2, text="Выключить перевод (Esc)", command=self.stop).pack(side="left", padx=2)
        ttk.Button(fb2, text="Перезапустить программу (двойное F10)", command=self.restart_program).pack(side="left", padx=2)

        # --- встроенный браузер: окно Chrome/Edge со своим профилем Babelo, под управлением программы
        ttk.Label(parent, text="Встроенный браузер Babelo", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        ttk.Label(parent, text="Адрес сайта, ссылка на видео или слова для поиска в Google:",
                  wraplength=480, justify="left").pack(anchor="w", padx=12)
        V.br_entry = ttk.Entry(parent)
        V.br_entry.pack(fill="x", padx=12, pady=2)
        V.br_entry.bind("<Return>", lambda e: self.browser_open(V.br_entry.get()))
        fb3 = ttk.Frame(parent)
        fb3.pack(fill="x", padx=10, pady=2)
        ttk.Button(fb3, text="Открыть / Найти в Google", command=lambda: self.browser_open(V.br_entry.get())).pack(side="left", padx=2)
        ttk.Button(fb3, text="Найти на YouTube", command=lambda: self.browser_open(V.br_entry.get(), "youtube")).pack(side="left", padx=2)
        ttk.Button(fb3, text="Закрыть браузер", command=self.browser.close).pack(side="left", padx=2)

        def br_combo(title, pairs, key):
            ttk.Label(parent, text=title).pack(anchor="w", padx=12)
            cb = ttk.Combobox(parent, state="readonly", values=[t for _, t in pairs])
            cb.set(label_of(pairs, CFG.get(key)))
            cb.pack(fill="x", padx=12, pady=(0, 3))

            def pick(_=None):
                CFG[key] = key_of(pairs, cb.get())
                save_settings()
            cb.bind("<<ComboboxSelected>>", pick)
        br_combo("Окно браузера (применяется при следующем открытии):", BR_MODES, "br_mode")
        br_combo("Какой браузер использовать:", BR_WHICH, "br_which")
        V.v_br_auto = tk.BooleanVar(value=bool(CFG.get("br_auto", True)))
        V.v_br_rem = tk.BooleanVar(value=bool(CFG.get("br_remember", True)))
        V.v_br_ask = tk.BooleanVar(value=bool(CFG.get("br_ask_dir", True)))
        V.v_br_ads = tk.BooleanVar(value=bool(CFG.get("br_adblock", True)))

        def br_opts():
            CFG["br_auto"] = bool(V.v_br_auto.get())
            CFG["br_remember"] = bool(V.v_br_rem.get())
            CFG["br_ask_dir"] = bool(V.v_br_ask.get())
            CFG["br_adblock"] = bool(V.v_br_ads.get())
            save_settings()
        ttk.Checkbutton(parent, text="Включать перевод видео сам, когда в браузере заиграло видео", variable=V.v_br_auto,
                        command=br_opts).pack(anchor="w", padx=12)
        ttk.Checkbutton(parent, text="Запоминать входы в аккаунты, закладки и историю (свой профиль)", variable=V.v_br_rem,
                        command=br_opts).pack(anchor="w", padx=12)
        ttk.Checkbutton(parent, text="При скачивании спрашивать, куда сохранить файл", variable=V.v_br_ask,
                        command=br_opts).pack(anchor="w", padx=12)
        ttk.Checkbutton(parent, text="Блокировать рекламу и всплывающие окна (списки фильтров как у AdBlock; реклама в видео не переводится)",
                        variable=V.v_br_ads, command=br_opts).pack(anchor="w", padx=12)
        rowa = ttk.Frame(parent)
        rowa.pack(fill="x", padx=12, pady=(0, 3))
        V.ad_info = ttk.Label(rowa, text=ADBLOCK.info, wraplength=330, justify="left")
        V.ad_info.pack(side="left", fill="x", expand=True)
        ttk.Button(rowa, text="Обновить фильтры", command=lambda: threading.Thread(target=lambda: ADBLOCK.load(True),
                                                                                    daemon=True).start()).pack(side="left", padx=(4, 0))
        ttk.Label(parent, text="Папка для скачанных файлов по умолчанию (пусто — «Загрузки»):").pack(anchor="w", padx=12)
        rowd = ttk.Frame(parent)
        rowd.pack(fill="x", padx=12, pady=(0, 3))
        V.br_dir = ttk.Entry(rowd)
        V.br_dir.insert(0, CFG.get("br_dl_dir", ""))
        V.br_dir.pack(side="left", fill="x", expand=True)

        def br_dir_save(_e=None):
            CFG["br_dl_dir"] = V.br_dir.get().strip()
            save_settings()

        def br_dir_pick():
            d = filedialog.askdirectory(parent=self.root, title="Папка для скачанных файлов")
            if d:
                V.br_dir.delete(0, "end")
                V.br_dir.insert(0, d)
                br_dir_save()
        V.br_dir.bind("<FocusOut>", br_dir_save)
        V.br_dir.bind("<Return>", br_dir_save)
        ttk.Button(rowd, text="Выбрать…", command=br_dir_pick).pack(side="left", padx=(4, 0))
        ttk.Label(parent, text="Стартовая страница браузера:").pack(anchor="w", padx=12)
        V.br_home = ttk.Entry(parent)
        V.br_home.insert(0, CFG.get("br_home", "https://www.google.com"))
        V.br_home.pack(fill="x", padx=12, pady=(0, 4))

        def br_home_save(_e=None):
            CFG["br_home"] = V.br_home.get().strip() or "https://www.google.com"
            save_settings()
        V.br_home.bind("<FocusOut>", br_home_save)
        V.br_home.bind("<Return>", br_home_save)
        ttk.Label(parent, text="Перевод страницы — как в Chrome: значок перевода в адресной строке или правая кнопка → «Перевести». "
                               "Любой язык выбирается в панели перевода (меню ⋮). Закладки: звёздочка в адресной строке или Ctrl+D. "
                               "Свои правила — файл %LOCALAPPDATA%\\Babelo\\adblock-user.txt (синтаксис AdBlock, например ||site.com^), "
                               "сайты без блокировки — adblock-allow.txt, свои домены — adblock-extra.txt. "
                               "Подгрузка видео вперёд — ползунок «Непрерывный: длина отрезка подгрузки» ниже.",
                  wraplength=480, justify="left").pack(anchor="w", padx=12, pady=(0, 6))
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=10, pady=4)
        names = [LANGS[c][0] for c in LANGS]
        codes = list(LANGS)
        V.v_src = tk.StringVar(value="Авто" if CFG["source"] == "auto" else LANGS.get(CFG["source"], ("Авто",))[0])
        V.v_dst = tk.StringVar(value=LANGS[CFG["target"]][0])
        V.v_whisper = tk.StringVar(value=CFG["whisper"])
        V.v_engine = tk.StringVar(value=label_of(ENGINES, CFG["engine"]))
        V.v_voice = tk.StringVar(value=CFG.get("voice", ""))

        def row(i, text, widget):
            ttk.Label(f, text=text).grid(row=i, column=0, sticky="w", pady=3)
            widget.grid(row=i, column=1, sticky="ew", padx=8, pady=3)
        f.columnconfigure(1, weight=1)
        cs = ttk.Combobox(f, textvariable=V.v_src, values=["Авто"] + names, state="readonly")
        cd = ttk.Combobox(f, textvariable=V.v_dst, values=names, state="readonly")
        cw = ttk.Combobox(f, textvariable=V.v_whisper, values=WHISPER_SIZES, state="readonly")
        ce = ttk.Combobox(f, textvariable=V.v_engine, state="readonly", values=[t for _, t in ENGINES])
        V.cv = ttk.Combobox(f, textvariable=V.v_voice, state="readonly")
        row(0, "Язык оригинала", cs)
        row(1, "Перевод на", cd)
        row(2, "Распознавание (tiny быстро … large-v3 точно)", cw)
        row(3, "Озвучка", ce)
        row(4, "Голос", V.cv)

        def pick_src(_=None):
            n = V.v_src.get()
            CFG["source"] = "auto" if n == "Авто" else codes[names.index(n)]
            save_settings()

        def fill_voices(_=None):
            lang = CFG["target"]
            if CFG["engine"] == "edge":
                vals = LANGS[lang][3]
            elif CFG["engine"] == "neural":
                vals = [lab for lab, _ in neural_list(lang)]
                if not vals:
                    self.set_status("Для этого языка нет нейросетевого голоса — будет говорить голос Windows.")
            else:
                vals = list_sapi_voices(lang)
            V.cv.config(values=vals)
            if CFG.get("voice") not in vals:
                CFG["voice"] = vals[0] if vals else ""
            V.v_voice.set(CFG["voice"])

        def pick_dst(_=None):
            CFG["target"] = codes[names.index(V.v_dst.get())]
            save_settings()
            fill_voices()
            self.ensure_voice_async()

        def pick_whisper(_=None):
            CFG["whisper"] = V.v_whisper.get()
            save_settings()
            self.refresh_models_state()
            if not HUB.ready(CFG["whisper"]):
                self.set_status("Для модели %s нужна загрузка — нажмите «Скачать модели»." % CFG["whisper"])

        def pick_engine(_=None):
            CFG["engine"] = key_of(ENGINES, V.v_engine.get())
            save_settings()
            fill_voices()
            self.ensure_voice_async()

        def pick_voice(_=None):
            CFG["voice"] = V.v_voice.get()
            save_settings()
            self.ensure_voice_async()
        cs.bind("<<ComboboxSelected>>", pick_src)
        cd.bind("<<ComboboxSelected>>", pick_dst)
        cw.bind("<<ComboboxSelected>>", pick_whisper)
        ce.bind("<<ComboboxSelected>>", pick_engine)
        V.cv.bind("<<ComboboxSelected>>", pick_voice)
        fill_voices()

        # те же настройки, что в коротком меню: режимы, субтитры, паузы, повторы, громкость
        V.refresh_controls = add_controls(parent, False, self)
        V.v_esc = tk.BooleanVar(value=bool(CFG.get("esc_stops", True)))

        def chg2():
            CFG["esc_stops"] = bool(V.v_esc.get())
            save_settings()
        ttk.Checkbutton(parent, text="Esc выключает перевод (пока перевод включён, Esc не работает в других программах)",
                        variable=V.v_esc, command=chg2).pack(anchor="w", padx=12, pady=(8, 0))

        # потоки распознавания — только здесь
        lab = ttk.Label(parent, text="")
        lab.pack(anchor="w", padx=12, pady=(8, 0))

        def upd(v):
            CFG["threads"] = int(float(v))
            lab.config(text="Потоки распознавания (больше — быстрее, но тяжелее): %d" % CFG["threads"])
        sc = ttk.Scale(parent, from_=2, to=12, orient="horizontal", command=upd)
        sc.set(int(CFG["threads"]))
        sc.bind("<ButtonRelease-1>", lambda e: save_settings())
        sc.pack(fill="x", padx=12)
        upd(CFG["threads"])

        V.models_lbl = ttk.Label(parent, text="", font=("Segoe UI", 9, "bold"))
        V.models_lbl.pack(anchor="w", padx=12, pady=(10, 0))
        V.dl_label = ttk.Label(parent, text="Загрузка не идёт.", wraplength=640)
        V.dl_label.pack(anchor="w", padx=12)
        V.dl_bar = ttk.Progressbar(parent, maximum=100)
        V.dl_bar.pack(fill="x", padx=12, pady=2)
        V.stats_lbl = ttk.Label(parent, text="Перевод выключен.", wraplength=640)
        V.stats_lbl.pack(anchor="w", padx=12, pady=2)
        tkf = ttk.Frame(parent)
        tkf.pack(fill="x", padx=12, pady=2)
        ttk.Label(tkf, text="Токен GitHub (нужен только для закрытого репозитория):").pack(side="left")
        V.v_token = tk.StringVar(value=str(CFG.get("github_token", "")))
        te = ttk.Entry(tkf, textvariable=V.v_token, show="•", width=22)
        te.pack(side="left", padx=6)

        def save_token(_=None):
            CFG["github_token"] = V.v_token.get().strip()
            save_settings()
        te.bind("<FocusOut>", save_token)
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Button(bar, text="Скачать модели", command=self.download).pack(side="left", padx=4)
        ttk.Button(bar, text="Отмена загрузки", command=lambda: self.dl_cancel.set()).pack(side="left", padx=4)
        V.btn_update = ttk.Button(bar, text="Проверить обновления", command=self.on_update_btn)
        V.btn_update.pack(side="left", padx=4)
        ttk.Button(bar, text="Проверить голос", command=self.test_voice).pack(side="left", padx=4)
        if dark:
            restyle(parent)
        self.views.append(V)
        return V

    def ensure_voice_async(self):
        """Выбран нейроголос, а файлов нет: скачиваем его в фоне (прогресс на полосе загрузки)."""
        if CFG["engine"] != "neural":
            return
        mid = neural_id(CFG["target"])
        if not mid or neural_ready(mid):
            return
        if self.voice_thread is not None and self.voice_thread.is_alive():
            return
        self.voice_cancel.clear()

        def run():
            try:
                download_neural(mid, lambda t, f: self.ui(self._on_progress, t, f), self.voice_cancel)
                self.ui(self.set_status, "Голос скачан ✓ Нажмите «Проверить голос».")
            except Cancelled:
                self.ui(self.set_status, "Загрузка голоса отменена.")
            except Exception as e:
                self.ui(self.set_status, "Не удалось скачать голос: %s. Пока говорит голос Windows; повторите выбор голоса." % e)
                log(traceback.format_exc())
        self.voice_thread = threading.Thread(target=run, daemon=True)
        self.voice_thread.start()
        self.set_status("Скачиваю нейроголос (~60 МБ) — прогресс на полосе загрузки.")

    def browser_open(self, text, search=None):
        try:
            self.browser.open(text, search)
            self.set_status("Встроенный браузер открыт. Включите видео — перевод начнётся сам (или нажмите F10).")
        except Exception as e:
            self.set_status("Встроенный браузер: %s" % e)
            log(traceback.format_exc())

    def vset(self, name, **kw):
        for v in list(self.views):
            try:
                getattr(v, name).config(**kw)
            except Exception:
                pass

    def vbar(self, value):
        for v in list(self.views):
            try:
                v.dl_bar["value"] = value
            except Exception:
                pass

    def pick_file(self):
        p = filedialog.askopenfilename(title="Видео или аудио", filetypes=[
            ("Видео и аудио", "*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.ts *.mp3 *.m4a *.wav *.flac *.ogg"), ("Все файлы", "*.*")])
        if p:
            self.open_file(p)

    def pick_url(self):
        u = simpledialog.askstring("Ссылка", "Прямая ссылка на видео или аудио (mp4, m3u8, mp3…):", parent=self.root)
        if u:
            self.open_file(u.strip())

    def open_file(self, path):
        if not HUB.ready(CFG["whisper"]):
            self.set_status("Сначала скачайте модели (кнопка «Скачать модели»).")
            return
        if self.engine is not None or self.filesess is not None:
            self.stop()
        try:
            self.filesess = FileSession(self, path)
        except Exception as e:
            self.filesess = None
            self.set_status("Не удалось открыть проигрыватель: %s" % e)
            log(traceback.format_exc())
            return
        self.banner.on(HINT)
        if CFG.get("esc_stops"):
            self.esc_hotkey(True)
        self.set_status("Файл открыт: перевод готовится вперёд, потом видео пойдёт с переводом. F10 — меню.")

    def refresh_models_state(self):
        w = os.path.exists(os.path.join(HUB.whisper_dir(CFG["whisper"]), "model.bin"))
        n = os.path.exists(os.path.join(NLLB_DIR, "model.bin"))
        self.vset("models_lbl", text="Модели:  распознавание (%s) — %s     перевод (NLLB) — %s" % (
            CFG["whisper"], "✓ скачано" if w else "✗ нет", "✓ скачано" if n else "✗ нет"))

    def _on_progress(self, text, frac):
        self.vset("dl_label", text=text)
        if frac is not None:
            self.vbar(frac * 100)

    def download(self):
        if self.dl_thread is not None and self.dl_thread.is_alive():
            self.set_status("Загрузка уже идёт — следите за полосой ниже.")
            return
        self.dl_cancel.clear()
        self.vbar(0)

        def run():
            try:
                HUB.download(CFG["whisper"], lambda t, f: self.ui(self._on_progress, t, f), self.dl_cancel)
                mid = neural_id(CFG["target"]) if CFG["engine"] == "neural" else None
                if mid:
                    download_neural(mid, lambda t, f: self.ui(self._on_progress, t, f), self.dl_cancel)
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
        self.vset("btn_update", text="Установить обновление (сборка %d)" % build)
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
            self.set_status("Сначала выключите перевод (Esc или кнопка «Выключить перевод»), потом обновляйте.")
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
        self.vbar(0)

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
        self.set_status("Устанавливаю обновление… Babelo закроется, откроется чёрное окно установки (не закрывайте его) — потом программа запустится сама.")
        self.muter.restore()
        self.master.set(False)
        try:
            NEURAL.close()
        except Exception:
            pass
        self.root.after(1200, lambda: os._exit(0))

    def _tick(self):
        e = self.engine
        if self.filesess is not None:
            fs = self.filesess
            txt = "Проигрыватель: %s · фраз переведено: %d · язык файла: %s" % (fs.status, len(fs.items), fs.src_lang)
        elif e is None:
            txt = "Перевод выключен. F10 — включить. Расширение браузера: %s." % ("подключено" if BRIDGE.fresh() else "не подключено")
        else:
            if not e.listening:
                sound = "подключаюсь к звуку компьютера…"
            else:
                sound = "звук идёт" if time.time() - e.last_loud < 2 else "тихо (включите видео или музыку)"
            s = e.stats
            if self.media.available():
                player = "плеер: %s%s" % ("расширение браузера" if self.media.src == "ext" else "Windows",
                                          "" if self.media.can_seek() else ", без перемотки")
            else:
                player = "плеер не найден"
            txt = "Перевод включён · слышу: %s · %s · фраз услышано %d, распознано %d, переведено %d, озвучено %d, ошибок %d" % (
                sound, player, s["heard"], s["recognised"], s["translated"], s["spoken"], s["errors"])
        self.vset("stats_lbl", text=txt)
        self.vset("ad_info", text="%s · заблокировано запросов: %d" % (ADBLOCK.info, ADBLOCK.blocked))
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
    if len(sys.argv) >= 3 and sys.argv[1] == "--neural-worker":      # отдельный процесс озвучки нейроголосом
        try:
            lf = open(neural_worker_log_path(), "a", buffering=1)
            os.dup2(lf.fileno(), 1)
            os.dup2(lf.fileno(), 2)
        except Exception:
            pass
        neural_worker_main(int(sys.argv[2]))
        return
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

