"""
Grammar Checker — System Tray App
Engine: Google Gemini Flash (free) or any OpenRouter model
"""

import tkinter as tk
from tkinter import ttk
import threading
import keyboard
import pyperclip
import json
import os
import sys
import time
import urllib.request
import urllib.error
import ctypes

from PIL import Image, ImageDraw
import pystray

# ── Single Instance Lock ──────────────────────────────────────────────────────
import socket

_lock_socket = None

def ensure_single_instance():
    """Bind a local socket as a mutex. If already bound, another instance is running."""
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        _lock_socket.bind(("127.0.0.1", 47831))  # arbitrary port as lock
        _lock_socket.listen(1)
        return True   # we are the first instance
    except OSError:
        return False  # port taken → another instance is running

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_CONFIG = {
    "provider": "gemini",          # "gemini" | "openrouter"
    "gemini_api_key": "",
    "gemini_model": "gemini-3.1-flash-lite-preview",
    "openrouter_api_key": "",
    "openrouter_model": "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
    "hotkey": "ctrl+shift+g",
    "target_language": "English",
    "autostart": True,
}

COMMON_LANGUAGES = [
    "English", "Spanish", "French", "German", "Italian", "Portuguese",
    "Russian", "Japanese", "Korean", "Chinese (Simplified)", "Chinese (Traditional)",
    "Arabic", "Hindi", "Turkish", "Dutch", "Polish", "Swedish", "Ukrainian",
    "Czech", "Thai", "Vietnamese", "Indonesian", "Greek", "Hebrew",
]

GEMINI_MODEL_SUGGESTIONS = [
    "gemini-3.1-flash-lite-preview",
]

FREE_OPENROUTER_MODELS = [
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free",
    "google/gemini-2.0-flash-exp:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "mistralai/mistral-7b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
]

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            # Fill missing keys with defaults
            for k, v in DEFAULT_CONFIG.items():
                data.setdefault(k, v)
            return data
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def _get_exe_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(__file__)

def _autostart_key_path():
    return r"Software\Microsoft\Windows\CurrentVersion\Run"

def _autostart_name():
    return "GrammarChecker"

def is_autostart_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_key_path(), 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, _autostart_name())
        winreg.CloseKey(key)
        exe = _get_exe_path()
        return val.lower() == exe.lower() or val.lower().startswith(f'"{exe.lower()}"')
    except FileNotFoundError:
        return False
    except Exception:
        return False

def set_autostart(enabled):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_key_path(), 0, winreg.KEY_WRITE)
        if enabled:
            exe = _get_exe_path()
            if " " in exe:
                val = f'"{exe}"'
            else:
                val = exe
            winreg.SetValueEx(key, _autostart_name(), 0, winreg.REG_SZ, val)
        else:
            winreg.DeleteValue(key, _autostart_name())
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

config = load_config()


# ── API Calls ─────────────────────────────────────────────────────────────────
GRAMMAR_PROMPT = """You are a grammar and spelling checker. Analyze the following text and return ONLY a valid JSON object with no extra text, no markdown fences.

JSON format:
{
  "corrected": "<fully corrected text>",
  "issues": [
    {"original": "<wrong phrase>", "fixed": "<corrected phrase>", "explanation": "<why>"}
  ],
  "score": <integer 0-100 how correct the original was>
}

If the text has no issues, return an empty issues array and score 100.

Text to check:
"""

def _post_json(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_with_gemini(text):
    key = config.get("gemini_api_key", "").strip()
    if not key:
        return {"error": "No Gemini API key. Open Settings and add your key.\nGet one free at: aistudio.google.com"}

    model = config.get("gemini_model", "gemini-3.1-flash-lite-preview").strip() or "gemini-2.0-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": GRAMMAR_PROMPT + text}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
    }
    try:
        resp = _post_json(url, headers, body)
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = _strip_fences(raw)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        if e.code == 429:
            return {"error": (
                f"Quota exceeded for model '{model}' (429).\n\n"
                f"Try a different model in Settings, or:\n"
                f"  • Create a new API key at aistudio.google.com\n"
                f"  • Switch to OpenRouter in Settings (truly free)"
            )}
        elif e.code == 404:
            return {"error": f"Model '{model}' not found (404).\nCheck the model name at ai.google.dev/gemini-api/docs/models"}
        return {"error": f"Gemini error {e.code}: {body_text[:200]}"}
    except json.JSONDecodeError:
        return {"corrected": raw, "issues": [], "score": None}
    except Exception as e:
        return {"error": str(e)}


def check_with_openrouter(text):
    key = config.get("openrouter_api_key", "").strip()
    if not key:
        return {"error": "No OpenRouter API key. Open Settings and add your key.\nGet one free at: openrouter.ai"}

    model = config.get("openrouter_model", FREE_OPENROUTER_MODELS[0])
    url = "https://openrouter.ai/api/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": GRAMMAR_PROMPT + text}],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://grammar-checker-app",
        "X-Title": "Grammar Checker",
    }

    try:
        resp = _post_json(url, headers, body)
        raw = resp["choices"][0]["message"]["content"].strip()
        raw = _strip_fences(raw)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        return {"error": f"OpenRouter error {e.code}: {body_text[:200]}"}
    except json.JSONDecodeError:
        return {"corrected": raw, "issues": [], "score": None}
    except Exception as e:
        return {"error": str(e)}


def _strip_fences(text):
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def check_grammar(text):
    if config.get("provider") == "openrouter":
        return check_with_openrouter(text)
    return check_with_gemini(text)


TRANSLATE_PROMPT = """You are a translator. Translate the following text to {target_language}.
If the text is already in {target_language}, just return it unchanged.
Return ONLY a valid JSON object with no extra text, no markdown fences.

JSON format:
{{
  "translated": "<translated text>",
  "detected_language": "<detected source language name>"
}}

Text to translate:
"""


