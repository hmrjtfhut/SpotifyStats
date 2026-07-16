import threading
from pathlib import Path
from PIL import Image, ImageDraw
import tkinter as tk

try:
    from pystray import Icon, Menu, MenuItem
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

try:
    import win32api
    import win32con
    import win32gui
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False


class TrayIcon:
    @staticmethod
    def create_pil_image(size=64):
        img  = Image.new("RGB", (size, size), color="#1DB954")
        draw = ImageDraw.Draw(img)
        w = "white"
        draw.rectangle([size//2-2, size//4, size//2+2, size//2+size//4], fill=w)
        cy = size//2 + size//6
        draw.ellipse([size//2-8, cy-6,  size//2,    cy+2],  fill=w)
        draw.ellipse([size//2+4, cy+2,  size//2+12, cy+10], fill=w)
        return img

    @staticmethod
    def write_ico(path):
        """Write the tray icon to a .ico file for native win32 use."""
        try:
            TrayIcon.create_pil_image(64).save(
                path, format="ICO", sizes=[(16, 16), (32, 32), (64, 64)]
            )
            return True
        except Exception as e:
            print(f"[Tray] Could not write icon: {e}")
            return False


class MinimizeToTray:
    def __init__(self, root, title="Spotify Stats Tracker"):
        self.root  = root
        self.title = title
        self.is_minimized     = False
        self.tray_icon        = None
        self._polling_for_icon = False

        if HAS_WIN32:
            threading.Thread(target=self._setup_native, daemon=True).start()
        elif HAS_PYSTRAY:
            threading.Thread(target=self._setup_pystray, daemon=True).start()

    # ── pystray backend ──────────────────────────────────────────────────
    def _setup_pystray(self):
        try:
            menu = Menu(
                MenuItem("Show",  self.restore_window),
                MenuItem("Exit",  self.exit_app),
            )
            icon = Icon("Spotify Stats Tracker", TrayIcon.create_pil_image(), menu=menu)
            self.tray_icon = icon
            icon.run()
        except Exception as e:
            print(f"[Tray] pystray error: {e}")

    # ── native win32 backend ─────────────────────────────────────────────
    def _setup_native(self):
        try:
            wc            = win32gui.WNDCLASS()
            hinst         = wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = "SpotifyStatsTray"

            def _wndproc(hwnd, msg, wparam, lparam):
                try:
                    if   msg == win32con.WM_DESTROY:     return self._on_destroy(hwnd, msg, wparam, lparam) or 0
                    elif msg == win32con.WM_COMMAND:      return self._on_command(hwnd, msg, wparam, lparam) or 0
                    elif msg == win32con.WM_USER + 20:    return self._on_notify(hwnd, msg, wparam, lparam)  or 0
                except Exception as e:
                    print(f"[Tray] wndproc error: {e}")
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

            wc.lpfnWndProc = _wndproc
            win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindow(
                wc.lpszClassName, "SpotifyStatsTray", 0, 0, 0, 0, 0, 0, 0, hinst, None
            )
            self._hwnd = hwnd

            hIcon = self._load_icon()
            nid   = (hwnd, 0,
                     win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                     win32con.WM_USER + 20, hIcon, self.title)
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
            self.tray_icon = nid
            win32gui.PumpMessages()
        except Exception as e:
            print(f"[Tray] Native setup failed: {e}")

    def _load_icon(self):
        ico_path = Path.home() / ".spotistats" / "tray_icon.ico"
        try:
            ico_path.parent.mkdir(parents=True, exist_ok=True)
            if not ico_path.exists():
                TrayIcon.write_ico(str(ico_path))
            return win32gui.LoadImage(
                0, str(ico_path), win32con.IMAGE_ICON, 0, 0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
            )
        except Exception as e:
            print(f"[Tray] Icon load error, using default: {e}")
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        try:
            if self.tray_icon:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self.tray_icon)
        except Exception:
            pass
        win32gui.PostQuitMessage(0)

    def _on_command(self, hwnd, msg, wparam, lparam):
        try:
            id_ = win32api.LOWORD(int(wparam or 0))
        except Exception:
            id_ = 0
        if   id_ == 1023: self.restore_window()
        elif id_ == 1024: self.exit_app()

    def _on_notify(self, hwnd, msg, wparam, lparam):
        try:
            l = int(lparam or 0)
        except Exception:
            l = 0
        if l == win32con.WM_RBUTTONUP:
            menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(menu, win32con.MF_STRING, 1023, "Show")
            win32gui.AppendMenu(menu, win32con.MF_STRING, 1024, "Exit")
            pos = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self._hwnd)
            win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self._hwnd, None)
            win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
        elif l == win32con.WM_LBUTTONDBLCLK:
            self.restore_window()

    # ── Public interface ─────────────────────────────────────────────────
    def on_window_minimize(self, event=None):
        if event is not None and self.root.state() != "iconic":
            return
        self.minimize_to_tray()

    def minimize_to_tray(self):
        if self.is_minimized or self._polling_for_icon:
            return

        if not (HAS_PYSTRAY or HAS_WIN32):
            try:
                self.root.iconify()
            except Exception:
                self.root.withdraw()
            self.is_minimized = True
            return

        if self.tray_icon is None:
            try:
                self.root.iconify()
            except Exception:
                pass
            self._polling_for_icon = True
            attempts = [0]

            def _poll():
                if self.tray_icon:
                    try:
                        self.root.withdraw()
                    except Exception:
                        pass
                    self.is_minimized     = True
                    self._polling_for_icon = False
                    print("[Tray] Minimized to tray")
                elif attempts[0] < 30:
                    attempts[0] += 1
                    self.root.after(100, _poll)
                else:
                    self._polling_for_icon = False

            self.root.after(100, _poll)
            return

        try:
            self.root.withdraw()
        except Exception:
            pass
        self.is_minimized = True
        print("[Tray] Minimized to tray")

    def restore_window(self, icon=None, item=None):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(100, lambda: self.root.attributes("-topmost", False))
            self.is_minimized = False
        except Exception as e:
            print(f"[Tray] Restore error: {e}")

    def exit_app(self, icon=None, item=None):
        try:
            if HAS_PYSTRAY and self.tray_icon and hasattr(self.tray_icon, "stop"):
                self.tray_icon.stop()
            elif HAS_WIN32 and self.tray_icon:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, self.tray_icon)
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
