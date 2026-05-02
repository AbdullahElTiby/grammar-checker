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


# ── Keyboard Layout Fix (Google-Style Multi-Variant) ──────────────────────────

import re
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_ARABIC_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')
_LATIN_RE = re.compile(r'[a-zA-Z]')

# ── Common English words for dictionary scoring ──
_COMMON_WORDS = frozenset([
    'the','be','to','of','and','a','in','that','have','i','it','for','not','on','with','he','as','you','do','at',
    'this','but','his','by','from','they','we','say','her','she','or','an','will','my','one','all','would','there',
    'their','what','so','up','out','if','about','who','get','which','go','me','when','make','can','like','time','no',
    'just','him','know','take','people','into','year','your','good','some','could','them','see','other','than','then',
    'now','look','only','come','its','over','think','also','back','after','use','two','how','our','work','first','well',
    'way','even','new','want','because','any','these','give','day','most','us','is','was','are','were','has','had','have',
    'did','does','can','could','would','should','may','might','must','shall','will','let','get','got','gotten',
    'say','said','says','make','made','makes','take','took','taken','takes','come','came','comes','coming','know','knew',
    'known','knows','knowing','see','saw','seen','sees','seeing','look','looked','looks','looking','use','used','uses',
    'using','find','found','finds','finding','give','gave','given','gives','giving','tell','told','tells','telling',
    'work','worked','works','working','call','called','calls','calling','try','tried','tries','trying','need','needed',
    'needs','needing','feel','felt','feels','feeling','become','became','becomes','becoming','leave','left','leaves',
    'leaving','put','puts','putting','mean','meant','means','meaning','keep','kept','keeps','keeping','begin','began',
    'begun','begins','beginning','seem','seemed','seems','seeming','help','helped','helps','helping','show','showed',
    'shown','shows','showing','hear','heard','hears','hearing','play','played','plays','playing','run','ran','runs',
    'running','move','moved','moves','moving','live','lived','lives','living','believe','believed','believes','believing',
    'hold','held','holds','holding','bring','brought','brings','bringing','happen','happened','happens','happening',
    'write','wrote','written','writes','writing','provide','provided','provides','providing','sit','sat','sits','sitting',
    'stand','stood','stands','standing','lose','lost','loses','losing','pay','paid','pays','paying','meet','met','meets',
    'meeting','include','included','includes','including','continue','continued','continues','continuing','set','sets',
    'setting','learn','learned','learns','learning','change','changed','changes','changing','lead','led','leads','leading',
    'understand','understood','understands','understanding','watch','watched','watches','watching','follow','followed',
    'follows','following','stop','stopped','stops','stopping','create','created','creates','creating','speak','spoke',
    'spoken','speaks','speaking','read','reads','reading','allow','allowed','allows','allowing','add','added','adds',
    'adding','spend','spent','spends','spending','grow','grew','grown','grows','growing','open','opened','opens','opening',
    'walk','walked','walks','walking','win','won','wins','winning','offer','offered','offers','offering','remember',
    'remembered','remembers','remembering','love','loved','loves','loving','consider','considered','considers','considering',
    'appear','appeared','appears','appearing','buy','bought','buys','buying','wait','waited','waits','waiting','serve',
    'served','serves','serving','die','died','dies','dying','send','sent','sends','sending','expect','expected','expects',
    'expecting','build','built','builds','building','stay','stayed','stays','staying','fall','fell','fallen','falls',
    'falling','cut','cuts','cutting','reach','reached','reaches','reaching','kill','killed','kills','killing','remain',
    'remained','remains','remaining','suggest','suggested','suggests','suggesting','raise','raised','raises','raising',
    'pass','passed','passes','passing','sell','sold','sells','selling','require','required','requires','requiring','report',
    'reported','reports','reporting','decide','decided','decides','deciding','pull','pulled','pulls','pulling',
    'youtube','google','facebook','instagram','twitter','whatsapp','telegram','tiktok','snapchat','linkedin','reddit',
    'pinterest','netflix','spotify','amazon','apple','microsoft','samsung','iphone','android','windows','linux','chrome',
    'firefox','edge','safari','gmail','outlook','yahoo','bing','zoom','teams','slack','discord','skype','hello','world',
    'john','jane','mike','sarah','david','emma','alex','chris','james','mary','robert','linda','michael','jennifer',
    'william','patricia','richard','elizabeth','joseph','susan','thomas','jessica','charles','daniel','karen','matthew',
    'nancy','anthony','lisa','mark','betty','donald','helen','steven','sandra','paul','donna','andrew','carol','joshua',
    'ruth','kenneth','sharon','kevin','michelle','brian','emily','george','amanda','edward','melissa','ronald','deborah',
    'timothy','stephanie','jason','rebecca','jeffrey','laura','ryan','shirley','jacob','cynthia','gary','kathleen',
    'nicholas','anna','stephen','brenda','larry','pamela','justin','scott','nicole','brandon','samuel','katherine',
    'benjamin','christine','gregory','debra','frank','rachel','alexander','catherine','raymond','carolyn','patrick',
    'janet','jack','dennis','maria','jerry','heather','tyler','diane','aaron','virginia','jose','julie','adam','joyce',
    'henry','victoria','nathan','olivia','douglas','kelly','zachary','christina','peter','lauren','kyle','joan','walter',
    'evelyn','ethan','judith','jeremy','megan','harold','cheryl','keith','andrea','christian','hannah','roger','martha',
    'noah','jacqueline','gerald','frances','carl','gloria','terry','ann','sean','teresa','austin','kathryn','arthur',
    'sara','lawrence','janice','jesse','jean','dylan','alice','bryan','madison','joe','doris','jordan','abigail','billy',
    'julia','bruce','judy','albert','grace','willie','denise','gabriel','amber','logan','marilyn','alan','beverly','juan',
    'danielle','wayne','theresa','elijah','sophia','randy','marie','roy','diana','vincent','brittany','ralph','natalie',
    'eugene','isabella','russell','charlotte','bobby','rose','mason','alexis','philip','kayla','louis','hi','ok','bye',
    'thanks','please','sorry','yes','no','maybe','sure','okay','hey','wow','ouch','yay','oops','aha','hmm','uhh','huh',
    'time','person','year','way','day','thing','man','world','life','hand','part','child','eye','woman','place','week',
    'case','point','government','company','number','group','problem','fact','good','new','first','last','long','great',
    'little','own','other','old','right','big','high','different','small','large','next','early','young','important','few',
    'public','bad','same','able','to','of','in','for','on','with','as','at','by','from','up','about','into','over','after',
    'beneath','under','above','out','off','away','down','through','during','before','between','among','within','without',
    'against','toward','until','while','although','because','since','unless','whether','either','neither','both','each',
    'every','many','much','more','most','several','various','certain','such','only','too','very','just','now','then','here',
    'there','when','where','why','how','what','which','who','whom','whose','this','that','these','those','mine','myself',
    'yourself','himself','herself','itself','ourselves','themselves','whatever','whoever','whomever','whichever','anything',
    'something','nothing','everything','someone','anyone','everyone','noone','somebody','anybody','everybody','nobody',
    'another','others','enough','half','quarter','double','twice','once','zero','one','two','three','four','five','six',
    'seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen',
    'nineteen','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety','hundred','thousand','million',
    'billion','first','second','third','fourth','fifth','sixth','seventh','eighth','ninth','tenth'
])