def translate_with_gemini(text, target_lang):
    key = config.get("gemini_api_key", "").strip()
    if not key:
        return {"error": "No Gemini API key. Open Settings and add your key."}

    model = config.get("gemini_model", "gemini-3.1-flash-lite-preview").strip() or "gemini-2.0-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    headers = {"Content-Type": "application/json"}
    prompt = TRANSLATE_PROMPT.format(target_language=target_lang) + text
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
    }
    try:
        resp = _post_json(url, headers, body)
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = _strip_fences(raw)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        return {"error": f"Gemini error {e.code}: {body_text[:200]}"}
    except json.JSONDecodeError:
        return {"translated": raw, "detected_language": "unknown"}
    except Exception as e:
        return {"error": str(e)}


def translate_with_openrouter(text, target_lang):
    key = config.get("openrouter_api_key", "").strip()
    if not key:
        return {"error": "No OpenRouter API key. Open Settings and add your key."}

    model = config.get("openrouter_model", FREE_OPENROUTER_MODELS[0])
    url = "https://openrouter.ai/api/v1/chat/completions"
    prompt = TRANSLATE_PROMPT.format(target_language=target_lang) + text
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://grammar-checker-app",
        "X-Title": "Grammar Checker",
    }
    try:
        resp = _post_json(url, headers, body)
        raw = resp["choices"][0]["message"]["content"].strip()
        raw = _strip_fences(raw)
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        return {"error": f"OpenRouter error {e.code}: {body_text[:200]}"}
    except json.JSONDecodeError:
        return {"translated": raw, "detected_language": "unknown"}
    except Exception as e:
        return {"error": str(e)}


def translate_text(text, target_lang=None):
    target_lang = target_lang or config.get("target_language", "English")
    if config.get("provider") == "openrouter":
        return translate_with_openrouter(text, target_lang)
    return translate_with_gemini(text, target_lang)


# ── Keyboard Layout Fix ────────────────────────────────────────────────────────

_ARABIC_TO_ENGLISH = {
    'ض': 'q', 'ص': 'w', 'ث': 'e', 'ق': 'r', 'ف': 't',
    'غ': 'y', 'ع': 'u', 'ه': 'i', 'خ': 'o', 'ج': 'p',
    'ح': '[', 'د': ']', 'ش': 'a', 'س': 's', 'ي': 'd',
    'ب': 'f', 'ل': 'g', 'ا': 'h', 'أ': 'h', 'إ': 'h', 'آ': 'h',
    'ت': 'j', 'ن': 'k', 'م': 'l', 'ك': ';', 'ط': "'",
    'ئ': 'z', 'ء': 'x', 'ؤ': 'c', 'ر': 'v', 'لا': 'b',
    'ى': 'n', 'ة': 'm', 'و': ',', 'ز': '.', 'ظ': '/',
    'ذ': '`', '،': ',', '؟': '?', '؛': ';',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
    '~': '`', '}': ']', '{': '[', '"': "'", ':': ';',
    '>': '.', '<': ',', 'َ': 'a', 'ُ': 'u', 'ِ': 'i',
}

_ENGLISH_TO_ARABIC = {
    'q': 'ض', 'w': 'ص', 'e': 'ث', 'r': 'ق', 't': 'ف',
    'y': 'غ', 'u': 'ع', 'i': 'ه', 'o': 'خ', 'p': 'ج',
    '[': 'ح', ']': 'د', 'a': 'ش', 's': 'س', 'd': 'ي',
    'f': 'ب', 'g': 'ل', 'h': 'ا', 'j': 'ت', 'k': 'ن',
    'l': 'م', ';': 'ك', "'": 'ط', 'z': 'ئ', 'x': 'ء',
    'c': 'ؤ', 'v': 'ر', 'b': 'لا', 'n': 'ى', 'm': 'ة',
    ',': 'و', '.': 'ز', '/': 'ظ', '`': 'ذ',
    '~': 'ذ', '}': 'د', '{': 'ح', '"': 'ط', ':': 'ك',
}

import re
_ARABIC_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')


def is_arabic_text(text):
    if not text:
        return False
    matches = _ARABIC_RE.findall(text)
    return len(matches) / max(len(text.replace(' ', '')), 1) > 0.3


def is_wrong_layout_english(text):
    if not text or is_arabic_text(text):
        return False
    words = text.split()
    if len(words) < 1:
        return False
    alpha = [ch for ch in text if ch.isalpha()]
    if len(alpha) < 2:
        return False
    mappable = sum(1 for ch in text if ch in _ENGLISH_TO_ARABIC)
    ratio = mappable / max(len([ch for ch in text if not ch.isspace()]), 1)
    vowels = sum(1 for ch in text.lower() if ch in 'aeiou')
    consonants = sum(1 for ch in text.lower() if ch.isalpha() and ch not in 'aeiou')
    vowel_ratio = vowels / max(vowels + consonants, 1)
    return ratio > 0.4 and vowel_ratio < 0.25


def fix_arabic_to_english(text):
    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] in _ARABIC_TO_ENGLISH:
            result.append(_ARABIC_TO_ENGLISH[text[i:i+2]])
            i += 2
        elif text[i] in _ARABIC_TO_ENGLISH:
            result.append(_ARABIC_TO_ENGLISH[text[i]])
            i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def fix_english_to_arabic(text):
    result = []
    for ch in text:
        result.append(_ENGLISH_TO_ARABIC.get(ch, ch))
    return ''.join(result)


# ── Colors ────────────────────────────────────────────────────────────────────
C = {
    "bg":      "#0d0d14",
    "surface": "#14141f",
    "card":    "#1a1a28",
    "border":  "#2a2a40",
    "accent":  "#6366f1",
    "accent2": "#a5b4fc",
    "text":    "#e2e0f0",
    "muted":   "#64607e",
    "green":   "#34d399",
    "yellow":  "#fbbf24",
    "red":     "#f87171",
}


