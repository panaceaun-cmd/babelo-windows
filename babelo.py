# -*- coding: utf-8 -*-
"""
Babelo для Windows — живой перевод любого звука, который играет на компьютере.
F10: первое нажатие — перевод включён; ещё раз — выезжает меню; два раза подряд — перевод выключен.
Распознавание (Whisper) -> перевод (NLLB) -> озвучка (голос Windows или Edge), оригинал заглушается, субтитры белым по чёрному.
"""
import os, sys, json, time, threading, queue, ctypes, traceback, re, tempfile, math, gc
from ctypes import wintypes

if os.name != "nt":
    print("Babelo работает только в Windows")
    sys.exit(1)

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

APP = "Babelo"
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP)
MODELS_DIR = os.path.join(DATA, "models")
NLLB_DIR = os.path.join(MODELS_DIR, "nllb")
os.makedirs(MODELS_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA, "babelo.log")
SETTINGS_FILE = os.path.join(DATA, "settings.json")
MUTED_FILE = os.path.join(DATA, "muted.json")
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
            "speed": 100, "volume": 100, "mute_original": True, "engine": "sapi", "voice": "", "threads": 4}

# ---------------------------------------------------------------- журнал и настройки
LOG_LINES = []
_log_lock = threading.Lock()

def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    with _log_lock:
        LOG_LINES.append(line)
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
class ModelHub:
    def __init__(self):
        self.whisper = None
        self.wkey = None
        self.nllb = None
        self.sp = None
        self.lock = threading.Lock()

    @staticmethod
    def whisper_dir(size):
        return os.path.join(MODELS_DIR, "whisper-" + size)

    def ready(self, size):
        return (os.path.exists(os.path.join(self.whisper_dir(size), "model.bin"))
                and os.path.exists(os.path.join(NLLB_DIR, "model.bin")))

    def download(self, size, progress):
        from faster_whisper.utils import download_model
        import huggingface_hub
        progress("Загрузка распознавания речи (Whisper %s)… это несколько минут" % size)
        download_model(size, output_dir=self.whisper_dir(size))
        progress("Загрузка модели перевода (NLLB, около 620 МБ)…")
        huggingface_hub.snapshot_download(
            "skywood/nllb-200-distilled-600M-ct2-int8", local_dir=NLLB_DIR,
            allow_patterns=["config.json", "model.bin", "sentencepiece.bpe.model", "shared_vocabulary.json"])
        progress("Модели готовы ✓")

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

    def translate(self, text, src, tgt):
        s_code, t_code = LANGS[src][1], LANGS[tgt][1]
        out = []
        for sent in [p for p in re.split(r"(?<=[.!?…。！？])\s+", text) if p.strip()]:
            toks = [s_code] + self.sp.encode(sent, out_type=str) + ["</s>"]
            res = self.nllb.translate_batch([toks], target_prefix=[[t_code]], beam_size=2, max_decoding_length=200)
            hyp = res[0].hypotheses[0]
            if hyp and hyp[0] == t_code:
                hyp = hyp[1:]
            out.append(self.sp.decode(hyp))
        return " ".join(out)

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

class Tts:
    """Голос Windows (SAPI, офлайн) или Edge (онлайн, качественнее)."""
    def __init__(self):
        self.sapi = None

    def _sapi(self):
        if self.sapi is None:
            import win32com.client
            self.sapi = win32com.client.Dispatch("SAPI.SpVoice")
        return self.sapi

    def speak(self, text, lang, stop_evt):
        if CFG["engine"] == "edge":
            try:
                return self._edge(text, lang, stop_evt)
            except Exception as e:
                log("Edge недоступен (%s), говорю голосом Windows" % e)
        self._sapi_speak(text, lang, stop_evt)

    def _sapi_speak(self, text, lang, stop_evt):
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
        sp.Rate = max(-10, min(10, round(10 * math.log(max(int(CFG["speed"]), 10) / 100.0, 3))))
        sp.Volume = int(CFG["volume"])
        sp.Speak(text, 1)                                  # асинхронно, ждём сами
        while not sp.WaitUntilDone(80):
            if stop_evt.is_set():
                sp.Speak("", 3)
                break

    def _edge(self, text, lang, stop_evt):
        import asyncio
        import edge_tts
        names = LANGS[lang][3]
        voice = CFG.get("voice", "") if CFG.get("voice", "") in names else names[0]
        path = os.path.join(tempfile.gettempdir(), "babelo_%d.mp3" % time.time_ns())

        async def gen():
            await edge_tts.Communicate(text, voice, rate="%+d%%" % (int(CFG["speed"]) - 100)).save(path)
        asyncio.run(gen())
        try:
            play_mp3(path, int(CFG["volume"]), stop_evt)
        finally:
            try:
                os.remove(path)
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