# ── Common Arabic phrase shortcuts ──
_COMMON_ARABIC_FIXES = {
    'هلو': 'hello', 'هاي': 'hi', 'باي': 'bye', 'ثانكس': 'thanks',
    'بليز': 'please', 'سوري': 'sorry', 'ياس': 'yes',
    'ميبي': 'maybe', 'شور': 'sure', 'اوكي': 'ok', 'هي': 'hey',
    'واو': 'wow', 'اوتش': 'ouch', 'ياي': 'yay', 'اوبس': 'oops',
    'هاها': 'haha', 'همم': 'hmm',
}


# ── Multi-Layout Database ──
# Each variant maps physical keys (QWERTY positions) to Arabic characters.
# VK codes: 0x41='a', 0x42='b', etc.  Shifted chars are prefixed with 'S'.
# These 10 variants cover the most common Arabic keyboard standards.

_LAYOUT_VARIANTS = [
    # name, lang_id, normal_map, shift_map
    ("Arabic 101", 0x0401, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic 102", 0x0801, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Egypt)", 0x0C01, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Saudi)", 0x0401, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Morocco)", 0x1801, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Iraq)", 0x0801, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Syria)", 0x1001, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Lebanon)", 0x1001, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Algeria)", 0x1401, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
    ("Arabic (Tunisia)", 0x1C01, {
        0x51:'ض',0x57:'ص',0x45:'ث',0x52:'ق',0x54:'ف',0x59:'غ',0x55:'ع',
        0x49:'ه',0x4F:'خ',0x50:'ح',0xDB:'ج',0xDD:'د',0x41:'ش',0x53:'س',
        0x44:'ي',0x46:'ب',0x47:'ل',0x48:'ا',0x4A:'ت',0x4B:'ن',0x4C:'م',
        0xBA:'ك',0xDE:'ط',0x5A:'ئ',0x58:'ء',0x43:'ؤ',0x56:'ر',0x42:'لا',
        0x4E:'ى',0x4D:'ة',0xBC:'و',0xBE:'ز',0xBF:'ظ',0xC0:'ذ',
    }, {
        0x51:'َ',0x57:'ً',0x45:'ُ',0x52:'ٌ',0x54:'لإ',0x59:'إ',0x55:'‘',
        0x49:'÷',0x4F:'×',0x50:'؛',0xDB:'<',0xDD:'>',0x41:'ِ',0x53:'ٍ',
        0x44:']',0x46:'[',0x47:'لأ',0x48:'أ',0x4A:'ـ',0x4B:'،',0x4C:'/',
        0xBA:':',0xDE:'"',0x5A:'~',0x58:'ْ',0x43:'}',0x56:'{',0x42:'لآ',
        0x4E:'آ',0x4D:'’',0xBC:',',0xBE:'.',0xBF:'؟',0xC0:'ّ',
    }),
]

