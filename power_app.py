"""
Power Control - Десктопное приложение с красивым интерфейсом
Запуск: python power_app.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import webbrowser
import socket
import sys
import os
import json
import subprocess
import mimetypes
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    import pystray
    from PIL import Image, ImageDraw
    import qrcode
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
# НАСТРОЙКИ СЕРВЕРА
# ═══════════════════════════════════════════════════════════════════════════

PORT = int(os.environ.get("POWER_PORT", "8765"))
SECRET_TOKEN = os.environ.get("POWER_TOKEN", "marsela")

WOL_COMPUTERS = {
    "my_pc": {
        "name": "Мой ПК",
        "mac": "90-1B-0E-1A-FB-D8",
        "broadcast": "192.168.89.255",
        "port": 9,
    }
}

SHUTDOWN_EXE = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe")
RUNDLL32_EXE = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "rundll32.exe")

# Пути к статическим файлам
if getattr(sys, 'frozen', False):
    # Если запущен как EXE
    SCRIPT_DIR = Path(sys._MEIPASS)
else:
    # Если запущен как скрипт
    SCRIPT_DIR = Path(__file__).parent

STATIC_DIR = SCRIPT_DIR / "static"
TEMPLATES_DIR = SCRIPT_DIR / "templates"


# ═══════════════════════════════════════════════════════════════════════════
# HTTP СЕРВЕР
# ═══════════════════════════════════════════════════════════════════════════

def send_magic_packet(mac: str, broadcast: str, port: int = 9):
    """Отправить Wake-on-LAN пакет"""
    mac_clean = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(mac_clean) != 12:
        raise ValueError(f"Неверный MAC-адрес: {mac}")
    
    mac_bytes = bytes.fromhex(mac_clean)
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


class PowerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Отключаем логи в консоль
    
    def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")
    
    def _token_ok(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        token = q.get("token", [""])[0]
        return token == SECRET_TOKEN
    
    def _shutdown_now(self):
        return subprocess.Popen([SHUTDOWN_EXE, "/s", "/t", "0"])
    
    def _sleep_now(self):
        return subprocess.Popen([RUNDLL32_EXE, "powrprof.dll,SetSuspendState", "0,1,0"])
    
    def do_GET(self):
        # Парсим путь без query параметров
        parsed_path = urlparse(self.path).path
        
        if parsed_path == "/" or parsed_path.startswith("/index.html"):
            template_path = TEMPLATES_DIR / "index.html"
            if template_path.exists():
                with open(template_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                # Встроенный HTML если файл не найден
                html = """<!DOCTYPE html><html><body><h1>Power Control</h1><p>Template not found</p></body></html>"""
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        
        if self.path.startswith("/static/"):
            file_path = SCRIPT_DIR / self.path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(file_path))
                mime_type = mime_type or "application/octet-stream"
                with open(file_path, "rb") as f:
                    self._send(200, f.read(), mime_type)
                return
            else:
                self._send(404, b"Not Found")
                return
        
        if self.path.startswith("/health"):
            self._send(200, b"OK")
            return
        
        if self.path.startswith("/shutdown"):
            if not self._token_ok():
                self._send(403, "Forbidden".encode("utf-8"))
                return
            self._send(200, "OK, shutting down".encode("utf-8"))
            try:
                self._shutdown_now()
            except Exception:
                pass
            return
        
        if self.path.startswith("/sleep"):
            if not self._token_ok():
                self._send(403, "Forbidden".encode("utf-8"))
                return
            self._send(200, "OK, going to sleep".encode("utf-8"))
            try:
                self._sleep_now()
            except Exception:
                pass
            return
        
        if self.path.startswith("/favicon.ico"):
            self._send(204, b"")
            return
        
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()
    
    def do_POST(self):
        if self.path not in ("/api/shutdown", "/api/sleep", "/api/wake"):
            self._send_json(404, {"message": "Не найдено"})
            return
        
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"message": "Неверный JSON"})
            return
        
        token = str(body.get("token", "")).strip()
        if token != SECRET_TOKEN:
            self._send_json(403, {"message": "Неверный токен"})
            return
        
        if self.path == "/api/wake":
            pc_id = str(body.get("pc_id", "")).strip() or "my_pc"
            if pc_id not in WOL_COMPUTERS:
                self._send_json(404, {"message": f"ПК '{pc_id}' не найден"})
                return
            pc = WOL_COMPUTERS[pc_id]
            try:
                send_magic_packet(pc["mac"], pc["broadcast"], int(pc.get("port", 9)))
                self._send_json(200, {"message": f"WoL отправлен: {pc.get('name', pc_id)}"})
            except Exception as e:
                self._send_json(500, {"message": f"Ошибка WoL: {e}"})
            return
        
        if self.path == "/api/shutdown":
            self._send_json(200, {"message": "Команда отправлена. ПК выключается…"})
            try:
                self._shutdown_now()
            except Exception as e:
                print(f"[shutdown] error: {e}")
            return
        
        if self.path == "/api/sleep":
            self._send_json(200, {"message": "Команда отправлена. ПК уходит в сон…"})
            try:
                self._sleep_now()
            except Exception as e:
                print(f"[sleep] error: {e}")
            return


# ═══════════════════════════════════════════════════════════════════════════
# GUI ПРИЛОЖЕНИЕ
# ═══════════════════════════════════════════════════════════════════════════


def get_local_ip():
    """Получить локальный IP адрес"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def create_tray_icon():
    """Создать иконку для трея"""
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), (102, 126, 234))
    dc = ImageDraw.Draw(image)
    
    dc.ellipse([16, 16, 48, 48], outline=(255, 255, 255), width=4)
    dc.rectangle([30, 12, 34, 32], fill=(255, 255, 255))
    
    return image