# ── Result Window ──────────────────────────────────────────────────────────────
class ResultWindow:
    def __init__(self, original, result):
        self.original = original
        self.result = result
        self._build()

    def _build(self):
        win = tk.Toplevel()
        win.title("✦ Grammar Check")
        win.configure(bg=C["bg"])
        win.geometry("640x540")
        win.resizable(True, True)
        win.attributes("-topmost", True)
        _center(win, 640, 540)

        # ── Header bar ──
        hdr = tk.Frame(win, bg=C["surface"], pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  ✦  Grammar Check",
                 font=("Georgia", 14, "bold"),
                 bg=C["surface"], fg=C["accent2"]).pack(side="left", padx=16)

        if "error" not in self.result:
            score = self.result.get("score")
            if score is not None:
                sc = C["green"] if score >= 80 else C["yellow"] if score >= 50 else C["red"]
                tk.Label(hdr, text=f"Score  {score}/100",
                         font=("Courier New", 11, "bold"),
                         bg=C["surface"], fg=sc).pack(side="right", padx=20)

        # ── Scrollable body ──
        outer = tk.Frame(win, bg=C["bg"])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=C["bg"])
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        pad = {"padx": 20}

        if "error" in self.result:
            tk.Label(body, text=f"⚠  {self.result['error']}",
                     font=("Segoe UI", 10), bg=C["bg"], fg=C["red"],
                     wraplength=580, justify="left").pack(pady=24, **pad, anchor="w")
        else:
            corrected = self.result.get("corrected", "")
            issues    = self.result.get("issues", [])

            # ── Corrected text ──
            _label(body, "CORRECTED TEXT")
            box = tk.Text(body, height=5, font=("Georgia", 10),
                          bg=C["card"], fg=C["text"], relief="flat",
                          wrap="word", padx=14, pady=10,
                          insertbackground=C["accent"],
                          highlightbackground=C["border"], highlightthickness=1)
            box.pack(fill="x", **pad, pady=(2, 6))
            box.insert("1.0", corrected)
            box.configure(state="disabled")

            def copy_it(btn=None):
                pyperclip.copy(corrected)
                copy_btn.configure(text="✓  Copied!", fg=C["green"])
                win.after(1800, lambda: copy_btn.configure(text="Copy corrected text", fg=C["accent2"]))

            copy_btn = tk.Button(body, text="Copy corrected text",
                                  font=("Segoe UI", 9), bg=C["surface"],
                                  fg=C["accent2"], relief="flat",
                                  padx=14, pady=5, cursor="hand2",
                                  activebackground=C["border"],
                                  command=copy_it)
            copy_btn.pack(anchor="e", padx=22, pady=(0, 18))

            # ── Issues ──
            if issues:
                _label(body, f"ISSUES  ({len(issues)})")
                for issue in issues:
                    _issue_card(body, issue)
            else:
                tk.Frame(body, bg=C["border"], height=1).pack(fill="x", **pad, pady=8)
                tk.Label(body, text="✓  No issues — your text looks great!",
                         font=("Segoe UI", 11), bg=C["bg"], fg=C["green"],
                         pady=10).pack(**pad, anchor="w")

        # ── Footer ──
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        provider = "Gemini Flash" if config.get("provider") == "gemini" else "OpenRouter"
        tk.Label(win, text=f"Engine: {provider}  ·  Press Esc to close",
                 font=("Segoe UI", 8), bg=C["surface"],
                 fg=C["muted"], pady=7).pack(fill="x")

        win.bind("<Escape>", lambda e: win.destroy())
        win.focus_force()


def _label(parent, text):
    tk.Label(parent, text=text, font=("Segoe UI", 8, "bold"),
             bg=C["bg"], fg=C["muted"]).pack(anchor="w", padx=20, pady=(12, 2))

def _issue_card(parent, issue):
    card = tk.Frame(parent, bg=C["card"],
                    highlightbackground=C["border"], highlightthickness=1)
    card.pack(fill="x", padx=20, pady=4)
    inner = tk.Frame(card, bg=C["card"], padx=14, pady=10)
    inner.pack(fill="x")

    row = tk.Frame(inner, bg=C["card"])
    row.pack(fill="x", pady=(0, 4))

    tk.Label(row, text=f'"{issue.get("original","")}"',
             font=("Segoe UI", 10, "bold"), bg=C["card"],
             fg=C["red"], wraplength=240, justify="left").pack(side="left")
    tk.Label(row, text="  →  ", font=("Segoe UI", 10),
             bg=C["card"], fg=C["muted"]).pack(side="left")
    tk.Label(row, text=f'"{issue.get("fixed","")}"',
             font=("Segoe UI", 10, "bold"), bg=C["card"],
             fg=C["green"], wraplength=240, justify="left").pack(side="left")

    tk.Label(inner, text=issue.get("explanation", ""),
             font=("Segoe UI", 9), bg=C["card"], fg=C["muted"],
             wraplength=580, justify="left").pack(anchor="w")