# Also use installed Windows layouts
class _WinLayout:
    def __init__(self, hkl):
        self.hkl = hkl
        self.name = self._get_name(hkl)
        self.char_to_vk = {}
        self.vk_to_char = {0: {}, 1: {}}
        self._build()

    def _get_name(self, hkl):
        lid = hkl & 0xFFFF
        buf = ctypes.create_unicode_buffer(256)
        _kernel32.GetLocaleInfoW(lid, 0x00000002, buf, 256)
        return buf.value or f"Layout 0x{hkl:X}"

    def _build(self):
        normal = (ctypes.c_ubyte * 256)()
        shift = (ctypes.c_ubyte * 256)()
        shift[0x10] = 0x80
        for vk in range(0x08, 0xFF):
            if vk in (0x0E, 0x0D):
                continue
            buf = ctypes.create_unicode_buffer(8)
            ret = _user32.ToUnicodeEx(vk, 0, normal, buf, 8, 0x04, self.hkl)
            if ret == 1 and buf[0] and buf[0] != '\x00':
                self.vk_to_char[0][vk] = buf[0]
                if buf[0] not in self.char_to_vk:
                    self.char_to_vk[buf[0]] = (vk, 0)
            buf2 = ctypes.create_unicode_buffer(8)
            ret2 = _user32.ToUnicodeEx(vk, 0, shift, buf2, 8, 0x04, self.hkl)
            if ret2 == 1 and buf2[0] and buf2[0] != '\x00':
                self.vk_to_char[1][vk] = buf2[0]
                if buf2[0] not in self.char_to_vk:
                    self.char_to_vk[buf2[0]] = (vk, 1)


def _get_all_layouts():
    layouts = []
    for name, lang_id, normal, shift in _LAYOUT_VARIANTS:
        class FakeLayout:
            pass
        fl = FakeLayout()
        fl.name = name
        fl.hkl = lang_id
        fl.char_to_vk = {}
        fl.vk_to_char = {0: {}, 1: {}}
        for vk, ch in normal.items():
            fl.char_to_vk[ch] = (vk, 0)
            fl.vk_to_char[0][vk] = ch
        for vk, ch in shift.items():
            fl.char_to_vk[ch] = (vk, 1)
            fl.vk_to_char[1][vk] = ch
        layouts.append(fl)

    # Add installed Windows layouts too
    try:
        num = _user32.GetKeyboardLayoutList(0, None)
        buf = (ctypes.c_void_p * num)()
        _user32.GetKeyboardLayoutList(num, buf)
        for i in range(num):
            hkl = buf[i] or 0x0409
            wl = _WinLayout(hkl)
            if len(wl.char_to_vk) > 10:
                layouts.append(wl)
    except Exception:
        pass

    return layouts


def _score_text(text, is_arabic_result=False):
    """Score how much the text looks like real English or Arabic."""
    if is_arabic_result:
        # For Arabic results, score based on Arabic character ratio
        ar_chars = sum(1 for ch in text if _ARABIC_RE.match(ch))
        total = sum(1 for ch in text if ch.isalpha())
        if total == 0:
            return 0.0
        return ar_chars / total

    words = re.findall(r"[a-zA-Z']+", text)
    if not words:
        return 0.0
    matched = 0
    for w in words:
        if w.lower() in _COMMON_WORDS:
            matched += len(w)
    total_chars = sum(len(w) for w in words)
    if total_chars == 0:
        return 0.0
    return matched / total_chars


def _convert_with_layout(text, source, target):
    result = []
    for ch in text:
        if ch == ' ':
            result.append(' ')
            continue
        if ch == '\n':
            result.append('\n')
            continue
        vk_info = source.char_to_vk.get(ch)
        if vk_info is None:
            result.append(ch)
            continue
        vk, shift = vk_info
        target_ch = target.vk_to_char[shift].get(vk, ch)
        result.append(target_ch)
    return ''.join(result)