# ---------------------------------------------------------------- движок перевода
class Engine:
    def __init__(self, app):
        self.app = app
        self.stop_evt = threading.Event()
        self.gate = False                  # True: свой голос играет, звук не слушаем
        self.phrase_q = queue.Queue()
        self.speak_q = queue.Queue()
        self.hist = []
        self.stats = {"heard": 0, "recognised": 0, "translated": 0, "spoken": 0, "errors": 0}

    def start(self):
        for fn in (self.capture, self.worker, self.speaker):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.stop_evt.set()

    # --- слушаем звук компьютера (WASAPI loopback) и режем на фразы
    def capture(self):
        p = None
        stream = None
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
            log("слушаю: %s (%d Гц, %d кан.)" % (spk["name"], sr, ch))
            buf, n_samples, silent = [], 0, 0
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
                loud = len(m) > 0 and float(np.abs(m).mean()) > 0.012
                silent = 0 if loud else silent + 1
                if loud or buf:
                    buf.append(m)
                    n_samples += len(m)
                if not buf:
                    continue
                max_s = int(CFG["phrase_sec"] * 16000)
                pause_frames = max(1, int(CFG["pause_ms"] // 100))
                if silent < pause_frames and n_samples <= max_s:
                    continue
                audio = np.concatenate(buf)
                buf, n_samples, silent = [], 0, 0
                if len(audio) < 6400:
                    continue
                self.stats["heard"] += 1
                self.phrase_q.put((audio, time.time()))
                while self.phrase_q.qsize() > 2:                  # медленный ПК: держим только свежие фразы
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

    # --- распознать и перевести
    def worker(self):
        try:
            self.app.ui(self.app.subs.show, "Babelo: загрузка моделей…")
            HUB.load(CFG["whisper"], int(CFG["threads"]))
            self.app.ui(self.app.subs.hide)
        except Exception as e:
            self.app.fail("загрузка моделей", e)
            return
        while not self.stop_evt.is_set():
            try:
                audio, t = self.phrase_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.time() - t > 25:
                continue
            try:
                self.process(audio)
            except Exception as e:
                self.stats["errors"] += 1
                log("ошибка фразы: %s" % e)
                log(traceback.format_exc())
                self.app.ui(self.app.subs.flash, "⚠ " + str(e)[:80], 3500)

    def process(self, audio):
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
            return
        if frm == target:
            log("[%s] %s — язык уже целевой" % (frm, text[:60]))
            return
        if len(text) > secs * 25 + 40 or frm not in LANGS:
            log("[%s] «%s» — отброшено (мусор или язык не поддерживается)" % (frm, text[:60]))
            return
        self.stats["recognised"] += 1
        trc = clean_text(HUB.translate(text, frm, target))[:500]
        if not any(c.isalpha() for c in trc):
            return
        self.stats["translated"] += 1
        log("[%s→%s] %s → %s" % (frm, target, text[:60], trc[:60]))
        self.speak_q.put((trc, time.time()))

    # --- говорим, пока следующая фраза уже распознаётся
    def speaker(self):
        for mod in ("pythoncom", "comtypes"):
            try:
                __import__(mod).CoInitialize()
            except Exception:
                pass
        tts = Tts()
        while not self.stop_evt.is_set():
            try:
                text, t = self.speak_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.time() - t > 20:
                continue
            try:
                self.gate = True
                if CFG["mute_original"]:
                    self.app.muter.mute()
                self.app.ui(self.app.subs.show, text)
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
                    if self.speak_q.empty():
                        self.gate = False
        self.app.muter.restore()
        self.gate = False
        self.app.ui(self.app.subs.hide)

# ---------------------------------------------------------------- окна
BG = "#1c1c20"

class Subtitles:
    """Субтитры внизу экрана: белые буквы на чёрном, мышь проходит сквозь них."""
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
        sw = w.winfo_screenwidth()
        self.lbl = tk.Label(w, text="", fg="white", bg="black", font=("Segoe UI", 22, "bold"),
                            wraplength=int(sw * 0.8), justify="center", padx=28, pady=14)
        self.lbl.pack()
        w.withdraw()
        w.update_idletasks()
        try:
            u = ctypes.windll.user32
            hwnd = u.GetParent(w.winfo_id()) or w.winfo_id()
            style = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80 | 0x08000000)   # click-through, tool window, no activate
        except Exception:
            pass
        self.win = w

    def show(self, text):
        self._ensure()
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        self.lbl.config(text=text)
        self.win.update_idletasks()
        w, h = self.lbl.winfo_reqwidth(), self.lbl.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, sh - h - 90))
        self.win.deiconify()
        self.win.lift()

    def hide(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.win is not None:
            self.win.withdraw()

    def flash(self, text, ms=3000):
        self.show(text)
        self.job = self.root.after(ms, self.hide)

class Menu:
    """Короткое меню, выезжающее справа."""
    W = 340

    def __init__(self, app):
        self.app = app
        self.win = None

    @property
    def shown(self):
        return self.win is not None

    def toggle(self):
        self.hide() if self.shown else self.show()

    def _slider(self, parent, title, lo, hi, key):
        lbl = tk.Label(parent, text="", fg="white", bg=BG, anchor="w")
        lbl.pack(fill="x", padx=14, pady=(8, 0))

        def upd(v):
            CFG[key] = int(float(v))
            lbl.config(text="%s: %d" % (title, CFG[key]))
        sc = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", showvalue=False, command=upd, bg=BG, fg="white",
                      troughcolor="#444", highlightthickness=0, length=self.W - 28)
        sc.set(int(CFG[key]))
        sc.bind("<ButtonRelease-1>", lambda e: save_settings())
        sc.pack(padx=14)
        upd(CFG[key])

    def show(self):
        if self.win is not None:
            return
        root = self.app.root
        w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=BG)
        hdr = tk.Frame(w, bg=BG)
        hdr.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(hdr, text="Babelo — перевод идёт", fg="white", bg=BG, font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Button(hdr, text="✕", fg="white", bg=BG, bd=0, font=("Segoe UI", 14), activebackground="#333",
                  activeforeground="white", command=self.hide).pack(side="right")
        self._slider(w, "Слушать фразу, с", 2, 15, "phrase_sec")
        self._slider(w, "Пауза конца фразы, мс", 200, 1000, "pause_ms")
        self._slider(w, "Скорость озвучки, %", 70, 150, "speed")
        self._slider(w, "Громкость перевода, %", 10, 100, "volume")
        var = tk.BooleanVar(value=bool(CFG["mute_original"]))
        w._var = var

        def chg():
            CFG["mute_original"] = bool(var.get())
            save_settings()
        tk.Checkbutton(w, text="Заглушать оригинал на время перевода", variable=var, command=chg, fg="white", bg=BG,
                       selectcolor="#333", activebackground=BG, activeforeground="white").pack(anchor="w", padx=10, pady=6)
        for text, cmd in (("Все настройки", self.app.show_main), ("Полное закрытие программы", self.app.full_close)):
            tk.Button(w, text=text, command=cmd, bg="#333", fg="white", activebackground="#444",
                      activeforeground="white", relief="flat", pady=6).pack(fill="x", padx=14, pady=4)
        w.update_idletasks()
        h = w.winfo_reqheight()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        y = max(20, (sh - h) // 2)
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
        self.root.title("Babelo")
        self.root.geometry("640x720")
        self.events = queue.Queue()
        self.engine = None
        self.last_f10 = 0.0
        self.release_job = None
        self.muter = Muter()
        self.muter.recover()
        self.subs = Subtitles(self.root)
        self.menu = Menu(self)
        self.hidden_note = False
        self.log_shown = 0
        self._build_main()
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
            n = len(LOG_LINES)
            fresh = LOG_LINES[self.log_shown:] if n >= self.log_shown else LOG_LINES[:]
            self.log_shown = n
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
        if not u.RegisterHotKey(None, 1, 0x4000, 0x79):          # MOD_NOREPEAT, VK_F10
            self.ui(self.set_status, "Клавиша F10 занята другой программой. Закройте её и запустите Babelo заново.")
            return
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:                             # WM_HOTKEY
                self.ui(self.on_f10)

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
        self.subs.flash("Babelo: перевод включён", 2500)

    def stop(self):
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        self.muter.restore()
        self.menu.hide()
        self.subs.hide()
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
        self.root.deiconify()
        self.root.lift()

    def set_status(self, text):
        self.status.config(text=text)
        log(text)

    def _build_main(self):
        r = self.root
        pad = {"padx": 10, "pady": 4}
        self.status = tk.Label(r, text="Нажмите F10, когда модели скачаны.", anchor="w", justify="left", wraplength=600,
                               font=("Segoe UI", 10, "bold"))
        self.status.pack(fill="x", **pad)
        f = ttk.Frame(r)
        f.pack(fill="x", **pad)
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

        for title, lo, hi, key in (("Слушать фразу не дольше, с", 2, 15, "phrase_sec"),
                                   ("Пауза конца фразы, мс", 200, 1000, "pause_ms"),
                                   ("Скорость озвучки, %", 70, 150, "speed"),
                                   ("Громкость перевода, %", 10, 100, "volume"),
                                   ("Потоки распознавания (больше — быстрее, но тяжелее)", 2, 12, "threads")):
            lab = ttk.Label(r, text="")
            lab.pack(anchor="w", padx=10)

            def mk(lab=lab, title=title, key=key):
                def upd(v):
                    CFG[key] = int(float(v))
                    lab.config(text="%s: %d" % (title, CFG[key]))
                return upd
            sc = ttk.Scale(r, from_=lo, to=hi, orient="horizontal", command=mk())
            sc.set(int(CFG[key]))
            sc.bind("<ButtonRelease-1>", lambda e: save_settings())
            sc.pack(fill="x", padx=10)
            mk()(CFG[key])
        self.v_mute = tk.BooleanVar(value=bool(CFG["mute_original"]))

        def chg():
            CFG["mute_original"] = bool(self.v_mute.get())
            save_settings()
        ttk.Checkbutton(r, text="Заглушать оригинал на время перевода", variable=self.v_mute, command=chg).pack(anchor="w", **pad)

        bar = ttk.Frame(r)
        bar.pack(fill="x", **pad)
        ttk.Button(bar, text="Скачать модели", command=self.download).pack(side="left", padx=4)
        ttk.Button(bar, text="Проверить голос", command=self.test_voice).pack(side="left", padx=4)
        ttk.Button(bar, text="Полное закрытие программы", command=self.full_close).pack(side="right", padx=4)
        self.logbox = tk.Text(r, height=12, state="disabled", bg="#111", fg="#ddd", font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=True, **pad)
        ttk.Label(r, text="F10: включить · ещё раз: меню · два раза подряд: выключить").pack(pady=4)

    def download(self):
        def run():
            try:
                HUB.download(CFG["whisper"], lambda t: self.ui(self.set_status, t))
            except Exception as e:
                self.ui(self.set_status, "Ошибка загрузки: %s (проверьте интернет и нажмите ещё раз)" % e)
                log(traceback.format_exc())
        threading.Thread(target=run, daemon=True).start()

    def test_voice(self):
        def run():
            for mod in ("pythoncom", "comtypes"):
                try:
                    __import__(mod).CoInitialize()
                except Exception:
                    pass
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