# ── Settings Window ────────────────────────────────────────────────────────────
class SettingsWindow:
    def __init__(self):
        self._build()

    def _build(self):
        win = tk.Toplevel()
        win.title("Grammar Checker — Settings")
        win.configure(bg=C["bg"])
        win.geometry("480x580")
        win.resizable(True, True)
        win.attributes("-topmost", True)
        _center(win, 480, 580)

        tk.Label(win, text="⚙  Settings",
                 font=("Georgia", 14, "bold"),
                 bg=C["bg"], fg=C["accent2"]).pack(pady=(20, 6))
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=20)

        body = tk.Frame(win, bg=C["bg"], padx=28)
        body.pack(fill="both", expand=True, pady=10)

        # ── Provider selector ──
        _label(body, "AI PROVIDER")
        prov_var = tk.StringVar(value=config.get("provider", "gemini"))
        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(2, 14))
        for val, label in [("gemini", "Google Gemini Flash  (recommended, free)"),
                            ("openrouter", "OpenRouter  (many free models)")]:
            tk.Radiobutton(row, text=label, variable=prov_var, value=val,
                           font=("Segoe UI", 9),
                           bg=C["bg"], fg=C["text"],
                           selectcolor=C["surface"],
                           activebackground=C["bg"]).pack(anchor="w")

        # ── Gemini key ──
        _label(body, "GEMINI API KEY  (aistudio.google.com → free)")
        gem_var = tk.StringVar(value=config.get("gemini_api_key", ""))
        gem_entry = _key_entry(body, gem_var)

        # ── Gemini model ──
        _label(body, "GEMINI MODEL  (type any model name or pick one)")
        gmodel_var = tk.StringVar(value=config.get("gemini_model", "gemini-3.1-flash-lite-preview"))
        gmodel_cb = ttk.Combobox(body, textvariable=gmodel_var,
                                  values=GEMINI_MODEL_SUGGESTIONS,
                                  font=("Courier New", 9))
        gmodel_cb.pack(fill="x", pady=(2, 4))
        tk.Label(body, text="Browse all models → ai.google.dev/gemini-api/docs/models",
                 font=("Segoe UI", 8), bg=C["bg"], fg=C["muted"],
                 cursor="hand2").pack(anchor="w", pady=(0, 10))

        # ── OpenRouter key ──
        _label(body, "OPENROUTER API KEY  (openrouter.ai → free)")
        or_var = tk.StringVar(value=config.get("openrouter_api_key", ""))
        _key_entry(body, or_var)

        # ── OpenRouter model ──
        _label(body, "OPENROUTER MODEL")
        model_var = tk.StringVar(value=config.get("openrouter_model", FREE_OPENROUTER_MODELS[0]))
        model_cb = ttk.Combobox(body, textvariable=model_var,
                                 values=FREE_OPENROUTER_MODELS,
                                 font=("Courier New", 9), state="readonly")
        model_cb.pack(fill="x", pady=(2, 10))

        # ── Hotkey ──
        _label(body, "HOTKEY")
        hk_var = tk.StringVar(value=config.get("hotkey", "ctrl+shift+g"))
        tk.Entry(body, textvariable=hk_var, font=("Courier New", 10),
                  bg=C["surface"], fg=C["text"], relief="flat",
                  insertbackground=C["accent"],
                  highlightbackground=C["border"], highlightthickness=1).pack(fill="x", ipady=5, pady=(2, 14))

        # ── Target language ──
        _label(body, "TRANSLATE TO  (target language)")
        lang_var = tk.StringVar(value=config.get("target_language", "English"))
        lang_cb = ttk.Combobox(body, textvariable=lang_var,
                                values=COMMON_LANGUAGES,
                                font=("Segoe UI", 9))
        lang_cb.pack(fill="x", pady=(2, 14))

        # ── Auto-start ──
        _label(body, "START WITH WINDOWS")
        autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        tk.Checkbutton(body, text="  Launch automatically on startup",
                        variable=autostart_var,
                        font=("Segoe UI", 9),
                        bg=C["bg"], fg=C["text"],
                        selectcolor=C["surface"],
                        activebackground=C["bg"]).pack(anchor="w", pady=(2, 14))

        def save():
            config["provider"]            = prov_var.get()
            config["gemini_api_key"]      = gem_var.get().strip()
            config["gemini_model"]        = gmodel_var.get().strip()
            config["openrouter_api_key"]  = or_var.get().strip()
            config["openrouter_model"]    = model_var.get().strip()
            config["hotkey"]              = hk_var.get().strip()
            config["target_language"]     = lang_var.get().strip()
            config["autostart"]           = autostart_var.get()
            set_autostart(autostart_var.get())
            save_config(config)
            # Re-register hotkey
            try:
                keyboard.unhook_all_hotkeys()
                keyboard.add_hotkey(config["hotkey"], on_hotkey)
            except Exception:
                pass
            win.destroy()

        tk.Button(body, text="  Save  ", font=("Segoe UI", 10, "bold"),
                  bg=C["accent"], fg="white", relief="flat",
                  padx=20, pady=7, cursor="hand2",
                  activebackground=C["accent2"], command=save).pack(pady=6)

        win.bind("<Escape>", lambda e: win.destroy())
        win.focus_force()


def _key_entry(parent, var):
    frame = tk.Frame(parent, bg=C["bg"])
    frame.pack(fill="x", pady=(2, 10))
    entry = tk.Entry(frame, textvariable=var, show="•",
                      font=("Courier New", 10), bg=C["surface"],
                      fg=C["text"], relief="flat",
                      insertbackground=C["accent"],
                      highlightbackground=C["border"], highlightthickness=1)
    entry.pack(side="left", fill="x", expand=True, ipady=5)
    tk.Button(frame, text="👁", font=("Segoe UI", 9), bg=C["surface"],
              fg=C["muted"], relief="flat", cursor="hand2",
              command=lambda: entry.configure(
                  show="" if entry.cget("show") == "•" else "•")
              ).pack(side="left", padx=(4, 0))
    return var


# ── Loading Popup ──────────────────────────────────────────────────────────────
class LoadingWindow:
    def __init__(self):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.configure(bg=C["surface"])
        self.win.geometry("260x70")
        self.win.attributes("-topmost", True)
        _center(self.win, 260, 70)

        tk.Label(self.win, text="✦  Checking grammar…",
                 font=("Georgia", 11), bg=C["surface"],
                 fg=C["accent2"]).pack(expand=True)
        self.win.update()

    def destroy(self):
        try: self.win.destroy()
        except Exception: pass


# ── Helpers ───────────────────────────────────────────────────────────────────
def _center(win, w, h):
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")


# ── Hotkey handler ─────────────────────────────────────────────────────────────
root_tk = None

def get_root():
    global root_tk
    if root_tk is None:
        root_tk = tk.Tk()
        root_tk.withdraw()
    return root_tk