def _try_all_variants(text, source_is_arabic):
    layouts = _get_all_layouts()
    if not layouts:
        return []

    # Find best source layout
    sources = []
    for layout in layouts:
        matched = sum(1 for ch in text if ch in layout.char_to_vk)
        total = sum(1 for ch in text if not ch.isspace())
        if total > 0:
            score = matched / total
            if score > 0.2:
                sources.append((layout, score))

    if not sources:
        return []

    sources.sort(key=lambda x: x[1], reverse=True)
    candidates = []

    for source, src_score in sources[:3]:
        for target in layouts:
            if target is source:
                continue
            # Skip same-script pairs
            src_has_arabic = any(bool(_ARABIC_RE.match(ch)) for ch in source.char_to_vk)
            tgt_has_arabic = any(bool(_ARABIC_RE.match(ch)) for ch in target.char_to_vk)
            if source_is_arabic and tgt_has_arabic:
                continue
            if not source_is_arabic and not tgt_has_arabic:
                continue

            converted = _convert_with_layout(text, source, target)
            if converted == text:
                continue
            word_score = _score_text(converted, is_arabic_result=tgt_has_arabic)
            candidates.append({
                "source": source.name,
                "target": target.name,
                "converted": converted,
                "word_score": word_score,
                "layout_score": src_score,
            })

    candidates.sort(key=lambda x: x["word_score"], reverse=True)
    return candidates


def detect_and_convert(text):
    # Check common phrase shortcuts first
    for ar, en in _COMMON_ARABIC_FIXES.items():
        if ar in text:
            text = text.replace(ar, en)

    has_arabic = bool(_ARABIC_RE.search(text))
    has_latin = bool(_LATIN_RE.search(text))
    if has_arabic and has_latin:
        return None

    candidates = _try_all_variants(text, has_arabic)
    if not candidates:
        return None

    best = candidates[0]

    # If confidence is low, try AI fallback before giving up
    if best["word_score"] < 0.3 and len(text) > 2:
        ai_result = _ai_fix_layout(text, best["converted"])
        if ai_result and ai_result != best["converted"]:
            best["converted"] = ai_result
            best["word_score"] = 0.5  # Mark as AI-assisted

    if best["word_score"] < 0.05 and len(text) > 3:
        return None

    return {
        "source_name": best["source"],
        "target_name": best["target"],
        "converted": best["converted"],
        "confidence": best["word_score"],
        "all_candidates": candidates[:5],
    }


def _ai_fix_layout(original, converted):
    """Ask AI to guess the intended text from a wrong-layout string."""
    key = config.get("gemini_api_key", "").strip() or config.get("openrouter_api_key", "").strip()
    if not key:
        return None

    prompt = (
        "You are a keyboard layout fixer. Someone typed text on the wrong keyboard layout.\n"
        f"Original garbled text: {original}\n"
        f"Physical key mapping result: {converted}\n"
        "What did they most likely intend to type? Reply with ONLY the corrected text, nothing else."
    )

    try:
        if config.get("provider") == "openrouter":
            model = config.get("openrouter_model", FREE_OPENROUTER_MODELS[0])
            url = "https://openrouter.ai/api/v1/chat/completions"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 64,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://grammar-checker-app",
                "X-Title": "Grammar Checker",
            }
            resp = _post_json(url, headers, body)
            return resp["choices"][0]["message"]["content"].strip()
        else:
            model = config.get("gemini_model", "gemini-3.1-flash-lite-preview").strip() or "gemini-2.0-flash-lite"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            headers = {"Content-Type": "application/json"}
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 64}
            }
            resp = _post_json(url, headers, body)
            return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


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
            result = detect_and_convert(text_to_fix)
            src = result["source_name"] if result else ""
            tgt = result["target_name"] if result else ""
            fixed = result["converted"] if result else text_to_fix
            get_root().after(100, lambda: show_layout_bubble(text_to_fix, fixed, src, tgt))

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

def show_layout_bubble(original, fixed, source_name="", target_name=""):
    global _active_layout_bubble
    if _active_layout_bubble:
        try: _active_layout_bubble.destroy()
        except: pass
    _active_layout_bubble = LayoutFixBubble(original, fixed, source_name, target_name)


class LayoutFixBubble:
    ANIM_STEPS = 12
    ANIM_MS = 12

    def __init__(self, original, fixed, source_name="", target_name=""):
        self.original = original
        self.fixed = fixed
        self.source_name = source_name
        self.target_name = target_name
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
        header_text = f"⌨  Layout Fix"
        if self.source_name and self.target_name:
            header_text = f"⌨  {self.source_name} → {self.target_name}"
        tk.Label(hdr, text=header_text,
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
