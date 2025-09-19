import json
import os
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from urllib import request
import winsound
import datetime

# ---- tray deps
try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

APP_TITLE_BASE = "Crypto Price Alert"
DB_FILENAME = "crypto_alerts.json"

DEFAULT_SETTINGS = {
    "check_interval_seconds": 60,
    "auto_silence_seconds": 60,
    "assume_quote": "USDT",
    "lang": "en"  # "en" or "fa"
}

# --------------------------- Utilities ---------------------------

def app_dir():
    return os.path.dirname(os.path.abspath(sys.argv[0]))

def db_path():
    return os.path.join(app_dir(), DB_FILENAME)

def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def load_db():
    if not os.path.exists(db_path()):
        data = {"settings": DEFAULT_SETTINGS.copy(), "coins": []}
        save_db(data)
        return data
    try:
        with open(db_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"settings": DEFAULT_SETTINGS.copy(), "coins": []}
    if "settings" not in data: data["settings"] = {}
    for k, v in DEFAULT_SETTINGS.items():
        data["settings"].setdefault(k, v)
    data.setdefault("coins", [])
    return data

def save_db(data):
    try:
        with open(db_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        messagebox.showerror("Save Error", f"Could not save database:\n{e}")

def log_message(msg: str):
    try:
        log_path = os.path.join(app_dir(), "log.txt")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def normalize_symbol(sym: str, assume_quote: str):
    s = (sym or "").strip().upper().replace("/", "").replace("-", "")
    if not s:
        return ""
    if len(s) <= 5 and not s.endswith(assume_quote):
        s = f"{s}{assume_quote}"
    return s

def _http_json(url, timeout=10):
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _sym_binance(sym):  # BTC -> BTCUSDT
    s = sym.upper().replace("/", "").replace("-", "")
    if s.endswith("USDT"): return s
    if len(s) <= 5: return s + "USDT"
    return s

def _sym_bybit(sym):    # BTC -> BTCUSDT
    return _sym_binance(sym)

def _sym_bitunix(sym):  # BTC -> BTCUSDT
    return _sym_binance(sym)

def _sym_coinbase(sym): # BTC -> BTC-USD
    s = sym.upper().replace("/", "").replace("-", "")
    base = s[:-4] if s.endswith("USDT") else s
    return f"{base}-USD"

def _sym_upbit(sym):    # BTC -> USDT-BTC
    s = sym.upper().replace("/", "").replace("-", "")
    base = s[:-4] if s.endswith("USDT") else s
    return f"USDT-{base}"

def _sym_okx(sym):      # BTC -> BTC-USDT
    s = sym.upper().replace("/", "").replace("-", "")
    base = s[:-4] if s.endswith("USDT") else s
    return f"{base}-USDT"

def fetch_price_multi(sym: str) -> float:
    """
    Try Binance → Bitunix → Bybit → Coinbase → Upbit → OKX (no API keys).
    Returns last trade price as float.
    Raises RuntimeError if all fail.
    """
    errors = []

    # 1) Binance
    try:
        pair = _sym_binance(sym)
        data = _http_json(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={pair}")
        log_message(f"Binance Price: {data['price']}")
        return float(data["price"])
    except Exception as e:
        errors.append(f"Binance: {e}")
        log_message(f"Binance: {e}")

    # 2) BitUnix (futures endpoint with markPrice; used as a fallback)
    try:
        pair = _sym_bitunix(sym)
        data = _http_json(f"https://fapi.bitunix.com/api/v1/futures/market/tickers?symbols={pair}")
        items = data.get('data') or []
        if items:
            log_message(f"BitUnix Price: {items[0]['markPrice']}")
            return float(items[0]["markPrice"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"BitUnix: {e}")
        log_message(f"BitUnix: {e}")

    # 3) Bybit
    try:
        pair = _sym_bybit(sym)
        data = _http_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={pair}")
        items = data.get("result", {}).get("list", [])
        if items:
            log_message(f"Bybit Price: {items[0]['lastPrice']}")
            return float(items[0]["lastPrice"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"Bybit: {e}")
        log_message(f"Bybit: {e}")

    # 4) Coinbase Exchange
    try:
        pair = _sym_coinbase(sym)
        data = _http_json(f"https://api.exchange.coinbase.com/products/{pair}/ticker")
        log_message(f"Coinbase Price: {data['price']}")
        return float(data["price"])
    except Exception as e:
        errors.append(f"Coinbase: {e}")
        log_message(f"Coinbase: {e}")

    # 5) Upbit
    try:
        pair = _sym_upbit(sym)
        arr = _http_json(f"https://api.upbit.com/v1/ticker?markets={pair}")
        if isinstance(arr, list) and arr:
            log_message(f"Upbit Price: {arr[0]['trade_price']}")
            return float(arr[0]["trade_price"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"Upbit: {e}")
        log_message(f"Upbit: {e}")

    # 6) OKX
    try:
        pair = _sym_okx(sym)
        data = _http_json(f"https://www.okx.com/api/v5/market/ticker?instId={pair}")
        arr = data.get("data", [])
        if arr:
            log_message(f"OKX Price: {arr[0]['last']}")
            return float(arr[0]["last"])
        raise RuntimeError("empty data")
    except Exception as e:
        errors.append(f"OKX: {e}")
        log_message(f"OKX: {e}")

    raise RuntimeError("All price sources failed: " + " | ".join(errors))

# --------------------------- i18n ---------------------------

def load_lang(lang_code):
    fname = f"lang_{lang_code}.json"
    path = os.path.join(app_dir(), fname)
    fallback = {"app_title": APP_TITLE_BASE}
    data = load_json_file(path, fallback)
    # Ensure minimal keys exist to avoid KeyErrors
    data.setdefault("app_title", APP_TITLE_BASE)
    return data

# --------------------------- Alarm Window ---------------------------

class AlarmWindow(tk.Toplevel):
    def __init__(self, master, symbol, target_price, current_price, auto_silence_seconds=60):
        super().__init__(master)
        self.master_app = master
        self.symbol = symbol
        self.target_price = target_price
        self.current_price = current_price
        self.auto_silence_seconds = max(1, int(auto_silence_seconds))

        self.title(master.t("alert_title").format(symbol=symbol))
        self.geometry("420x240")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._sound_thread = None
        self._sound_stop = threading.Event()

        header = ttk.Label(self, text=master.t("alert_header").format(symbol=symbol),
                           font=("Segoe UI", 14, "bold"), anchor="center", justify="center")
        header.pack(pady=(14,8), fill="x")

        info = ttk.Label(self, anchor="center", justify="center",
                         text=(master.t("alert_text").format(
                                target=target_price,
                                current=current_price,
                                secs=self.auto_silence_seconds)))
        info.pack(padx=12)

        btns = ttk.Frame(self)
        btns.pack(pady=16)

        self.silence_btn = ttk.Button(btns, text=master.t("silence_sound"), command=self.silence_sound)
        self.silence_btn.grid(row=0, column=0, padx=6)

        close_btn = ttk.Button(btns, text=master.t("close_window"), command=self.on_close)
        close_btn.grid(row=0, column=1, padx=6)

        self.after(100, self.start_sound_loop)

    def start_sound_loop(self):
        if self._sound_thread is not None:
            return
        self._sound_stop.clear()
        self._sound_thread = threading.Thread(target=self._beep_worker, daemon=True)
        self._sound_thread.start()

    def _beep_worker(self):
        start = time.time()
        # Loop short beeps every ~800 ms
        while not self._sound_stop.is_set():
            try:
                winsound.Beep(1500, 400)
            except Exception:
                winsound.MessageBeep()
                time.sleep(0.4)
            for _ in range(4):
                if self._sound_stop.is_set():
                    break
                time.sleep(0.1)
            if time.time() - start >= self.auto_silence_seconds:
                self._sound_stop.set()
                break

    def silence_sound(self):
        self._sound_stop.set()

    def on_close(self):
        self.silence_sound()
        self.destroy()

# --------------------------- Edit Dialog ---------------------------

class EditCoinDialog(simpledialog.Dialog):
    def __init__(self, parent, title, coin=None, assume_quote="USDT"):
        self.parent_app = parent
        self.coin = coin or {}
        self.assume_quote = assume_quote
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text=self.parent_app.t("symbol_hint")).grid(row=0, column=0, sticky="w", padx=6, pady=(8,2))
        self.symbol_var = tk.StringVar(value=self.coin.get("symbol", ""))
        self.symbol_entry = ttk.Entry(master, textvariable=self.symbol_var, width=28, justify="left")
        self.symbol_entry.grid(row=1, column=0, sticky="we", padx=6)

        ttk.Label(master, text=self.parent_app.t("target_price")).grid(row=2, column=0, sticky="w", padx=6, pady=(8,2))
        self.price_var = tk.StringVar(value=str(self.coin.get("target_price", "")))
        self.price_entry = ttk.Entry(master, textvariable=self.price_var, width=28, justify="left")
        self.price_entry.grid(row=3, column=0, sticky="we", padx=6)

        ttk.Label(master, text=self.parent_app.t("condition")).grid(row=4, column=0, sticky="w", padx=6, pady=(8,2))
        self.cond_var = tk.StringVar(value=self.coin.get("condition", ">="))
        self.cond_combo = ttk.Combobox(master, textvariable=self.cond_var, values=[">=", "<="], state="readonly", width=6)
        self.cond_combo.grid(row=5, column=0, sticky="w", padx=6)

        return self.symbol_entry

    def validate(self):
        sym = normalize_symbol(self.symbol_var.get(), self.assume_quote)
        if not sym:
            messagebox.showwarning(self.parent_app.t("invalid_symbol_title"), self.parent_app.t("invalid_symbol_body"))
            return False
        try:
            price = float(self.price_var.get())
            if price <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning(self.parent_app.t("invalid_price_title"), self.parent_app.t("invalid_price_body"))
            return False
        cond = self.cond_var.get()
        if cond not in (">=", "<="):
            messagebox.showwarning(self.parent_app.t("invalid_condition_title"), self.parent_app.t("invalid_condition_body"))
            return False
        self.result = {"symbol": sym, "target_price": price, "condition": cond}
        return True

# --------------------------- Main App (with i18n + Tray) ---------------------------

class CryptoAlertApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self._polling = False

        # Load DB and language
        self.db = load_db()
        self.coins = self.db["coins"]
        self.settings = self.db["settings"]
        self.lang = self.settings.get("lang", "en")
        self.i18n = load_lang(self.lang)

        self.title(self.i18n.get("app_title", APP_TITLE_BASE))
        self.geometry("760x560")
        self.minsize(720, 520)

        # Tray-related
        self.tray_icon = None

        # UI
        self._build_ui()

        # intercept close to hide
        self.protocol("WM_DELETE_WINDOW", self.on_close_to_tray)

        # Start polling loop
        self._polling_started = False
        self._schedule_poll()

        # Create tray icon
        self.create_tray_icon()

        log_message("App started")

    # --- i18n helpers ---
    def t(self, key):
        return self.i18n.get(key, key)

    def switch_language(self, lang_code):
        if lang_code not in ("en", "fa"):
            return
        self.lang = lang_code
        self.settings["lang"] = lang_code
        save_db(self.db)
        self.i18n = load_lang(lang_code)
        # Update UI texts and tray
        self.rebuild_ui_texts()
        self.recreate_tray_menu()

    # ---------- UI ----------

    def _build_ui(self):
        # Menu bar with language toggle button
        topbar = ttk.Frame(self, padding=(10, 8, 10, 0))
        topbar.pack(fill="x")
        self.lang_btn = ttk.Button(topbar, text="فارسی" if self.lang == "en" else "English",
                                   command=lambda: self.switch_language("fa" if self.lang=="en" else "en"))
        self.lang_btn.pack(side="right")

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        # Add section
        self.add_frame = ttk.LabelFrame(main, text=self.t("add_new_alert"))
        self.add_frame.pack(fill="x", pady=(0,10))

        self.lbl_symbol = ttk.Label(self.add_frame, text=self.t("symbol"))
        self.lbl_symbol.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.symbol_var = tk.StringVar()
        self.symbol_entry = ttk.Entry(self.add_frame, textvariable=self.symbol_var, width=18, justify="left")
        self.symbol_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")

        self.lbl_target = ttk.Label(self.add_frame, text=self.t("target_price"))
        self.lbl_target.grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.price_var = tk.StringVar()
        self.price_entry = ttk.Entry(self.add_frame, textvariable=self.price_var, width=12, justify="left")
        self.price_entry.grid(row=0, column=3, padx=6, pady=6, sticky="w")

        self.lbl_cond = ttk.Label(self.add_frame, text=self.t("condition"))
        self.lbl_cond.grid(row=0, column=4, padx=6, pady=6, sticky="w")
        self.cond_var = tk.StringVar(value=">=")
        self.cond_combo = ttk.Combobox(self.add_frame, textvariable=self.cond_var, values=[">=", "<="], state="readonly", width=6)
        self.cond_combo.grid(row=0, column=5, padx=6, pady=6, sticky="w")

        self.add_btn = ttk.Button(self.add_frame, text=self.t("add_alert"), command=self.add_alert)
        self.add_btn.grid(row=0, column=6, padx=10, pady=6, sticky="w")

        # Settings section
        self.settings_frame = ttk.LabelFrame(main, text=self.t("settings"))
        self.settings_frame.pack(fill="x", pady=(0,10))

        self.lbl_interval = ttk.Label(self.settings_frame, text=self.t("check_interval"))
        self.lbl_interval.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.interval_var = tk.StringVar(value=str(self.settings.get("check_interval_seconds", 60)))
        ttk.Entry(self.settings_frame, textvariable=self.interval_var, width=8, justify="left").grid(row=0, column=1, padx=6, pady=6, sticky="w")

        self.lbl_silence = ttk.Label(self.settings_frame, text=self.t("auto_silence"))
        self.lbl_silence.grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.silence_var = tk.StringVar(value=str(self.settings.get("auto_silence_seconds", 60)))
        ttk.Entry(self.settings_frame, textvariable=self.silence_var, width=8, justify="left").grid(row=0, column=3, padx=6, pady=6, sticky="w")

        self.save_settings_btn = ttk.Button(self.settings_frame, text=self.t("save_settings"), command=self.save_settings)
        self.save_settings_btn.grid(row=0, column=4, padx=10, pady=6)

        self.status_var = tk.StringVar(value=self.t("ready"))
        self.status_lbl = ttk.Label(self.settings_frame, textvariable=self.status_var)
        self.status_lbl.grid(row=0, column=5, padx=6, pady=6, sticky="e")

        for i in range(6):
            self.settings_frame.grid_columnconfigure(i, weight=1)

        # List section
        self.list_frame = ttk.LabelFrame(main, text=self.t("active_alerts"))
        self.list_frame.pack(fill="both", expand=True)

        cols = ("symbol", "target", "condition")
        self.tree = ttk.Treeview(self.list_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("symbol", text=self.t("symbol_col"))
        self.tree.heading("target", text=self.t("target_col"))
        self.tree.heading("condition", text=self.t("condition_col"))
        self.tree.column("symbol", width=160, anchor="center")
        self.tree.column("target", width=160, anchor="center")
        self.tree.column("condition", width=140, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")

        # Buttons
        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=10)
        self.btn_edit = ttk.Button(btns, text=self.t("edit_selected"), command=self.edit_selected)
        self.btn_edit.pack(side="left", padx=5)
        self.btn_delete = ttk.Button(btns, text=self.t("delete_selected"), command=self.delete_selected)
        self.btn_delete.pack(side="left", padx=5)
        self.btn_refresh = ttk.Button(btns, text=self.t("refresh_now"), command=self.manual_refresh)
        self.btn_refresh.pack(side="right", padx=5)

        self.refresh_tree()

    def rebuild_ui_texts(self):
        self.title(self.t("app_title"))
        self.lang_btn.config(text=("فارسی" if self.lang=="en" else "English"))
        self.add_frame.config(text=self.t("add_new_alert"))
        self.lbl_symbol.config(text=self.t("symbol"))
        self.lbl_target.config(text=self.t("target_price"))
        self.lbl_cond.config(text=self.t("condition"))
        self.add_btn.config(text=self.t("add_alert"))
        self.settings_frame.config(text=self.t("settings"))
        self.lbl_interval.config(text=self.t("check_interval"))
        self.lbl_silence.config(text=self.t("auto_silence"))
        self.save_settings_btn.config(text=self.t("save_settings"))
        self.status_var.set(self.t("ready"))
        self.list_frame.config(text=self.t("active_alerts"))
        self.tree.heading("symbol", text=self.t("symbol_col"))
        self.tree.heading("target", text=self.t("target_col"))
        self.tree.heading("condition", text=self.t("condition_col"))
        self.btn_edit.config(text=self.t("edit_selected"))
        self.btn_delete.config(text=self.t("delete_selected"))
        self.btn_refresh.config(text=self.t("refresh_now"))

    # ---------- Tray ----------

    def _build_tray_image(self, size=32):
        if Image is None:
            return None
        img = Image.new("RGBA", (size, size), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        r = size//2 - 2
        center = (size//2, size//2)
        draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), fill=(255, 184, 28, 255), outline=(90, 60, 0, 255), width=2)
        draw.rectangle((center[0]-2, 6, center[0]+2, size-6), fill=(255,255,255,220))
        return img

    def create_tray_icon(self):
        if pystray is None or Image is None:
            log_message("pystray/Pillow not installed; tray icon disabled.")
            return

        icon_image = self._build_tray_image(32)

        def on_open(icon, item):
            self.after(0, self.show_window)

        def on_toggle(icon, item):
            self.after(0, self.toggle_window)

        def on_exit(icon, item):
            self.after(0, self.exit_app)

        # build menu with localized labels
        self.tray_menu = pystray.Menu(
            pystray.MenuItem(self.t("tray_open"), on_open, default=True),
            pystray.MenuItem(self.t("tray_toggle"), on_toggle),
            pystray.MenuItem(self.t("tray_exit"), on_exit),
        )

        self.tray_icon = pystray.Icon("CryptoPriceAlert", icon_image, self.t("app_title"), self.tray_menu)

        def _run_tray():
            try:
                self.tray_icon.run()
            except Exception as e:
                log_message(f"Tray icon failed: {e}")

        threading.Thread(target=_run_tray, daemon=True).start()
        log_message("Tray icon started")

    def recreate_tray_menu(self):
        # Safely rebuild tray with new language labels
        try:
            if self.tray_icon:
                self.tray_icon.visible = False
                self.tray_icon.stop()
        except Exception:
            pass
        self.create_tray_icon()

    def on_close_to_tray(self):
        self.withdraw()
        self.update_idletasks()
        log_message("Main window hidden to tray")

    def show_window(self):
        try:
            self.deiconify()
            self.after(50, lambda: self.lift())
            self.focus_force()
        except Exception:
            pass
        log_message("Main window shown from tray")

    def toggle_window(self):
        if self.state() == "withdrawn":
            self.show_window()
        else:
            self.on_close_to_tray()

    def exit_app(self):
        log_message("Exit requested from tray menu")
        try:
            if self.tray_icon:
                self.tray_icon.visible = False
                self.tray_icon.stop()
        except Exception:
            pass
        self.destroy()

    # ---------- Data ops ----------

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for coin in self.coins:
            iid = coin["id"]
            self.tree.insert("", "end", iid=iid, values=(coin["symbol"], coin["target_price"], coin["condition"]))

    def add_alert(self):
        sym_raw = self.symbol_var.get()
        price_raw = self.price_var.get()
        sym = normalize_symbol(sym_raw, self.settings.get("assume_quote", "USDT"))
        if not sym:
            messagebox.showwarning(self.t("invalid_symbol_title"), self.t("invalid_symbol_body"))
            return
        try:
            price = float(price_raw)
            if price <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning(self.t("invalid_price_title"), self.t("invalid_price_body"))
            return
        cond = self.cond_var.get()
        if cond not in (">=", "<="):
            messagebox.showwarning(self.t("invalid_condition_title"), self.t("invalid_condition_body"))
            return

        coin = {
            "id": str(uuid.uuid4()),
            "symbol": sym,
            "target_price": price,
            "condition": cond
        }
        self.coins.append(coin)
        self.db["coins"] = self.coins
        save_db(self.db)
        self.refresh_tree()
        self.symbol_var.set("")
        self.price_var.set("")
        self.cond_var.set(">=")

    def edit_selected(self):
        item = self.tree.selection()
        if not item:
            messagebox.showinfo(self.t("edit_title"), self.t("edit_select_msg"))
            return
        iid = item[0]
        coin = next((c for c in self.coins if c["id"] == iid), None)
        if not coin:
            return
        dlg = EditCoinDialog(self, self.t("edit_dialog_title"), coin, assume_quote=self.settings.get("assume_quote", "USDT"))
        if dlg.result:
            coin["symbol"] = dlg.result["symbol"]
            coin["target_price"] = float(dlg.result["target_price"])
            coin["condition"] = dlg.result["condition"]
            save_db(self.db)
            self.refresh_tree()

    def delete_selected(self):
        item = self.tree.selection()
        if not item:
            messagebox.showinfo(self.t("delete_title"), self.t("delete_select_msg"))
            return
        iid = item[0]
        self.coins = [c for c in self.coins if c["id"] != iid]
        self.db["coins"] = self.coins
        save_db(self.db)
        self.refresh_tree()

    def save_settings(self):
        try:
            interval = int(float(self.interval_var.get()))
            silence = int(float(self.silence_var.get()))
            if interval < 10:
                raise ValueError("Interval too short (< 10s).")
            if silence < 1:
                raise ValueError("Auto-silence must be >= 1.")
        except Exception as e:
            messagebox.showwarning(self.t("invalid_settings_title"), self.t("invalid_settings_body").format(err=e))
            return
        self.settings["check_interval_seconds"] = interval
        self.settings["auto_silence_seconds"] = silence
        save_db(self.db)
        self.status_var.set(self.t("settings_saved"))

    # ---------- Polling ----------

    def _schedule_poll(self):
        try:
            interval_ms = int(self.settings.get("check_interval_seconds", 60)) * 1000
        except Exception:
            interval_ms = 60000
        self.after(interval_ms, self._on_poll_timer)
        if not self._polling:
            self._polling = True
            self.after(1000, self.check_prices)

    def _on_poll_timer(self):
        self.check_prices()
        self._schedule_poll()

    def manual_refresh(self):
        self.check_prices()

    def check_prices(self):
        if not self.coins:
            self.status_var.set(self.t("no_alerts"))
            return

        to_remove_ids = []
        last_errors = []

        for coin in list(self.coins):
            sym = coin["symbol"]
            try:
                price = fetch_price_multi(sym)
            except RuntimeError as e:
                msg = (self.t("symbol_not_found_msg").format(sym=sym) + "\n\n" + self.t("vpn_hint"))
                messagebox.showwarning(self.t("symbol_not_found_title"), msg)
                log_message(f"Symbol {sym} not found. Error: {e}")
                last_errors.append(str(e))
                continue

            cond = coin.get("condition", ">=")
            target = float(coin["target_price"])
            hit = (price >= target) if cond == ">=" else (price <= target)

            if hit:
                AlarmWindow(self, symbol=sym, target_price=target, current_price=price,
                            auto_silence_seconds=int(self.settings.get("auto_silence_seconds", 60)))
                to_remove_ids.append(coin["id"])

            self.update_idletasks()
            self.update()
            time.sleep(0.05)

        if to_remove_ids:
            self.coins = [c for c in self.coins if c["id"] not in to_remove_ids]
            self.db["coins"] = self.coins
            save_db(self.db)
            self.refresh_tree()

        t = time.strftime("%Y-%m-%d %H:%M:%S")
        if last_errors:
            self.status_var.set(self.t("last_check_errors").format(time=t, n=len(last_errors)))
        else:
            self.status_var.set(self.t("last_check_ok").format(time=t))

# --------------------------- Run ---------------------------

def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = CryptoAlertApp()
    try:
        s = ttk.Style(root)
        if "vista" in s.theme_names():
            s.theme_use("vista")
        elif "xpnative" in s.theme_names():
            s.theme_use("xpnative")
    except Exception:
        pass

    root.mainloop()
    log_message("App closed")

if __name__ == "__main__":
    main()
