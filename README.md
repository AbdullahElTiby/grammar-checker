# ✦ Grammar Checker — Free Edition

System tray app that checks grammar anywhere on your PC. **100% free** using either Google Gemini Flash or OpenRouter free models.

---

## Quick Start

1. Double-click `setup_and_run.bat`
2. Settings window opens — add your free API key (see below)
3. Click Save

---

## Getting a Free API Key

### Option A — Google Gemini Flash ✅ (Recommended)
- Free: **1,500 requests/day**, no credit card needed
1. Go to **https://aistudio.google.com**
2. Sign in with your Google account
3. Click **"Get API key"** → Create API key
4. Copy and paste it into Settings → Gemini API Key

### Option B — OpenRouter (many free models)
- Free models available (rate-limited, no credit card)
1. Go to **https://openrouter.ai**
2. Create a free account
3. Go to **Keys** → Create key
4. Paste into Settings → OpenRouter API Key
5. Choose a free model (ones ending in `:free`)

Good free models on OpenRouter:
- `nvidia/llama-3.1-nemotron-ultra-253b-v1:free` — very capable
- `deepseek/deepseek-chat-v3-0324:free` — great quality
- `google/gemini-2.0-flash-exp:free` — fast
- `qwen/qwen3-235b-a22b:free` — strong reasoning

---

## How to Use

1. In **any app** — select text and copy it (`Ctrl+C`)
2. Press **`Ctrl+Shift+G`**
3. Popup shows:
   - ✅ Corrected text with one-click copy
   - 🔴 → 🟢 Each issue with explanation
   - Score out of 100

---

## Auto-start with Windows

1. Press `Win+R`, type `shell:startup`, hit Enter
2. Create a new file `grammar.bat` with:
   ```
   @echo off
   start /b pythonw C:\path\to\grammar_checker.py
   ```
3. Save it — the app will launch automatically at login

---

## Dependencies

- Python 3.9+
- Windows 10/11
- No paid API needed!