# ── Floating Bubble ───────────────────────────────────────────────────────────

_active_bubble = None

def show_bubble(original, result):
    global _active_bubble
    if _active_bubble:
        try: _active_bubble.destroy()
        except: pass
    _active_bubble = GrammarBubble(original, result)


class GrammarBubble:
    """Animated floating bubble that appears near the cursor."""

    ANIM_STEPS = 12
    ANIM_MS    = 12   # ms per frame → ~144fps feel

    def __init__(self, original, result):
        self.original  = original
        self.result    = result
        self.corrected = result.get("corrected", original) if "error" not in result else None
        self.issues    = result.get("issues", []) if "error" not in result else []
        self.score     = result.get("score", 100) if "error" not in result else None
        self._build()

    def _build(self):
        c = C
        self._prev_window = ctypes.windll.user32.GetForegroundWindow()
        # ── Get cursor position ──
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = pt.x, pt.y

        win = tk.Toplevel()
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=c["bg"])

        # ── Rounded look via padding + border ──
        outer = tk.Frame(win, bg=c["accent"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=c["bg"], padx=0, pady=0)
        inner.pack(fill="both", expand=True)

        if "error" in self.result:
            self._build_error(inner)
        elif not self.issues:
            self._build_clean(inner)
        else:
            self._build_issues(inner)

        # ── Position above cursor ──
        win.update_idletasks()
        W = win.winfo_reqwidth()
        H = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()

        # Default: above cursor, centered
        x = cx - W // 2
        y = cy - H - 18

        # Keep on screen
        x = max(8, min(x, sw - W - 8))
        y = max(8, min(y, sh - H - 8))

        self._target_y = y
        self._start_y  = y + 18   # slides up from below

        win.geometry(f"{W}x{H}+{x}+{self._start_y}")
        win.deiconify()

        # ── Animate in ──
        self._step = 0
        self._animate_in()

        # ── Click outside to close ──
        win.bind("<FocusOut>", lambda e: self._animate_out())
        win.bind("<Escape>",   lambda e: self._animate_out())
        win.after(100, win.focus_force)

        # Auto-dismiss after 8s if no interaction
        self._auto_id = win.after(8000, self._animate_out)

    def _build_error(self, parent):
        c = C
        f = tk.Frame(parent, bg=c["bg"], padx=16, pady=12)
        f.pack()
        tk.Label(f, text="⚠  " + self.result["error"][:120],
                 font=("Segoe UI", 9), bg=c["bg"], fg=c["red"],
                 wraplength=340, justify="left").pack()
        self._close_btn(f)

    def _build_clean(self, parent):
        c = C
        f = tk.Frame(parent, bg=c["bg"], padx=18, pady=12)
        f.pack()
        tk.Label(f, text="✓  Looks perfect!",
                 font=("Segoe UI", 11, "bold"), bg=c["bg"], fg=c["green"]).pack(side="left")

        def translate_clean():
            try: self.win.destroy()
            except: pass
            loading = LoadingWindow()
            loading.win.children[list(loading.win.children.keys())[0]].configure(text="✦  Translating…")
            def do_translate():
                result = translate_text(self.original)
                get_root().after(0, lambda: (loading.destroy(),
                    show_translation_bubble(self.original, result)))
            threading.Thread(target=do_translate, daemon=True).start()

        tk.Button(f, text="🌐  Translate",
                  font=("Segoe UI", 9), bg=c["bg"], fg=c["accent2"],
                  relief="flat", padx=10, pady=3, cursor="hand2",
                  activebackground=c["border"],
                  command=translate_clean).pack(side="left", padx=(12, 0))
        self._close_btn(f)

    def _build_issues(self, parent):
        c = C
        score = self.score

        # ── Header row ──
        hdr = tk.Frame(parent, bg=c["surface"], padx=14, pady=8)
        hdr.pack(fill="x")

        sc_color = c["green"] if score and score >= 80 else c["yellow"] if score and score >= 50 else c["red"]
        tk.Label(hdr, text="✦  Grammar Check",
                 font=("Georgia", 10, "bold"), bg=c["surface"],
                 fg=c["accent2"]).pack(side="left")
        if score is not None:
            tk.Label(hdr, text=f"{score}/100",
                     font=("Courier New", 10, "bold"), bg=c["surface"],
                     fg=sc_color).pack(side="right", padx=(8, 0))

        btn_row = tk.Frame(hdr, bg=c["surface"])
        btn_row.pack(side="right")

        # ✕ close
        tk.Button(btn_row, text="✕", font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  cursor="hand2", padx=4,
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

        # ── Corrected preview ──
        body = tk.Frame(parent, bg=c["bg"], padx=14, pady=10)
        body.pack(fill="x")

        tk.Label(body, text=self.corrected,
                 font=("Georgia", 10), bg=c["bg"], fg=c["text"],
                 wraplength=360, justify="left").pack(anchor="w")

        # ── Issues (max 3 shown) ──
        shown = self.issues[:3]
        for issue in shown:
            row = tk.Frame(body, bg=c["card"],
                           highlightbackground=c["border"], highlightthickness=1)
            row.pack(fill="x", pady=3)
            rinner = tk.Frame(row, bg=c["card"], padx=10, pady=5)
            rinner.pack(fill="x")

            tk.Label(rinner, text=f'"{issue.get("original","")}"',
                     font=("Segoe UI", 9, "bold"), bg=c["card"],
                     fg=c["red"]).pack(side="left")
            tk.Label(rinner, text=" → ",
                     font=("Segoe UI", 9), bg=c["card"],
                     fg=c["muted"]).pack(side="left")
            tk.Label(rinner, text=f'"{issue.get("fixed","")}"',
                     font=("Segoe UI", 9, "bold"), bg=c["card"],
                     fg=c["green"]).pack(side="left")
            tk.Label(rinner, text=f'  {issue.get("explanation","")}',
                     font=("Segoe UI", 8), bg=c["card"],
                     fg=c["muted"], wraplength=240).pack(side="left")

        if len(self.issues) > 3:
            tk.Label(body, text=f"+ {len(self.issues)-3} more issues",
                     font=("Segoe UI", 8), bg=c["bg"],
                     fg=c["muted"]).pack(anchor="w", pady=(4,0))

        # ── Action buttons ──
        acts = tk.Frame(parent, bg=c["surface"], padx=14, pady=8)
        acts.pack(fill="x")

        def apply_fix():
            prev = self._prev_window
            try: self.win.destroy()
            except: pass
            def do_paste():
                pyperclip.copy(self.corrected)
                time.sleep(0.25)
                ctypes.windll.user32.SetForegroundWindow(prev)
                time.sleep(0.1)
                keyboard.send("ctrl+v")
            threading.Thread(target=do_paste, daemon=True).start()

        tk.Button(acts, text="✓  Apply fix",
                   font=("Segoe UI", 9, "bold"),
                   bg=c["accent"], fg="white", relief="flat",
                   padx=14, pady=5, cursor="hand2",
                   activebackground=c["accent2"],
                   command=apply_fix).pack(side="left")

        def translate_text_action():
            try: self.win.destroy()
            except: pass
            loading = LoadingWindow()
            loading.win.children[list(loading.win.children.keys())[0]].configure(text="✦  Translating…")
            def do_translate():
                result = translate_text(self.corrected or self.original)
                get_root().after(0, lambda: (loading.destroy(),
                    show_translation_bubble(self.original, result)))
            threading.Thread(target=do_translate, daemon=True).start()

        tk.Button(acts, text="🌐  Translate",
                   font=("Segoe UI", 9),
                   bg=c["surface"], fg=c["accent2"], relief="flat",
                   padx=10, pady=5, cursor="hand2",
                   activebackground=c["border"],
                   command=translate_text_action).pack(side="left", padx=(6, 0))

        def fix_layout():
            try: self.win.destroy()
            except: pass
            text_to_fix = self.corrected or self.original
            if is_arabic_text(text_to_fix):
                fixed = fix_arabic_to_english(text_to_fix)
            else:
                fixed = fix_english_to_arabic(text_to_fix)
            get_root().after(100, lambda: show_layout_bubble(text_to_fix, fixed))

        tk.Button(acts, text="⌨  Fix Layout",
                   font=("Segoe UI", 9),
                   bg=c["surface"], fg=c["accent2"], relief="flat",
                   padx=10, pady=5, cursor="hand2",
                   activebackground=c["border"],
                   command=fix_layout).pack(side="left", padx=(6, 0))

        def open_full():
            self._animate_out()
            get_root().after(200, lambda: ResultWindow(self.original, self.result))

        tk.Button(acts, text="Details",
                  font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=c["border"],
                  command=open_full).pack(side="left", padx=(6, 0))

        tk.Button(acts, text="Dismiss",
                  font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

    def _close_btn(self, parent):
        tk.Button(parent, text="  ✕", font=("Segoe UI", 9),
                  bg=C["bg"], fg=C["muted"], relief="flat",
                  cursor="hand2", command=self._animate_out).pack(side="left", padx=(12, 0))

    # ── Animation helpers ──────────────────────────────────────────────────────
    def _ease(self, t):
        """Ease out cubic."""
        return 1 - (1 - t) ** 3

    def _animate_in(self):
        if self._step > self.ANIM_STEPS:
            try:
                self.win.attributes("-alpha", 1.0)
                x = self.win.winfo_x()
                self.win.geometry(f"+{x}+{self._target_y}")
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        alpha = t
        y = int(self._start_y + (self._target_y - self._start_y) * t)
        try:
            self.win.attributes("-alpha", alpha)
            x = self.win.winfo_x()
            self.win.geometry(f"+{x}+{y}")
        except: return
        self._step += 1
        self.win.after(self.ANIM_MS, self._animate_in)

    def _animate_out(self):
        try:
            if self._auto_id:
                self.win.after_cancel(self._auto_id)
        except: pass
        self._step = self.ANIM_STEPS
        self._fade_out()

    def _fade_out(self):
        if self._step < 0:
            try: self.win.destroy()
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        try:
            self.win.attributes("-alpha", t)
            x = self.win.winfo_x()
            y = self.win.winfo_y()
            self.win.geometry(f"+{x}+{y + 2}")
        except: return
        self._step -= 1
        self.win.after(self.ANIM_MS, self._fade_out)

    def destroy(self):
        try: self.win.destroy()
        except: pass


_active_trans_bubble = None

def show_translation_bubble(original, result):
    global _active_trans_bubble
    if _active_trans_bubble:
        try: _active_trans_bubble.destroy()
        except: pass
    _active_trans_bubble = TranslationBubble(original, result)


class TranslationBubble:
    ANIM_STEPS = 12
    ANIM_MS = 12

    def __init__(self, original, result):
        self.original = original
        self.result = result
        self.translated = result.get("translated", original) if "error" not in result else None
        self.detected = result.get("detected_language", "") if "error" not in result else ""
        self.target_lang = config.get("target_language", "English")
        self._build()

    def _build(self):
        c = C
        self._prev_window = ctypes.windll.user32.GetForegroundWindow()
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = pt.x, pt.y

        win = tk.Toplevel()
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=c["bg"])

        outer = tk.Frame(win, bg=c["accent"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=c["bg"], padx=0, pady=0)
        inner.pack(fill="both", expand=True)

        if "error" in self.result:
            self._build_error(inner)
        else:
            self._build_result(inner)

        win.update_idletasks()
        W = win.winfo_reqwidth()
        H = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(8, min(cx - W // 2, sw - W - 8))
        y = max(8, min(cy - H - 18, sh - H - 8))
        self._target_y = y
        self._start_y = y + 18
        win.geometry(f"{W}x{H}+{x}+{self._start_y}")
        win.deiconify()

        self._step = 0
        self._animate_in()
        win.bind("<FocusOut>", lambda e: self._animate_out())
        win.bind("<Escape>", lambda e: self._animate_out())
        win.after(100, win.focus_force)
        self._auto_id = win.after(8000, self._animate_out)

    def _build_error(self, parent):
        c = C
        f = tk.Frame(parent, bg=c["bg"], padx=16, pady=12)
        f.pack()
        tk.Label(f, text="⚠  " + self.result["error"][:120],
                 font=("Segoe UI", 9), bg=c["bg"], fg=c["red"],
                 wraplength=360, justify="left").pack()
        tk.Button(f, text="  ✕", font=("Segoe UI", 9),
                  bg=c["bg"], fg=c["muted"], relief="flat",
                  cursor="hand2", command=self._animate_out).pack(pady=(8, 0))

    def _build_result(self, parent):
        c = C

        hdr = tk.Frame(parent, bg=c["surface"], padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"🌐  → {self.target_lang}",
                 font=("Georgia", 10, "bold"), bg=c["surface"],
                 fg=c["accent2"]).pack(side="left")
        if self.detected:
            tk.Label(hdr, text=f"from {self.detected}",
                      font=("Segoe UI", 8), bg=c["surface"],
                      fg=c["muted"]).pack(side="left", padx=(8, 0))
        tk.Button(hdr, text="✕", font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  cursor="hand2", padx=4,
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

        body = tk.Frame(parent, bg=c["bg"], padx=14, pady=10)
        body.pack(fill="x")
        tk.Label(body, text=self.translated,
                 font=("Georgia", 10), bg=c["bg"], fg=c["text"],
                 wraplength=360, justify="left").pack(anchor="w")

        acts = tk.Frame(parent, bg=c["surface"], padx=14, pady=8)
        acts.pack(fill="x")

        def copy_trans():
            pyperclip.copy(self.translated)
            copy_btn.configure(text="✓  Copied!", fg=c["green"])
            win.after(1800, lambda: copy_btn.configure(text="Copy", fg=c["accent2"]))

        copy_btn = tk.Button(acts, text="Copy",
                              font=("Segoe UI", 9),
                              bg=c["surface"], fg=c["accent2"], relief="flat",
                              padx=10, pady=5, cursor="hand2",
                              activebackground=c["border"],
                              command=copy_trans)
        copy_btn.pack(side="left")

        def apply_trans():
            prev = self._prev_window
            try: self.win.destroy()
            except: pass
            def do_paste():
                pyperclip.copy(self.translated)
                time.sleep(0.25)
                ctypes.windll.user32.SetForegroundWindow(prev)
                time.sleep(0.1)
                keyboard.send("ctrl+v")
            threading.Thread(target=do_paste, daemon=True).start()

        tk.Button(acts, text="✓  Apply",
                  font=("Segoe UI", 9, "bold"),
                  bg=c["accent"], fg="white", relief="flat",
                  padx=14, pady=5, cursor="hand2",
                  activebackground=c["accent2"],
                  command=apply_trans).pack(side="left", padx=(6, 0))

        tk.Button(acts, text="Dismiss",
                  font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

    def _ease(self, t):
        return 1 - (1 - t) ** 3

    def _animate_in(self):
        if self._step > self.ANIM_STEPS:
            try:
                self.win.attributes("-alpha", 1.0)
                x = self.win.winfo_x()
                self.win.geometry(f"+{x}+{self._target_y}")
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        try:
            self.win.attributes("-alpha", t)
            x = self.win.winfo_x()
            y = int(self._start_y + (self._target_y - self._start_y) * t)
            self.win.geometry(f"+{x}+{y}")
        except: return
        self._step += 1
        self.win.after(self.ANIM_MS, self._animate_in)

    def _animate_out(self):
        try:
            if self._auto_id:
                self.win.after_cancel(self._auto_id)
        except: pass
        self._step = self.ANIM_STEPS
        self._fade_out()

    def _fade_out(self):
        if self._step < 0:
            try: self.win.destroy()
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        try:
            self.win.attributes("-alpha", t)
            x = self.win.winfo_x()
            y = self.win.winfo_y()
            self.win.geometry(f"+{x}+{y + 2}")
        except: return
        self._step -= 1
        self.win.after(self.ANIM_MS, self._fade_out)

    def destroy(self):
        try: self.win.destroy()
        except: pass


_active_layout_bubble = None

def show_layout_bubble(original, fixed):
    global _active_layout_bubble
    if _active_layout_bubble:
        try: _active_layout_bubble.destroy()
        except: pass
    _active_layout_bubble = LayoutFixBubble(original, fixed)


class LayoutFixBubble:
    ANIM_STEPS = 12
    ANIM_MS = 12

    def __init__(self, original, fixed):
        self.original = original
        self.fixed = fixed
        self._build()

    def _build(self):
        c = C
        self._prev_window = ctypes.windll.user32.GetForegroundWindow()
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = pt.x, pt.y

        win = tk.Toplevel()
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=c["bg"])

        outer = tk.Frame(win, bg=c["accent"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=c["bg"], padx=0, pady=0)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=c["surface"], padx=14, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⌨  Layout Fix",
                 font=("Georgia", 10, "bold"), bg=c["surface"],
                 fg=c["accent2"]).pack(side="left")
        tk.Button(hdr, text="✕", font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  cursor="hand2", padx=4,
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

        body = tk.Frame(inner, bg=c["bg"], padx=14, pady=10)
        body.pack(fill="x")

        tk.Label(body, text=self.fixed,
                 font=("Georgia", 10), bg=c["bg"], fg=c["text"],
                 wraplength=360, justify="left").pack(anchor="w")

        acts = tk.Frame(inner, bg=c["surface"], padx=14, pady=8)
        acts.pack(fill="x")

        def apply_fix():
            prev = self._prev_window
            try: self.win.destroy()
            except: pass
            def do_paste():
                pyperclip.copy(self.fixed)
                time.sleep(0.25)
                ctypes.windll.user32.SetForegroundWindow(prev)
                time.sleep(0.1)
                keyboard.send("ctrl+v")
            threading.Thread(target=do_paste, daemon=True).start()

        tk.Button(acts, text="✓  Apply",
                  font=("Segoe UI", 9, "bold"),
                  bg=c["accent"], fg="white", relief="flat",
                  padx=14, pady=5, cursor="hand2",
                  activebackground=c["accent2"],
                  command=apply_fix).pack(side="left")

        def copy_fix():
            pyperclip.copy(self.fixed)
            copy_btn.configure(text="✓  Copied!", fg=c["green"])
            win.after(1800, lambda: copy_btn.configure(text="Copy", fg=c["accent2"]))

        copy_btn = tk.Button(acts, text="Copy",
                              font=("Segoe UI", 9),
                              bg=c["surface"], fg=c["accent2"], relief="flat",
                              padx=10, pady=5, cursor="hand2",
                              activebackground=c["border"],
                              command=copy_fix)
        copy_btn.pack(side="left", padx=(6, 0))

        def fix_grammar():
            try: self.win.destroy()
            except: pass
            loading = LoadingWindow()
            def do_check():
                result = check_grammar(self.fixed)
                get_root().after(0, lambda: (loading.destroy(),
                    show_bubble(self.fixed, result)))
            threading.Thread(target=do_check, daemon=True).start()

        tk.Button(acts, text="✦  Grammar Check",
                  font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["accent2"], relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=c["border"],
                  command=fix_grammar).pack(side="left", padx=(6, 0))

        tk.Button(acts, text="Dismiss",
                  font=("Segoe UI", 9),
                  bg=c["surface"], fg=c["muted"], relief="flat",
                  padx=10, pady=5, cursor="hand2",
                  activebackground=c["border"],
                  command=self._animate_out).pack(side="right")

        win.update_idletasks()
        W = win.winfo_reqwidth()
        H = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(8, min(cx - W // 2, sw - W - 8))
        y = max(8, min(cy - H - 18, sh - H - 8))
        self._target_y = y
        self._start_y = y + 18
        win.geometry(f"{W}x{H}+{x}+{self._start_y}")
        win.deiconify()

        self._step = 0
        self._animate_in()
        win.bind("<FocusOut>", lambda e: self._animate_out())
        win.bind("<Escape>", lambda e: self._animate_out())
        win.after(100, win.focus_force)
        self._auto_id = win.after(8000, self._animate_out)

    def _ease(self, t):
        return 1 - (1 - t) ** 3

    def _animate_in(self):
        if self._step > self.ANIM_STEPS:
            try:
                self.win.attributes("-alpha", 1.0)
                x = self.win.winfo_x()
                self.win.geometry(f"+{x}+{self._target_y}")
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        try:
            self.win.attributes("-alpha", t)
            x = self.win.winfo_x()
            y = int(self._start_y + (self._target_y - self._start_y) * t)
            self.win.geometry(f"+{x}+{y}")
        except: return
        self._step += 1
        self.win.after(self.ANIM_MS, self._animate_in)

    def _animate_out(self):
        try:
            if self._auto_id:
                self.win.after_cancel(self._auto_id)
        except: pass
        self._step = self.ANIM_STEPS
        self._fade_out()

    def _fade_out(self):
        if self._step < 0:
            try: self.win.destroy()
            except: pass
            return
        t = self._ease(self._step / self.ANIM_STEPS)
        try:
            self.win.attributes("-alpha", t)
            x = self.win.winfo_x()
            y = self.win.winfo_y()
            self.win.geometry(f"+{x}+{y + 2}")
        except: return
        self._step -= 1
        self.win.after(self.ANIM_MS, self._fade_out)

    def destroy(self):
        try: self.win.destroy()
        except: pass


def on_hotkey():
    time.sleep(0.15)
    text = pyperclip.paste().strip()
    if not text:
        return

    root = get_root()

    def run():
        loading = LoadingWindow()

        def do_check():
            result = check_grammar(text)
            root.after(0, lambda: (loading.destroy(), show_bubble(text, result)))

        threading.Thread(target=do_check, daemon=True).start()

    root.after(0, run)


# ── Tray ───────────────────────────────────────────────────────────────────────
def make_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill="#6366f1")
    # Simple G letter
    d.rectangle([22, 28, 40, 36], fill="white")
    d.rectangle([32, 28, 40, 44], fill="white")
    d.arc([14, 14, 50, 50], start=45, end=315, fill="white", width=5)
    return img

tray_icon = None

def open_settings(icon=None, item=None):
    get_root().after(0, SettingsWindow)

def quit_app(icon=None, item=None):
    if tray_icon: tray_icon.stop()
    sys.exit(0)

def start_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem("✦  Grammar Checker", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings", open_settings),
        pystray.MenuItem("Quit", quit_app),
    )
    tray_icon = pystray.Icon("grammar", make_icon(), "Grammar Checker", menu)
    tray_icon.run()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if not ensure_single_instance():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "Grammar Checker is already running!\n\nCheck your system tray (bottom-right corner).",
            "Already Running",
            0x40  # MB_ICONINFORMATION
        )
        sys.exit(0)

    keyboard.add_hotkey(config.get("hotkey", "ctrl+shift+g"), on_hotkey)
    threading.Thread(target=start_tray, daemon=True).start()

    if config.get("autostart", True):
        set_autostart(True)

    root = get_root()
    # Open settings on first run
    has_key = config.get("gemini_api_key") or config.get("openrouter_api_key")
    if not has_key:
        root.after(600, SettingsWindow)

    root.mainloop()


if __name__ == "__main__":
    main()
