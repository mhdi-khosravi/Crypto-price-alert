# Crypto-price-alert
a simple python windows app to track price of crypto symbols and show alarm if price reach the target
# Crypto Price Alert (Modern UI, Bilingual)

A desktop application for **Windows** to track cryptocurrency prices and trigger alarms when target prices are reached.  
Built with **Python** and **pystray** for system tray integration.

---

## ✨ Features

- **Multi-exchange price fetching**: Binance, Bybit, Coinbase, Upbit, OKX, Bitunix
- **Add/edit/delete alerts** for any coin (default quote is USDT)
- **Background tray mode**: App hides instead of closing, accessible from tray near the clock
- **Alarm window with sound** when target is reached (sound auto-silences after X seconds, but window stays open)
- **Bilingual support**: English / فارسی (switch live)
- **Persistent settings** stored in `crypto_alerts.json`
- **Logging**: saves events and errors in `log.txt`

---

## 📦 Requirements

- Python 3.9+
- Packages:
  ```bash
  pip install pystray pillow
  ```

---

## ▶️ Run from source

1. Clone or download this repo.
2. Extract the files, you should see:
   - `main.py`
   - `lang_en.json`
   - `lang_fa.json`
3. Run:
   ```bash
   python main.py
   ```

---

## 🖥 Build Windows Executable

You can create a portable `.exe` using **PyInstaller**:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed main.py
```

The output will be in the `dist/` folder.  
You can compress the `.exe` into a `.zip` and upload it to GitHub Releases.

---

## 🔧 Usage Guide

### Adding Alerts
- Enter a symbol (`BTC` or `BTCUSDT`), a target price, and choose condition (`>=` or `<=`).
- Click **Add Alert**.
- The alert appears in the **Active Alerts** list.

### Editing / Deleting Alerts
- Select an alert in the list.
- Click **Edit Selected** or **Delete Selected**.

### When Price Reaches Target
- A popup alarm window appears.
- A beep sound plays (auto-silenced after the configured time).
- The alarm stays open until closed manually.

### Settings
- **Check interval (sec):** How often the app fetches prices (default: 60).
- **Auto-silence (sec):** How long the alarm sound continues before stopping automatically.

### Tray Icon
- Closing the main window (`X`) hides it to the tray (near the clock).
- Right-click the tray icon → options to **Open**, **Show/Hide**, **Exit**.

### Language & Theme
- Top-right button switches between **English** and **فارسی**.

---

## 📂 Files

- `main.py` — Main program
- `lang_en.json` — English text
- `lang_fa.json` — Persian text
- `crypto_alerts.json` — Created on first run; stores alerts & settings
- `log.txt` — Event/error log

---

## ⚠️ Notes

- Some exchanges may be blocked in restricted regions (e.g., Iran). In that case, use a VPN.
- Default quote currency is **USDT**. Typing `BTC` is normalized to `BTCUSDT` automatically.
- Tray integration requires **pystray** and **Pillow** to be installed.

---

## 📜 License

This project is released under the MIT License.  
Feel free to fork, modify, and contribute!

---

## 👨‍💻 Author

Crypto Price Alert Modern UI by **Mhdi**  
Contributions and pull requests are welcome!