class PowerControlApp:
    def __init__(self, start_minimized=False):
        self.root = tk.Tk()
        self.root.title("Power Control")
        self.root.geometry("520x920")
        self.root.resizable(False, False)
        
        # Флаг запуска в трее
        self.start_minimized = start_minimized
        
        # Путь к файлу конфигурации
        self.config_file = Path.home() / ".power_control_config.json"
        
        # Загружаем сохраненные настройки (обновляет глобальные переменные)
        self.load_config()
        
        # Темная/светлая тема
        self.is_dark_theme = False
        self.setup_theme()
        
        self.root.configure(bg=self.bg_color)
        
        # Сервер
        self.server = None
        self.server_thread = None
        self.server_running = False
        self.local_ip = get_local_ip()
        self.current_token = SECRET_TOKEN  # Текущий токен (уже загружен из конфига)
        self.current_port = PORT  # Текущий порт (уже загружен из конфига)
        
        # Трей
        self.tray_icon = None
        
        # Флаги редактирования
        self.is_editing_token = False
        self.is_editing_port = False
        
        # Автозапуск
        self.autostart_enabled = self.check_autostart()
        
        # Создаем интерфейс
        self.create_widgets()
        
        # Обработка закрытия
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def setup_theme(self):
        """Настроить цветовую тему"""
        if self.is_dark_theme:
            # Темная тема
            self.bg_color = "#1c1c1e"
            self.card_color = "#2c2c2e"
            self.accent_color = "#0a84ff"
            self.text_color = "#ffffff"
            self.text_secondary = "#98989d"
            self.success_color = "#30d158"
            self.danger_color = "#ff453a"
            self.border_color = "#38383a"
        else:
            # Светлая тема
            self.bg_color = "#f5f5f7"
            self.card_color = "#ffffff"
            self.accent_color = "#007aff"
            self.text_color = "#1d1d1f"
            self.text_secondary = "#86868b"
            self.success_color = "#34c759"
            self.danger_color = "#ff3b30"
            self.border_color = "#d2d2d7"
    
    def load_config(self):
        """Загрузить сохраненные настройки"""
        global SECRET_TOKEN, PORT
        
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                    # Загружаем токен
                    saved_token = config.get('token', SECRET_TOKEN)
                    SECRET_TOKEN = saved_token
                    
                    # Загружаем порт
                    saved_port = config.get('port', PORT)
                    PORT = saved_port
                    
                    print(f"Настройки загружены: токен={saved_token}, порт={saved_port}")
        except Exception as e:
            print(f"Ошибка загрузки настроек: {e}")
    
    def save_config(self):
        """Сохранить настройки"""
        try:
            config = {
                'token': self.current_token,
                'port': self.current_port
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"Настройки сохранены: токен={self.current_token}, порт={self.current_port}")
        except Exception as e:
            print(f"Ошибка сохранения настроек: {e}")
    
    def toggle_theme(self):
        """Переключить тему"""
        self.is_dark_theme = not self.is_dark_theme
        self.setup_theme()
        
        # Сохраняем состояние сервера и другие данные
        was_running = self.server_running
        saved_server = self.server
        saved_thread = self.server_thread
        
        # Пересоздаем интерфейс
        for widget in self.root.winfo_children():
            widget.destroy()
        
        self.root.configure(bg=self.bg_color)
        
        # Восстанавливаем состояние сервера перед созданием виджетов
        if was_running:
            self.server = saved_server
            self.server_thread = saved_thread
            self.server_running = True
        
        self.create_widgets()
        
        # Обновляем UI если сервер был запущен
        if was_running:
            self.status_indicator.config(text="● Работает", fg=self.success_color)
            self.toggle_button.config(
                text="Остановить сервер",
                bg=self.danger_color,
                fg="white",
                activebackground="#d92b20"
            )
    
    def generate_qr_code(self):
        """Генерировать QR-код с URL"""
        url = f"http://{self.local_ip}:{self.current_port}?token={self.current_token}"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color=self.text_color, back_color=self.card_color)
        return img
    
    def update_qr_code(self):
        """Обновить QR-код"""
        qr_img = self.generate_qr_code()
        qr_img = qr_img.resize((180, 180))
        
        from PIL import ImageTk
        self.qr_photo = ImageTk.PhotoImage(qr_img)
        
        # Обновляем QR-код label
        if hasattr(self, 'qr_label') and self.qr_label.winfo_exists():
            self.qr_label.config(image=self.qr_photo)
            self.qr_label.image = self.qr_photo
    
    def show_qr_code(self):
        """Показать QR-код в отдельном окне"""
        qr_window = tk.Toplevel(self.root)
        qr_window.title("QR-код для подключения")
        qr_window.geometry("350x420")
        qr_window.resizable(False, False)
        qr_window.configure(bg=self.bg_color)
        qr_window.transient(self.root)
        
        # Центрируем окно
        qr_window.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - qr_window.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - qr_window.winfo_height()) // 2
        qr_window.geometry(f"+{x}+{y}")
        
        # Заголовок
        title = tk.Label(
            qr_window,
            text="Отсканируйте QR-код",
            font=("SF Pro Text", 16, "bold"),
            bg=self.bg_color,
            fg=self.text_color
        )
        title.pack(pady=(20, 10))
        
        # QR-код
        qr_img = self.generate_qr_code()
        qr_img = qr_img.resize((280, 280))
        
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(qr_img)
        
        qr_label = tk.Label(qr_window, image=photo, bg=self.bg_color)
        qr_label.image = photo
        qr_label.pack(pady=10)
        
        # URL под QR-кодом
        url_text = tk.Label(
            qr_window,
            text=f"http://{self.local_ip}:{self.current_port}",
            font=("SF Pro Text", 12),
            bg=self.bg_color,
            fg=self.text_secondary
        )
        url_text.pack(pady=(5, 10))
        
        # Кнопка закрыть
        close_btn = tk.Button(
            qr_window,
            text="Закрыть",
            font=("SF Pro Text", 14),
            bg=self.accent_color,
            fg="white",
            activebackground="#0051d5",
            activeforeground="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=qr_window.destroy,
            bd=0,
            highlightthickness=0
        )
        close_btn.pack(pady=(0, 20), padx=40, fill=tk.X)
        close_btn.config(height=2)
        
    def create_widgets(self):
        """Создать интерфейс в стиле Apple"""
        
        # Отступ сверху и кнопка темы
        top_frame = tk.Frame(self.root, bg=self.bg_color, height=50)
        top_frame.pack(fill=tk.X)
        top_frame.pack_propagate(False)
        
        # Кнопка переключения темы в правом верхнем углу (поверх всех элементов)
        self.theme_button = tk.Label(
            self.root,
            text="☾" if not self.is_dark_theme else "☼",
            font=("SF Pro Text", 26),
            bg=self.bg_color,
            fg=self.text_color,
            cursor="hand2"
        )
        self.theme_button.place(x=480, y=10, anchor=tk.NE)
        self.theme_button.bind("<Button-1>", lambda e: self.toggle_theme())
        
        # Заголовок
        title_label = tk.Label(
            self.root,
            text="Power Control",
            font=("SF Pro Display", 32, "bold"),
            bg=self.bg_color,
            fg=self.text_color
        )
        title_label.pack(pady=(0, 8))
        
        subtitle_label = tk.Label(
            self.root,
            text="Управление питанием ПК",
            font=("SF Pro Text", 13),
            bg=self.bg_color,
            fg=self.text_secondary
        )
        subtitle_label.pack(pady=(0, 30))
        
        # Карточка с информацией
        info_card = tk.Frame(
            self.root, 
            bg=self.card_color,
            highlightbackground=self.border_color,
            highlightthickness=1
        )
        info_card.pack(fill=tk.BOTH, padx=30, pady=(0, 20))
        
        # Внутренний контейнер с отступами
        info_inner = tk.Frame(info_card, bg=self.card_color)
        info_inner.pack(fill=tk.BOTH, padx=24, pady=24)
        
        # IP адрес и порт
        self.ip_port_frame = tk.Frame(info_inner, bg=self.card_color)
        self.ip_port_frame.pack(anchor=tk.W, fill=tk.X)
        
        ip_port_label_frame = tk.Frame(self.ip_port_frame, bg=self.card_color)
        ip_port_label_frame.pack(anchor=tk.W)
        
        ip_port_label = tk.Label(
            ip_port_label_frame,
            text="IP адрес",
            font=("SF Pro Text", 13),
            bg=self.card_color,
            fg=self.text_secondary
        )
        ip_port_label.pack(side=tk.LEFT)
        
        self._create_tooltip(ip_port_label, "Адрес вашего ПК в локальной сети")
        
        # Кнопка изменить порт
        self.edit_port_btn = tk.Label(
            ip_port_label_frame,
            text="изменить порт",
            font=("SF Pro Text", 12),
            bg=self.card_color,
            fg=self.accent_color,
            cursor="hand2"
        )
        self.edit_port_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.edit_port_btn.bind("<Button-1>", lambda e: self.start_edit_port())
        
        # Label для отображения IP:порт
        self.ip_port_value = tk.Label(
            self.ip_port_frame,
            text=f"{self.local_ip}:{self.current_port}",
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color
        )
        self.ip_port_value.pack(anchor=tk.W, pady=(4, 0))
        
        # Entry для редактирования порта (скрыт по умолчанию)
        port_edit_frame = tk.Frame(self.ip_port_frame, bg=self.card_color)
        
        tk.Label(
            port_edit_frame,
            text=f"{self.local_ip}:",
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color
        ).pack(side=tk.LEFT)
        
        self.port_entry = tk.Entry(
            port_edit_frame,
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color,
            relief=tk.SOLID,
            bd=1,
            insertbackground=self.text_color,
            width=6
        )
        self.port_entry.pack(side=tk.LEFT)
        self.port_entry.bind("<Return>", lambda e: self.save_port())
        self.port_entry.bind("<Escape>", lambda e: self.cancel_edit_port())
        self.port_entry.bind("<FocusOut>", lambda e: self.save_port())
        
        self.port_edit_frame = port_edit_frame
        
        # Разделитель
        tk.Frame(info_inner, bg=self.border_color, height=1).pack(fill=tk.X, pady=16)
        
        # URL с QR-кодом
        self.url_frame = tk.Frame(info_inner, bg=self.card_color)
        self.url_frame.pack(anchor=tk.W, fill=tk.X)
        
        url_label = tk.Label(
            self.url_frame,
            text="URL для телефона",
            font=("SF Pro Text", 13),
            bg=self.card_color,
            fg=self.text_secondary
        )
        url_label.pack(anchor=tk.W)
        
        self._create_tooltip(url_label, "Откройте этот адрес на телефоне в той же Wi-Fi сети")
        
        # QR-код
        qr_img = self.generate_qr_code()
        qr_img = qr_img.resize((180, 180))
        
        from PIL import ImageTk
        self.qr_photo = ImageTk.PhotoImage(qr_img)
        
        self.qr_label = tk.Label(self.url_frame, image=self.qr_photo, bg=self.card_color)
        self.qr_label.image = self.qr_photo  # Сохраняем ссылку
        self.qr_label.pack(pady=(10, 10))
        
        # URL под QR-кодом (кликабельный для копирования)
        self.url_value = tk.Label(
            self.url_frame,
            text=f"http://{self.local_ip}:{self.current_port}?token={self.current_token}",
            font=("SF Pro Text", 15),
            bg=self.card_color,
            fg=self.accent_color,
            cursor="hand2"
        )
        self.url_value.pack(anchor=tk.CENTER, pady=(0, 5))
        self.url_value.bind("<Button-1>", lambda e: self.copy_url())
        self._create_tooltip(self.url_value, "Клик - копировать URL")
        
        # Разделитель
        tk.Frame(info_inner, bg=self.border_color, height=1).pack(fill=tk.X, pady=16)
        
        # Токен с возможностью редактирования
        self.token_frame = tk.Frame(info_inner, bg=self.card_color)
        self.token_frame.pack(anchor=tk.W, fill=tk.X)
        
        token_label_frame = tk.Frame(self.token_frame, bg=self.card_color)
        token_label_frame.pack(anchor=tk.W)
        
        token_label = tk.Label(
            token_label_frame,
            text="Токен",
            font=("SF Pro Text", 13),
            bg=self.card_color,
            fg=self.text_secondary
        )
        token_label.pack(side=tk.LEFT)
        
        self._create_tooltip(token_label, "Пароль для доступа к управлению. Введите его на телефоне")
        
        # Кнопка изменить
        self.edit_token_btn = tk.Label(
            token_label_frame,
            text="изменить",
            font=("SF Pro Text", 12),
            bg=self.card_color,
            fg=self.accent_color,
            cursor="hand2"
        )
        self.edit_token_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.edit_token_btn.bind("<Button-1>", lambda e: self.start_edit_token())
        
        # Label для отображения токена
        self.token_value = tk.Label(
            self.token_frame,
            text=self.current_token,
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color
        )
        self.token_value.pack(anchor=tk.W, pady=(4, 0))
        
        # Entry для редактирования (скрыт по умолчанию)
        self.token_entry = tk.Entry(
            self.token_frame,
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color,
            relief=tk.SOLID,
            bd=1,
            insertbackground=self.text_color
        )
        self.token_entry.bind("<Return>", lambda e: self.save_token())
        self.token_entry.bind("<Escape>", lambda e: self.cancel_edit_token())
        self.token_entry.bind("<FocusOut>", lambda e: self.save_token())
        
        # Статус сервера (компактный, в одну строку)
        status_card = tk.Frame(
            self.root,
            bg=self.card_color,
            highlightbackground=self.border_color,
            highlightthickness=1
        )
        status_card.pack(fill=tk.X, padx=30, pady=(0, 16))
        
        status_inner = tk.Frame(status_card, bg=self.card_color)
        status_inner.pack(fill=tk.BOTH, padx=20, pady=10)
        
        # Статус в одну строку
        status_row = tk.Frame(status_inner, bg=self.card_color)
        status_row.pack(fill=tk.X)
        
        status_label = tk.Label(
            status_row,
            text="Статус",
            font=("SF Pro Text", 14),
            bg=self.card_color,
            fg=self.text_secondary
        )
        status_label.pack(side=tk.LEFT)
        
        self.status_indicator = tk.Label(
            status_row,
            text="● Остановлен",
            font=("SF Pro Text", 14),
            bg=self.card_color,
            fg=self.danger_color
        )
        self.status_indicator.pack(side=tk.RIGHT)
        
        # Автозапуск с Windows (под статусом)
        autostart_card = tk.Frame(
            self.root,
            bg=self.card_color,
            highlightbackground=self.border_color,
            highlightthickness=1
        )
        autostart_card.pack(fill=tk.X, padx=30, pady=(0, 16))
        
        autostart_inner = tk.Frame(autostart_card, bg=self.card_color)
        autostart_inner.pack(fill=tk.BOTH, padx=20, pady=10)
        
        autostart_row = tk.Frame(autostart_inner, bg=self.card_color)
        autostart_row.pack(fill=tk.X)
        
        autostart_label = tk.Label(
            autostart_row,
            text="Запускать с Windows",
            font=("SF Pro Text", 14),
            bg=self.card_color,
            fg=self.text_color
        )
        autostart_label.pack(side=tk.LEFT)
        
        self.autostart_var = tk.BooleanVar(value=self.autostart_enabled)
        
        # Кастомный чекбокс - квадрат с галочкой
        self.autostart_toggle = tk.Canvas(
            autostart_row,
            width=24,
            height=24,
            bg=self.card_color,
            highlightthickness=0,
            cursor="hand2"
        )
        self.autostart_toggle.pack(side=tk.RIGHT)
        
        # Рисуем чекбокс
        self._draw_checkbox(self.autostart_toggle, self.autostart_enabled)
        
        # Обработчик клика
        self.autostart_toggle.bind("<Button-1>", lambda e: self._toggle_autostart_switch())
        
        # Кнопки управления
        buttons_frame = tk.Frame(self.root, bg=self.bg_color)
        buttons_frame.pack(fill=tk.X, padx=30, pady=(0, 16))
        
        # Одна большая кнопка для запуска/остановки
        self.toggle_button = tk.Button(
            buttons_frame,
            text="Запустить сервер",
            font=("SF Pro Text", 16, "bold"),
            bg=self.accent_color,
            fg="white",
            activebackground="#0051d5",
            activeforeground="white",
            relief=tk.FLAT,
            cursor="hand2",
            command=self.toggle_server,
            bd=0,
            highlightthickness=0,
            disabledforeground="white"
        )
        self.toggle_button.pack(fill=tk.BOTH, ipady=14)
        
        # Подсказка внизу
        hint_label = tk.Label(
            self.root,
            text="Откройте URL на телефоне в той же Wi-Fi сети",
            font=("SF Pro Text", 11),
            bg=self.bg_color,
            fg=self.text_secondary
        )
        hint_label.pack(pady=(20, 0))
    
    def _create_info_row(self, parent, label_text, value_text, clickable=False, tooltip=None):
        """Создать строку с информацией"""
        row_frame = tk.Frame(parent, bg=self.card_color)
        row_frame.pack(anchor=tk.W, fill=tk.X)
        
        label = tk.Label(
            row_frame,
            text=label_text,
            font=("SF Pro Text", 13),
            bg=self.card_color,
            fg=self.text_secondary
        )
        label.pack(anchor=tk.W)
        
        # Добавляем подсказку при наведении
        if tooltip:
            self._create_tooltip(label, tooltip)
        
        value = tk.Label(
            row_frame,
            text=value_text,
            font=("SF Pro Text", 17),
            bg=self.card_color,
            fg=self.text_color
        )
        value.pack(anchor=tk.W, pady=(4, 0))
        
        if clickable:
            value.config(cursor="hand2", fg=self.accent_color)
            value.bind("<Button-1>", lambda e: self.copy_url())
            if tooltip:
                self._create_tooltip(value, tooltip)
        
        return value
    
    def _create_tooltip(self, widget, text):
        """Создать всплывающую подсказку"""
        def show_tooltip(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            
            label = tk.Label(
                tooltip,
                text=text,
                background="#1d1d1f",
                foreground="white",
                relief=tk.FLAT,
                borderwidth=0,
                font=("SF Pro Text", 11),
                padx=12,
                pady=8
            )
            label.pack()
            
            widget.tooltip = tooltip
        
        def hide_tooltip(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()
                del widget.tooltip
        
        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)
    
    def _draw_checkbox(self, canvas, is_checked):
        """Нарисовать квадратный чекбокс с галочкой"""
        canvas.delete("all")
        
        # Рисуем квадрат
        if is_checked:
            # Заполненный квадрат с галочкой
            canvas.create_rectangle(0, 0, 24, 24, fill=self.accent_color, outline="", width=0)
            # Галочка
            canvas.create_line(5, 12, 10, 17, fill="white", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
            canvas.create_line(10, 17, 19, 7, fill="white", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        else:
            # Пустой квадрат с рамкой
            canvas.create_rectangle(0, 0, 24, 24, fill=self.card_color, outline=self.border_color, width=2)
    
    def _toggle_autostart_switch(self):
        """Переключить автозапуск через кастомный чекбокс"""
        self.autostart_enabled = not self.autostart_enabled
        self.autostart_var.set(self.autostart_enabled)
        self._draw_checkbox(self.autostart_toggle, self.autostart_enabled)
        
        # Применяем изменения в реестре
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            
            if self.autostart_enabled:
                # Добавляем в автозагрузку с параметром --minimized
                if getattr(sys, 'frozen', False):
                    exe_path = f'"{sys.executable}" --minimized'
                else:
                    python_path = sys.executable
                    script_path = Path(__file__).absolute()
                    exe_path = f'"{python_path}" "{script_path}" --minimized'
                
                winreg.SetValueEx(key, "PowerControl", 0, winreg.REG_SZ, exe_path)
            else:
                # Удаляем из автозагрузки
                try:
                    winreg.DeleteValue(key, "PowerControl")
                except FileNotFoundError:
                    pass
            
            winreg.CloseKey(key)
        except Exception as e:
            print(f"Ошибка автозапуска: {e}")
    
    def check_autostart(self):
        """Проверить включен ли автозапуск"""
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "PowerControl")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False
    
    def toggle_autostart(self):
        """Переключить автозапуск"""
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            
            if self.autostart_var.get():
                # Добавляем в автозагрузку
                if getattr(sys, 'frozen', False):
                    # Если запущен как EXE
                    exe_path = sys.executable
                else:
                    # Если запущен как скрипт
                    python_path = sys.executable
                    script_path = Path(__file__).absolute()
                    exe_path = f'"{python_path}" "{script_path}"'
                
                winreg.SetValueEx(key, "PowerControl", 0, winreg.REG_SZ, exe_path)
            else:
                # Удаляем из автозагрузки
                try:
                    winreg.DeleteValue(key, "PowerControl")
                except FileNotFoundError:
                    pass
            
            winreg.CloseKey(key)
        except Exception as e:
            print(f"Ошибка автозапуска: {e}")
    
    def start_edit_port(self):
        """Начать редактирование порта"""
        if self.is_editing_port or self.server_running:
            return
        
        self.is_editing_port = True
        
        # Скрываем label, показываем entry
        self.ip_port_value.pack_forget()
        self.port_entry.delete(0, tk.END)
        self.port_entry.insert(0, str(self.current_port))
        self.port_edit_frame.pack(anchor=tk.W, pady=(4, 0))
        self.port_entry.focus()
        self.port_entry.select_range(0, tk.END)
        
        # Меняем кнопку на "сохранить"
        self.edit_port_btn.config(text="сохранить")
        self.edit_port_btn.unbind("<Button-1>")
        self.edit_port_btn.bind("<Button-1>", lambda e: self.save_port())
    
    def save_port(self):
        """Сохранить новый порт"""
        if not self.is_editing_port:
            return
        
        try:
            new_port = int(self.port_entry.get().strip())
            if 1024 <= new_port <= 65535:
                global PORT
                PORT = new_port
                self.current_port = new_port
                
                # Обновляем отображение
                self.ip_port_value.config(text=f"{self.local_ip}:{self.current_port}")
                self.url_value.config(text=f"http://{self.local_ip}:{self.current_port}?token={self.current_token}")
                
                # Обновляем QR-код
                self.update_qr_code()
                
                # Сохраняем конфигурацию
                self.save_config()
        except ValueError:
            pass
        
        self.cancel_edit_port()
    
    def cancel_edit_port(self):
        """Отменить редактирование порта"""
        if not self.is_editing_port:
            return
        
        self.is_editing_port = False
        
        # Скрываем entry, показываем label
        self.port_edit_frame.pack_forget()
        self.ip_port_value.pack(anchor=tk.W, pady=(4, 0))
        
        # Возвращаем кнопку "изменить порт"
        self.edit_port_btn.config(text="изменить порт")
        self.edit_port_btn.unbind("<Button-1>")
        self.edit_port_btn.bind("<Button-1>", lambda e: self.start_edit_port())
        
    def start_edit_token(self):
        """Начать редактирование токена"""
        if self.is_editing_token:
            return
        
        self.is_editing_token = True
        
        # Скрываем label, показываем entry
        self.token_value.pack_forget()
        self.token_entry.delete(0, tk.END)
        self.token_entry.insert(0, self.current_token)
        self.token_entry.pack(anchor=tk.W, pady=(4, 0), fill=tk.X)
        self.token_entry.focus()
        self.token_entry.select_range(0, tk.END)
        
        # Меняем кнопку на "сохранить"
        self.edit_token_btn.config(text="сохранить")
        self.edit_token_btn.unbind("<Button-1>")
        self.edit_token_btn.bind("<Button-1>", lambda e: self.save_token())
    
    def save_token(self):
        """Сохранить новый токен"""
        if not self.is_editing_token:
            return
        
        new_token = self.token_entry.get().strip()
        if new_token:
            global SECRET_TOKEN
            SECRET_TOKEN = new_token
            self.current_token = new_token
            self.token_value.config(text=new_token)
            
            # Обновляем URL с новым токеном
            self.url_value.config(text=f"http://{self.local_ip}:{self.current_port}?token={self.current_token}")
            
            # Обновляем QR-код с новым токеном
            self.update_qr_code()
            
            # Сохраняем конфигурацию
            self.save_config()
            
            # Если сервер запущен, перезапускаем
            if self.server_running:
                self.stop_server()
                self.root.after(100, self.start_server)
        
        self.cancel_edit_token()
    
    def cancel_edit_token(self):
        """Отменить редактирование токена"""
        if not self.is_editing_token:
            return
        
        self.is_editing_token = False
        
        # Скрываем entry, показываем label
        self.token_entry.pack_forget()
        self.token_value.pack(anchor=tk.W, pady=(4, 0))
        
        # Возвращаем кнопку "изменить"
        self.edit_token_btn.config(text="изменить")
        self.edit_token_btn.unbind("<Button-1>")
        self.edit_token_btn.bind("<Button-1>", lambda e: self.start_edit_token())
    
    def toggle_server(self):
        """Переключить состояние сервера"""
        if self.server_running:
            self.stop_server()
        else:
            self.start_server()
    
    def start_server(self):
        """Запустить сервер"""
        if not self.server_running:
            try:
                self.server = HTTPServer(("0.0.0.0", self.current_port), PowerHandler)
                self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                self.server_thread.start()
                
                self.server_running = True
                self.status_indicator.config(text="● Работает", fg=self.success_color)
                
                # Меняем кнопку на красную с текстом "Остановить"
                self.toggle_button.config(
                    text="Остановить сервер",
                    bg=self.danger_color,
                    fg="white",
                    activebackground="#d92b20"
                )
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось запустить сервер:\n{e}")
    
    def stop_server(self):
        """Остановить сервер"""
        if self.server_running:
            try:
                self.server.shutdown()
                self.server = None
                self.server_running = False
                
                self.status_indicator.config(text="● Остановлен", fg=self.danger_color)
                
                # Меняем кнопку обратно на синюю с текстом "Запустить"
                self.toggle_button.config(
                    text="Запустить сервер",
                    bg=self.accent_color,
                    fg="white",
                    activebackground="#0051d5"
                )
            except Exception as e:
                messagebox.showerror("Ошибка", f"Ошибка остановки сервера:\n{e}")
    
    def open_browser(self):
        """Открыть браузер"""
        webbrowser.open(f"http://{self.local_ip}:{self.current_port}?token={self.current_token}")
    
    def copy_url(self):
        """Копировать URL"""
        url = f"http://{self.local_ip}:{self.current_port}?token={self.current_token}"
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update()
    
    def minimize_to_tray(self):
        """Свернуть в трей"""
        if TRAY_AVAILABLE:
            self.root.withdraw()
            
            menu = pystray.Menu(
                pystray.MenuItem("Показать окно", self.show_window, default=True),
                pystray.MenuItem("Открыть в браузере", lambda: self.open_browser()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", self.quit_app)
            )
            
            self.tray_icon = pystray.Icon(
                "power_control",
                create_tray_icon(),
                "Power Control",
                menu
            )
            
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
    
    def show_window(self, icon=None, item=None):
        """Показать окно"""
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.deiconify()
    
    def on_closing(self):
        """Обработка закрытия окна"""
        if TRAY_AVAILABLE:
            if messagebox.askyesno("Свернуть в трей?", "Свернуть приложение в системный трей?\n\n(Нажмите 'Нет' для полного выхода)"):
                self.minimize_to_tray()
                return
        
        self.quit_app()
    
    def quit_app(self, icon=None, item=None):
        """Выход из приложения"""
        if self.server_running:
            self.stop_server()
        
        if self.tray_icon:
            self.tray_icon.stop()
        
        self.root.quit()
        sys.exit(0)
    
    def run(self):
        """Запустить приложение"""
        # Автоматически запускаем сервер при старте
        self.root.after(500, self.toggle_server)
        
        # Если запущено с флагом minimized, сразу сворачиваем в трей
        if self.start_minimized and TRAY_AVAILABLE:
            self.root.after(1000, self.minimize_to_tray)
        
        self.root.mainloop()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Power Control - Управление питанием ПК')
    parser.add_argument('--minimized', action='store_true', help='Запустить свернутым в трей')
    args = parser.parse_args()
    
    app = PowerControlApp(start_minimized=args.minimized)
    app.run()
