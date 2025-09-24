import json
import os
import sys
import threading
import time
import uuid
import queue
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from urllib import request
import winsound
import datetime

APP_TITLE = "Crypto Price Alert (Final)"
DB_FILENAME = "crypto_alerts.json"

DEFAULT_SETTINGS = {
    "check_interval_seconds": 60,
    "auto_silence_seconds": 60,
    "assume_quote": "USDT"
}

# --------------------------- Utilities & DB ---------------------------

def app_dir():
    return os.path.dirname(os.path.abspath(sys.argv[0]))

def db_path():
    return os.path.join(app_dir(), DB_FILENAME)

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
    # defaults
    data.setdefault("settings", {})
    for k, v in DEFAULT_SETTINGS.items():
        data["settings"].setdefault(k, v)
    data.setdefault("coins", [])
    # add enabled field if missing
    for c in data["coins"]:
        if "enabled" not in c:
            c["enabled"] = True
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

# --------------------------- Price Sources (multi-exchange) ---------------------------

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
    Returns last price as float. Raises RuntimeError if all fail.
    """
    errors = []

    # 1) Binance
    try:
        pair = _sym_binance(sym)
        data = _http_json(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={pair}")
        return float(data["price"])
    except Exception as e:
        errors.append(f"Binance: {e}")
        log_message(f"Binance error {sym}: {e}")

    # 2) Bitunix (futures markPrice as fallback)
    try:
        pair = _sym_bitunix(sym)
        data = _http_json(f"https://fapi.bitunix.com/api/v1/futures/market/tickers?symbols={pair}")
        items = data.get("data") or []
        if items:
            return float(items[0]["markPrice"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"Bitunix: {e}")
        log_message(f"Bitunix error {sym}: {e}")

    # 3) Bybit
    try:
        pair = _sym_bybit(sym)
        data = _http_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={pair}")
        items = data.get("result", {}).get("list", [])
        if items:
            return float(items[0]["lastPrice"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"Bybit: {e}")
        log_message(f"Bybit error {sym}: {e}")

    # 4) Coinbase
    try:
        pair = _sym_coinbase(sym)
        data = _http_json(f"https://api.exchange.coinbase.com/products/{pair}/ticker")
        return float(data["price"])
    except Exception as e:
        errors.append(f"Coinbase: {e}")
        log_message(f"Coinbase error {sym}: {e}")

    # 5) Upbit
    try:
        pair = _sym_upbit(sym)
        arr = _http_json(f"https://api.upbit.com/v1/ticker?markets={pair}")
        if isinstance(arr, list) and arr:
            return float(arr[0]["trade_price"])
        raise RuntimeError("empty list")
    except Exception as e:
        errors.append(f"Upbit: {e}")
        log_message(f"Upbit error {sym}: {e}")

    # 6) OKX
    try:
        pair = _sym_okx(sym)
        data = _http_json(f"https://www.okx.com/api/v5/market/ticker?instId={pair}")
        arr = data.get("data", [])
        if arr:
            return float(arr[0]["last"])
        raise RuntimeError("empty data")
    except Exception as e:
        errors.append(f"OKX: {e}")
        log_message(f"OKX error {sym}: {e}")

    raise RuntimeError("All price sources failed: " + " | ".join(errors))

# --------------------------- Alarm Window ---------------------------

class AlarmWindow(tk.Toplevel):
    def __init__(self, master, symbol, target_price, current_price, auto_silence_seconds=60):
        super().__init__(master)
        self.title(f"ALERT: {symbol}")
        self.geometry("420x220")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.symbol = symbol
        self.target_price = target_price
        self.current_price = current_price
        self.auto_silence_seconds = max(1, int(auto_silence_seconds))
        self._sound_thread = None
        self._sound_stop = threading.Event()

        header = ttk.Label(self, text=f"Price Alert Triggered: {symbol}", font=("Segoe UI", 14, "bold"))
        header.pack(pady=(14,8))

        info = ttk.Label(self, anchor="center", justify="center",
                         text=(f"Target price: {target_price}\n"
                               f"Current price: {current_price}\n\n"
                               f"Sound will auto-silence after {self.auto_silence_seconds} seconds.\n"
                               f"This window stays open until you close it."))
        info.pack(padx=12)

        btns = ttk.Frame(self)
        btns.pack(pady=16)

        self.silence_btn = ttk.Button(btns, text="Silence Sound", command=self.silence_sound)
        self.silence_btn.grid(row=0, column=0, padx=6)

        close_btn = ttk.Button(btns, text="Close Window", command=self.on_close)
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
        self.coin = coin or {}
        self.assume_quote = assume_quote
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Symbol (e.g., BTCUSDT or BTC):").grid(row=0, column=0, sticky="w", padx=6, pady=(8,2))
        self.symbol_var = tk.StringVar(value=self.coin.get("symbol", ""))
        self.symbol_entry = ttk.Entry(master, textvariable=self.symbol_var, width=28)
        self.symbol_entry.grid(row=1, column=0, sticky="we", padx=6)

        ttk.Label(master, text="Target Price:").grid(row=2, column=0, sticky="w", padx=6, pady=(8,2))
        self.price_var = tk.StringVar(value=str(self.coin.get("target_price", "")))
        self.price_entry = ttk.Entry(master, textvariable=self.price_var, width=28)
        self.price_entry.grid(row=3, column=0, sticky="we", padx=6)

        ttk.Label(master, text="Condition:").grid(row=4, column=0, sticky="w", padx=6, pady=(8,2))
        self.cond_var = tk.StringVar(value=self.coin.get("condition", ">="))
        self.cond_combo = ttk.Combobox(master, textvariable=self.cond_var, values=[">=", "<="], state="readonly", width=6)
        self.cond_combo.grid(row=5, column=0, sticky="w", padx=6)

        ttk.Label(master, text="Status:").grid(row=6, column=0, sticky="w", padx=6, pady=(8,2))
        self.enabled_var = tk.BooleanVar(value=bool(self.coin.get("enabled", True)))
        self.enabled_chk = ttk.Checkbutton(master, text="Enabled", variable=self.enabled_var)
        self.enabled_chk.grid(row=7, column=0, sticky="w", padx=6)

        return self.symbol_entry

    def validate(self):
        sym = normalize_symbol(self.symbol_var.get(), self.assume_quote)
        if not sym:
            messagebox.showwarning("Invalid Symbol", "Please enter a symbol.")
            return False
        try:
            price = float(self.price_var.get())
            if price <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning("Invalid Price", "Please enter a valid target price (> 0).")
            return False
        cond = self.cond_var.get()
        if cond not in (">=", "<="):
            messagebox.showwarning("Invalid Condition", "Condition must be >= or <=.")
            return False
        self.result = {"symbol": sym, "target_price": price, "condition": cond, "enabled": bool(self.enabled_var.get())}
        return True

# --------------------------- Price Checker Thread ---------------------------

class PriceChecker(threading.Thread):
    """
    Worker thread that checks prices of enabled alerts without blocking UI.
    Sends UI tasks via queue to the main thread.
    """
    def __init__(self, app, task_queue):
        super().__init__(daemon=True)
        self.app = app
        self.q = task_queue
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                interval = int(self.app.settings.get("check_interval_seconds", 60))
            except Exception:
                interval = 60

            coins_snapshot = list(self.app.coins)  # shallow copy
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            errors = 0

            for coin in coins_snapshot:
                if self._stop.is_set():
                    break
                if not coin.get("enabled", True):
                    continue
                sym = coin["symbol"]
                try:
                    price = fetch_price_multi(sym)
                except Exception as e:
                    errors += 1
                    log_message(f"Fetch error for {sym}: {e}")
                    continue

                cond = coin.get("condition", ">=")
                target = float(coin["target_price"])
                hit = (price >= target) if cond == ">=" else (price <= target)

                if hit:
                    # enqueue a UI task: show alarm and disable the alert (do not delete)
                    self.q.put(("ALARM", {"id": coin["id"], "symbol": sym, "target": target, "price": price}))

                # small polite delay
                for _ in range(5):
                    if self._stop.is_set():
                        break
                    time.sleep(0.01)

            # update status line via queue
            if errors:
                self.q.put(("STATUS", f"Last check {now_str} — {errors} error(s)."))
            else:
                self.q.put(("STATUS", f"Last check {now_str} — OK."))

            # sleep until next cycle
            for _ in range(interval * 10):  # 0.1s ticks
                if self._stop.is_set():
                    break
                time.sleep(0.1)

# --------------------------- Main App ---------------------------

class CryptoAlertApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x560")
        self.minsize(820, 520)

        self.db = load_db()
        self.coins = self.db["coins"]
        self.settings = self.db["settings"]

        self._sort_state = {"column": None, "reverse": False}
        self._filter_text = ""

        # UI
        self._build_ui()

        # Worker thread & queue
        self.task_queue = queue.Queue()
        self.worker = PriceChecker(self, self.task_queue)
        self.worker.start()
        self.after(100, self._process_queue)

    # ---------- UI ----------

    def _build_ui(self):
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        # Top controls: language placeholder + search + quick actions
        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0,8))

        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search_changed())
        ttk.Entry(top, textvariable=self.search_var, width=24).pack(side="left", padx=(4,10))

        ttk.Button(top, text="Enable Selected", command=self.enable_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Disable Selected", command=self.disable_selected).pack(side="left", padx=4)
        ttk.Button(top, text="Refresh Now", command=self.manual_refresh).pack(side="right", padx=4)

        # Add section
        add_frame = ttk.LabelFrame(main, text="Add New Alert")
        add_frame.pack(fill="x", pady=(0,10))

        ttk.Label(add_frame, text="Symbol:").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.symbol_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.symbol_var, width=18).grid(row=0, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(add_frame, text="Target Price:").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.price_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.price_var, width=12).grid(row=0, column=3, padx=6, pady=6, sticky="w")

        ttk.Label(add_frame, text="Condition:").grid(row=0, column=4, padx=6, pady=6, sticky="w")
        self.cond_var = tk.StringVar(value=">=")
        ttk.Combobox(add_frame, textvariable=self.cond_var, values=[">=", "<="], state="readonly", width=6).grid(row=0, column=5, padx=6, pady=6, sticky="w")

        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(add_frame, text="Enabled", variable=self.enabled_var).grid(row=0, column=6, padx=10, pady=6, sticky="w")

        ttk.Button(add_frame, text="Add Alert", command=self.add_alert).grid(row=0, column=7, padx=10, pady=6, sticky="w")

        # Settings section
        settings_frame = ttk.LabelFrame(main, text="Settings")
        settings_frame.pack(fill="x", pady=(0,10))

        ttk.Label(settings_frame, text="Check interval (sec):").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.interval_var = tk.StringVar(value=str(self.settings.get("check_interval_seconds", 60)))
        ttk.Entry(settings_frame, textvariable=self.interval_var, width=8).grid(row=0, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(settings_frame, text="Auto-silence sound after (sec):").grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.silence_var = tk.StringVar(value=str(self.settings.get("auto_silence_seconds", 60)))
        ttk.Entry(settings_frame, textvariable=self.silence_var, width=8).grid(row=0, column=3, padx=6, pady=6, sticky="w")

        ttk.Button(settings_frame, text="Save Settings", command=self.save_settings).grid(row=0, column=4, padx=10, pady=6)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(settings_frame, textvariable=self.status_var).grid(row=0, column=5, padx=6, pady=6, sticky="e")

        for i in range(6):
            settings_frame.grid_columnconfigure(i, weight=1)

        # List section
        list_frame = ttk.LabelFrame(main, text="Alerts")
        list_frame.pack(fill="both", expand=True)

        cols = ("symbol", "target", "condition", "status")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("symbol", text="Symbol", command=lambda: self._sort_by("symbol"))
        self.tree.heading("target", text="Target Price", command=lambda: self._sort_by("target_price"))
        self.tree.heading("condition", text="Condition", command=lambda: self._sort_by("condition"))
        self.tree.heading("status", text="Status", command=lambda: self._sort_by("enabled"))
        self.tree.column("symbol", width=150, anchor="center")
        self.tree.column("target", width=150, anchor="center")
        self.tree.column("condition", width=100, anchor="center")
        self.tree.column("status", width=100, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")

        # Action buttons
        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=10)
        ttk.Button(btns, text="Edit Selected", command=self.edit_selected).pack(side="left", padx=5)
        ttk.Button(btns, text="Delete Selected", command=self.delete_selected).pack(side="left", padx=5)

        self.refresh_tree()

    # ---------- Queue from worker ----------

    def _process_queue(self):
        try:
            while True:
                kind, payload = self.task_queue.get_nowait()
                if kind == "ALARM":
                    coin_id = payload["id"]
                    sym = payload["symbol"]
                    target = payload["target"]
                    price = payload["price"]
                    # find coin, set enabled -> False (do not remove)
                    coin = next((c for c in self.coins if c["id"] == coin_id), None)
                    if coin:
                        coin["enabled"] = False
                        save_db(self.db)
                        self.refresh_tree()
                    # show alarm window
                    AlarmWindow(self, symbol=sym, target_price=target, current_price=price,
                                auto_silence_seconds=int(self.settings.get("auto_silence_seconds", 60)))
                    log_message(f"ALERT triggered for {sym} | target {target} | current {price} | disabled alert")

                elif kind == "STATUS":
                    self.status_var.set(payload)
        except queue.Empty:
            pass
        # schedule next poll
        self.after(120, self._process_queue)

    # ---------- Search & Sort ----------

    def _on_search_changed(self):
        self._filter_text = (self.search_var.get() or "").strip().upper()
        self.refresh_tree()

    def _sort_by(self, key):
        reverse = False
        if self._sort_state["column"] == key:
            reverse = not self._sort_state["reverse"]
        self._sort_state = {"column": key, "reverse": reverse}

        def sort_key(c):
            if key == "target_price":
                return float(c.get("target_price", 0))
            if key == "enabled":
                return 1 if c.get("enabled", True) else 0
            return str(c.get(key, "")).upper()

        self.coins.sort(key=sort_key, reverse=reverse)
        save_db(self.db)
        self.refresh_tree()

    # ---------- Tree helpers ----------

    def _iter_filtered(self):
        for c in self.coins:
            if not self._filter_text:
                yield c
            else:
                if self._filter_text in c.get("symbol","").upper():
                    yield c

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for coin in self._iter_filtered():
            iid = coin["id"]
            status = "Enabled" if coin.get("enabled", True) else "Disabled"
            self.tree.insert("", "end", iid=iid,
                             values=(coin["symbol"], coin["target_price"], coin["condition"], status))

    def _selected_coin(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return next((c for c in self.coins if c["id"] == iid), None)

    # ---------- CRUD & actions ----------

    def add_alert(self):
        sym_raw = self.symbol_var.get()
        price_raw = self.price_var.get()
        sym = normalize_symbol(sym_raw, self.settings.get("assume_quote", "USDT"))
        if not sym:
            messagebox.showwarning("Invalid Symbol", "Please enter a symbol (e.g., BTC or BTCUSDT).")
            return
        try:
            price = float(price_raw)
            if price <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning("Invalid Price", "Please enter a valid target price (> 0).")
            return
        cond = self.cond_var.get()
        if cond not in (">=", "<="):
            messagebox.showwarning("Invalid Condition", "Condition must be >= or <=.")
            return

        coin = {
            "id": str(uuid.uuid4()),
            "symbol": sym,
            "target_price": price,
            "condition": cond,
            "enabled": bool(self.enabled_var.get())
        }
        self.coins.append(coin)
        save_db(self.db)
        self.refresh_tree()
        self.symbol_var.set("")
        self.price_var.set("")
        self.cond_var.set(">=")
        self.enabled_var.set(True)

    def edit_selected(self):
        coin = self._selected_coin()
        if not coin:
            messagebox.showinfo("Edit", "Please select an alert to edit.")
            return
        dlg = EditCoinDialog(self, "Edit Alert", coin, assume_quote=self.settings.get("assume_quote", "USDT"))
        if dlg.result:
            coin["symbol"] = dlg.result["symbol"]
            coin["target_price"] = float(dlg.result["target_price"])
            coin["condition"] = dlg.result["condition"]
            coin["enabled"]  = bool(dlg.result["enabled"])
            save_db(self.db)
            self.refresh_tree()

    def delete_selected(self):
        coin = self._selected_coin()
        if not coin:
            messagebox.showinfo("Delete", "Please select an alert to delete.")
            return
        self.coins = [c for c in self.coins if c["id"] != coin["id"]]
        self.db["coins"] = self.coins
        save_db(self.db)
        self.refresh_tree()

    def enable_selected(self):
        coin = self._selected_coin()
        if not coin:
            messagebox.showinfo("Enable", "Please select an alert first.")
            return
        coin["enabled"] = True
        save_db(self.db)
        self.refresh_tree()

    def disable_selected(self):
        coin = self._selected_coin()
        if not coin:
            messagebox.showinfo("Disable", "Please select an alert first.")
            return
        coin["enabled"] = False
        save_db(self.db)
        self.refresh_tree()

    def manual_refresh(self):
        # Force an immediate light check: spawn a one-off worker that checks enabled alerts quickly
        threading.Thread(target=self._manual_check_once, daemon=True).start()

    def _manual_check_once(self):
        errors = 0
        for coin in list(self.coins):
            if not coin.get("enabled", True):
                continue
            try:
                price = fetch_price_multi(coin["symbol"])
            except Exception as e:
                errors += 1
                log_message(f"Manual fetch error {coin['symbol']}: {e}")
                continue
            cond = coin.get("condition", ">=")
            target = float(coin["target_price"])
            hit = (price >= target) if cond == ">=" else (price <= target)
            if hit:
                self.task_queue.put(("ALARM", {"id": coin["id"], "symbol": coin["symbol"], "target": target, "price": price}))
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        msg = f"Manual check {ts} — {'OK' if errors==0 else str(errors)+' error(s)'}."
        self.task_queue.put(("STATUS", msg))

    # ---------- Settings ----------

    def save_settings(self):
        try:
            interval = int(float(self.interval_var.get()))
            silence = int(float(self.silence_var.get()))
            if interval < 10:
                raise ValueError("Interval too short (< 10s).")
            if silence < 1:
                raise ValueError("Auto-silence must be >= 1.")
        except Exception as e:
            messagebox.showwarning("Invalid Settings", f"Please enter valid numbers.\n{e}")
            return
        self.settings["check_interval_seconds"] = interval
        self.settings["auto_silence_seconds"] = silence
        save_db(self.db)
        self.status_var.set("Settings saved.")

    # ---------- Close ----------

    def destroy(self):
        try:
            if hasattr(self, "worker") and self.worker:
                self.worker.stop()
        except Exception:
            pass
        super().destroy()

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
