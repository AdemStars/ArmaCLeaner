"""Arma Cleaner - nettoyeur de mods Arma 3 avec treemap façon WinDirStat.

Aucune dépendance : Python 3.8+ avec tkinter (inclus sur Windows).
"""
import colorsys
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import webbrowser
import queue
import zlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ARMA_APPID = "107410"
CHECK_ON, CHECK_OFF = "☑", "☐"
STAR_ON, STAR_OFF = "★", "☆"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ArmaCleaner")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

THEMES = {
    "light": dict(bg="#f0f0f0", fg="#000000", field="#ffffff", sel="#cce4ff", canvas="#dddddd",
                  edge="#f0f0f0", tip="#ffffe0", btn="#e1e1e1", val=0.78),
    "dark": dict(bg="#252526", fg="#e6e6e6", field="#1e1e1e", sel="#094771", canvas="#1b1b1c",
                 edge="#1b1b1c", tip="#3a3a3c", btn="#3c3c3f", val=0.62),
}


def load_config():
    cfg = {"dark": False, "favorites": [], "lists": [], "last_used": {}}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


# ---------------------------------------------------------------- détection
def steam_libraries():
    """Retourne les dossiers de bibliothèques Steam trouvés."""
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        roots.append(p)
    libs = []
    for r in roots:
        r = os.path.normpath(r)
        if os.path.isdir(r) and r not in libs:
            libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                text = open(vdf, encoding="utf-8", errors="ignore").read()
                for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                    p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
                    if os.path.isdir(p) and p not in libs:
                        libs.append(p)
            except OSError:
                pass
    return libs


def find_mod_roots():
    """Dossiers contenant des mods : workshop + @mods du dossier du jeu."""
    roots = []
    for lib in steam_libraries():
        ws = os.path.join(lib, "steamapps", "workshop", "content", ARMA_APPID)
        if os.path.isdir(ws):
            roots.append(ws)
        game = os.path.join(lib, "steamapps", "common", "Arma 3")
        if os.path.isdir(game):
            roots.append(game)
    return roots


# ---------------------------------------------------------------- données
class Mod:
    def __init__(self, path, workshop_id, name):
        self.path = path
        self.id = workshop_id
        self.name = name
        self.size = 0
        self.disk = 0
        self.last_used = 0
        self.selected = False
        self.favorite = False
        self.workshop = bool(workshop_id)
        self.key = workshop_id or "dir:" + os.path.basename(path).lower()

    @property
    def source(self):
        return "Workshop" if self.workshop else "Hors workshop"


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def rpt_sessions():
    """Parcourt les logs .rpt d'Arma 3 : [(date_du_log, {noms de mods normalisés / ids})]."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    folder = os.path.join(base, "Arma 3")
    sessions = []
    try:
        files = [e for e in os.scandir(folder) if e.name.lower().endswith(".rpt")]
    except OSError:
        return []
    for e in files:
        try:
            with open(e.path, encoding="utf-8", errors="ignore") as f:
                head = f.read(100000)
            ts = e.stat().st_mtime
        except OSError:
            continue
        found = set()
        for line in head.splitlines():
            if "-mod=" not in line:
                continue
            raw = line.split("-mod=", 1)[1].split('"', 1)[0]
            for part in raw.split(";"):
                comp = part.strip().rstrip("\\/").replace("/", "\\").split("\\")[-1]
                if comp:
                    found.add(_norm(comp))
        if found:
            sessions.append((ts, found))
    return sessions


def last_used_from_rpt(mods, sessions):
    res = {}
    for m in mods:
        tokens = {_norm(m.name), _norm(os.path.basename(m.path))}
        if m.id:
            tokens.add(m.id)
        tokens.discard("")
        ts = max((t for t, found in sessions if tokens & found), default=0)
        if ts:
            res[m.key] = ts
    return res


USE_FILTERS = ("Tous", "Pas lancé depuis 30 jours", "Pas lancé depuis 90 jours",
               "Pas lancé depuis 180 jours", "Pas lancé depuis 1 an", "Jamais détecté")
USE_DAYS = {USE_FILTERS[1]: 30, USE_FILTERS[2]: 90, USE_FILTERS[3]: 180, USE_FILTERS[4]: 365}


def fmt_date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "Inconnu"


def read_name(path, fallback):
    for fn in ("meta.cpp", "mod.cpp"):
        fp = os.path.join(path, fn)
        if os.path.isfile(fp):
            try:
                text = open(fp, encoding="utf-8", errors="ignore").read()
                m = re.search(r'\bname\s*=\s*"([^"]*)"', text)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            except OSError:
                pass
    return fallback


def scan_mods(roots):
    mods = []
    for root in roots:
        is_ws = os.path.basename(root) == ARMA_APPID
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir():
                continue
            if is_ws:
                mods.append(Mod(e.path, e.name if e.name.isdigit() else "", read_name(e.path, e.name)))
            elif e.name.startswith("@"):
                mods.append(Mod(e.path, "", read_name(e.path, e.name)))
    return mods


_k32 = None
if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    _k32 = ctypes.windll.kernel32
    _k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetCompressedFileSizeW.restype = wintypes.DWORD


def disk_size(path, logical):
    """Taille réellement occupée (tient compte de la compression NTFS)."""
    if _k32 is None:
        return logical
    high = wintypes.DWORD(0)
    low = _k32.GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.GetLastError() != 0:
        return logical
    return (high.value << 32) | low


def dir_size(path):
    """Retourne (taille logique, taille sur disque)."""
    total = disk = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            n = e.stat(follow_symlinks=False).st_size
                            total += n
                            disk += disk_size(e.path, n)
                    except OSError:
                        pass
        except OSError:
            pass
    return total, disk


ALGOS = {"XPRESS4K (rapide)": "XPRESS4K", "XPRESS8K": "XPRESS8K", "XPRESS16K": "XPRESS16K",
         "LZX (max, plus lent)": "LZX"}


def run_compact(path, algo=None, on_file=None, proc_cb=None):
    """Compresse (algo) ou décompresse (None) un dossier via compact.exe (NTFS). Transparent pour le jeu.
    on_file() est appelé à chaque fichier traité ; proc_cb(proc) permet d'annuler."""
    cmd = ["compact", "/S:" + path, "/I"]
    cmd += ["/C", "/EXE:" + algo] if algo else ["/U", "/EXE"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    if proc_cb:
        proc_cb(proc)
    for raw in proc.stdout:
        if on_file and b"[" in raw:
            on_file()
    return proc.wait() == 0


def count_files(path):
    return sum(len(files) for _, _, files in os.walk(path))


def fmt_size(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


def parse_html_list(path):
    """Lit un preset HTML du launcher Arma 3. Retourne (nom, ids workshop)."""
    text = open(path, encoding="utf-8", errors="ignore").read()
    ids = sorted(set(re.findall(r"filedetails/?\?id=(\d+)", text)))
    m = re.search(r'name="arma:PresetName"\s+content="([^"]*)"', text)
    name = m.group(1).strip() if m and m.group(1).strip() else os.path.splitext(os.path.basename(path))[0]
    return name, ids


def mod_color(mod, val):
    hue = (zlib.crc32(mod.key.encode()) % 360) / 360.0
    sat = 0.35 + (zlib.crc32(mod.name.encode()) % 40) / 100.0
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# ---------------------------------------------------------------- treemap
def squarify(sizes, x, y, w, h):
    """Treemap 'squarified'. sizes triés décroissants. Retourne [(x, y, w, h)]."""
    total = float(sum(sizes))
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0, 0)] * len(sizes)
    areas = [s / total * w * h for s in sizes]
    rects = []
    i = 0
    while i < len(areas):
        side = min(w, h)
        row = [areas[i]]
        j = i + 1

        def worst(r):
            s = sum(r)
            return max(side * side * max(r) / (s * s), s * s / (side * side * min(r)))

        while j < len(areas) and worst(row + [areas[j]]) <= worst(row):
            row.append(areas[j])
            j += 1
        s = sum(row)
        if w >= h:  # colonne à gauche
            cw = s / h
            cy = y
            for a in row:
                rh = a / cw
                rects.append((x, cy, cw, rh))
                cy += rh
            x += cw
            w -= cw
        else:  # ligne en haut
            rh = s / w
            cx = x
            for a in row:
                rw = a / rh
                rects.append((cx, y, rw, rh))
                cx += rw
            y += rh
            h -= rh
        i = j
    return rects


# ---------------------------------------------------------------- application
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Arma Cleaner")
        try:
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            self._icon = tk.PhotoImage(file=os.path.join(base, "logo_win.png"))
            self.iconphoto(True, self._icon)
        except (tk.TclError, OSError):
            pass
        self.geometry("1300x760")
        self.cfg = load_config()
        self.favorites = set(self.cfg["favorites"])
        self.lists = self.cfg["lists"]
        self.dark = bool(self.cfg["dark"])
        self.mods = []
        self.q = queue.Queue()
        self.cq = queue.Queue()
        self.scan_token = 0
        self.pending = 0
        self.roots = []
        self.sort_key, self.sort_rev = "size", True
        self._build_ui()
        self.apply_theme()
        self.refresh_lists()
        self.after(100, self._poll)
        self.after(200, self.auto_detect)

    def save(self):
        self.cfg.update(dark=self.dark, favorites=sorted(self.favorites), lists=self.lists)
        save_config(self.cfg)

    # ---- thème
    def apply_theme(self):
        t = THEMES["dark" if self.dark else "light"]
        self.theme = t
        st = ttk.Style(self)
        st.theme_use("clam")
        self.configure(bg=t["bg"])
        st.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["field"],
                     bordercolor=t["canvas"], lightcolor=t["bg"], darkcolor=t["bg"],
                     troughcolor=t["bg"], insertcolor=t["fg"])
        st.configure("TButton", background=t["btn"], foreground=t["fg"])
        st.map("TButton", background=[("active", t["sel"])])
        st.configure("Treeview", background=t["field"], fieldbackground=t["field"], foreground=t["fg"])
        st.configure("Treeview.Heading", background=t["btn"], foreground=t["fg"])
        st.map("Treeview", background=[("selected", t["sel"])], foreground=[("selected", t["fg"])])
        st.configure("TCombobox", background=t["btn"], foreground=t["fg"], arrowcolor=t["fg"])
        st.map("TCombobox", fieldbackground=[("readonly", t["field"])], foreground=[("readonly", t["fg"])])
        st.configure("TScrollbar", background=t["btn"], arrowcolor=t["fg"])
        self.option_add("*TCombobox*Listbox.background", t["field"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.canvas.configure(bg=t["canvas"])
        self.tooltip.configure(bg=t["tip"], fg=t["fg"])
        self.listbox.configure(bg=t["field"], fg=t["fg"], selectbackground=t["sel"],
                               selectforeground=t["fg"], highlightbackground=t["bg"])
        self.theme_btn.configure(text="☀ Clair" if self.dark else "🌙 Sombre")
        self.tree.tag_configure("fav", foreground="#e0a800")
        self.tree.tag_configure("ext", foreground="#e07a30")
        self.tree.tag_configure("sel", background="#7a2e2e" if self.dark else "#ffc9c4")
        self.draw_treemap()

    def toggle_theme(self):
        self.dark = not self.dark
        self.apply_theme()
        self.save()

    # ---- UI
    def _build_ui(self):
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="Choisir dossier…", command=self.choose_folder).pack(side="left")
        ttk.Button(bar, text="Rescanner", command=self.rescan).pack(side="left", padx=2)
        ttk.Button(bar, text="Tout cocher (visibles)", command=lambda: self.set_visible(True)).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Tout décocher", command=lambda: self.set_all(False)).pack(side="left", padx=2)
        ttk.Button(bar, text="Inverser", command=self.invert).pack(side="left")
        self.theme_btn = ttk.Button(bar, command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=(6, 0))
        self.del_btn = ttk.Button(bar, text="Supprimer la sélection", command=self.delete_selected)
        self.del_btn.pack(side="right")
        self.decomp_btn = ttk.Button(bar, text="Décompresser", command=lambda: self.compress_selected(False))
        self.decomp_btn.pack(side="right", padx=(0, 10))
        self.comp_btn = ttk.Button(bar, text="Compresser", command=lambda: self.compress_selected(True))
        self.comp_btn.pack(side="right", padx=2)
        self.algo_var = tk.StringVar(value=next(iter(ALGOS)))
        ttk.Combobox(bar, textvariable=self.algo_var, values=list(ALGOS), state="readonly",
                     width=20).pack(side="right")

        bar2 = ttk.Frame(self, padding=(4, 0))
        bar2.pack(fill="x")
        ttk.Label(bar2, text="Filtre :").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(bar2, textvariable=self.filter_var, width=28).pack(side="left", padx=4)
        self.src_var = tk.StringVar(value="Tous")
        cb = ttk.Combobox(bar2, textvariable=self.src_var, state="readonly", width=14,
                          values=("Tous", "Favoris", "Workshop", "Hors workshop"))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())
        ttk.Label(bar2, text="Lancé :").pack(side="left", padx=(10, 2))
        self.use_var = tk.StringVar(value=USE_FILTERS[0])
        cb2 = ttk.Combobox(bar2, textvariable=self.use_var, state="readonly", width=24, values=USE_FILTERS)
        cb2.pack(side="left")
        cb2.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())
        self.status = tk.StringVar(value="Prêt")
        ttk.Label(bar2, textvariable=self.status).pack(side="right")

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        left = ttk.Frame(pw)
        lt = ttk.Frame(left)
        lt.pack(fill="both", expand=True)
        cols = ("ok", "fav", "name", "size", "disk", "used", "src", "id")
        self.tree = ttk.Treeview(lt, columns=cols, show="headings", selectmode="extended")
        for c, t, wd in (("ok", "", 28), ("fav", "★", 28), ("name", "Mod", 200), ("size", "Taille", 70),
                         ("disk", "Sur disque", 75), ("used", "Dernier lancement", 105),
                         ("src", "Source", 90), ("id", "ID", 85)):
            self.tree.heading(c, text=t, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=wd, anchor="e" if c in ("size", "disk") else "w", stretch=(c == "name"))
        sb = ttk.Scrollbar(lt, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<space>", self.on_tree_space)
        self.tree.bind("<f>", self.on_tree_fav_key)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

        lib = ttk.LabelFrame(left, text="Bibliothèque de listes HTML", padding=4)
        lib.pack(fill="x", pady=(6, 0))
        lf = ttk.Frame(lib)
        lf.pack(fill="x")
        self.listbox = tk.Listbox(lf, height=6, selectmode="extended", exportselection=False,
                                  relief="flat", activestyle="none")
        self.listbox.bind("<Button-3>", self.on_list_right_click)
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=lsb.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        lsb.pack(side="right", fill="y")
        lb = ttk.Frame(lib)
        lb.pack(fill="x", pady=(4, 0))
        ttk.Button(lb, text="Importer…", command=self.import_html).pack(side="left")
        ttk.Button(lb, text="Retirer", command=self.remove_list).pack(side="left", padx=2)
        lb2 = ttk.Frame(lib)
        lb2.pack(fill="x", pady=(4, 0))
        ttk.Button(lb2, text="Cocher les mods de la liste", command=lambda: self.apply_list(True)).pack(fill="x")
        ttk.Button(lb2, text="Cocher tout SAUF la liste", command=lambda: self.apply_list(False)).pack(fill="x", pady=(2, 0))
        pw.add(left, weight=2)

        self.canvas = tk.Canvas(pw, highlightthickness=0)
        self.canvas.bind("<Configure>", lambda e: self.draw_treemap())
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-3>", self.on_canvas_right_click)
        pw.add(self.canvas, weight=3)

        self.tooltip = tk.Label(self, relief="solid", bd=1, justify="left")
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Leave>", lambda e: self.tooltip.place_forget())

    # ---- scan
    def auto_detect(self):
        self.roots = find_mod_roots()
        if not self.roots:
            self.status.set("Dossier Arma 3 introuvable : utilise « Choisir dossier… »")
            return
        self.rescan()

    def choose_folder(self):
        d = filedialog.askdirectory(title="Dossier de mods (ex: …\\workshop\\content\\107410 ou dossier Arma 3)")
        if d:
            self.roots = [os.path.normpath(d)]
            self.rescan()

    def update_usage(self):
        """Fusionne l'historique sauvegardé avec les logs .rpt (l'historique s'accumule dans le temps)."""
        hist = self.cfg.setdefault("last_used", {})
        for k, ts in last_used_from_rpt(self.mods, rpt_sessions()).items():
            hist[k] = max(hist.get(k, 0), ts)
        for m in self.mods:
            m.last_used = hist.get(m.key, 0)
        self.save()

    def rescan(self):
        selected = {m.path for m in self.mods if m.selected}
        self.mods = scan_mods(self.roots)
        self.update_usage()
        for m in self.mods:
            m.favorite = m.key in self.favorites
            m.selected = m.path in selected and not m.favorite
        self.scan_token += 1
        token = self.scan_token
        self.pending = len(self.mods)
        self.status.set(f"Calcul des tailles… ({self.pending} mods)")
        self.refresh_lists()
        self.refresh_list()
        self.draw_treemap()
        threading.Thread(target=self._size_worker, args=(token, list(self.mods)), daemon=True).start()

    def _size_worker(self, token, mods):
        for m in mods:
            self.q.put((token, m, dir_size(m.path)))
        self.q.put((token, None, (0, 0)))

    def _poll(self):
        dirty = False
        try:
            while True:
                token, m, size = self.q.get_nowait()
                if token != self.scan_token:
                    continue
                if m is None:
                    self.update_status()
                    dirty = True
                    continue
                m.size, m.disk = size
                self.pending -= 1
                dirty = True
        except queue.Empty:
            pass
        if dirty:
            self.refresh_list()
            self.draw_treemap()
            if self.pending > 0:
                self.status.set(f"Calcul des tailles… ({self.pending} restants)")
        try:
            while True:
                kind, text = self.cq.get_nowait()
                self.handle_cq(kind, text)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---- compression NTFS (transparente pour le jeu)
    def compress_selected(self, compress, mods=None):
        sel = mods if mods is not None else [m for m in self.mods if m.selected]
        if not sel:
            messagebox.showinfo("Compression", "Coche d'abord les mods à traiter.")
            return
        if os.name != "nt":
            messagebox.showerror("Compression", "Disponible uniquement sous Windows (NTFS).")
            return
        algo = ALGOS[self.algo_var.get()] if compress else None
        verb = "Compresser" if compress else "Décompresser"
        if not messagebox.askyesno(
                verb, f"{verb} {len(sel)} mod(s) ({algo or 'décompression'}) ?\n\n"
                "Les fichiers restent utilisables par le jeu (compression NTFS transparente, "
                "réversible). Le disque doit être en NTFS. Ferme Arma 3 avant."):
            return
        self.set_busy(True)
        self.cancel_flag = threading.Event()
        self.cproc = None
        self.open_progress(verb, len(sel))
        threading.Thread(target=self._compress_worker, args=(sel, algo), daemon=True).start()

    def open_progress(self, verb, n):
        t = THEMES["dark" if self.dark else "light"]
        dlg = tk.Toplevel(self)
        dlg.title(verb)
        dlg.configure(bg=t["bg"], padx=16, pady=12)
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.protocol("WM_DELETE_WINDOW", self.cancel_compress)
        self.pdlg = dlg
        self.p_title = tk.StringVar(value=f"{verb} de {n} mod(s)…")
        self.p_detail = tk.StringVar(value="Préparation…")
        ttk.Label(dlg, textvariable=self.p_title, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(dlg, textvariable=self.p_detail, width=60).pack(anchor="w", pady=(6, 6))
        self.pbar = ttk.Progressbar(dlg, length=420, mode="indeterminate")
        self.pbar.pack(fill="x")
        self.pbar.start(12)
        self.p_pct = tk.StringVar(value="")
        ttk.Label(dlg, textvariable=self.p_pct).pack(anchor="e")
        ttk.Button(dlg, text="Annuler", command=self.cancel_compress).pack(anchor="e", pady=(6, 0))
        self.update_idletasks()
        dlg.geometry("+%d+%d" % (self.winfo_rootx() + (self.winfo_width() - dlg.winfo_reqwidth()) // 2,
                                 self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 2))
        dlg.grab_set()

    def cancel_compress(self):
        self.cancel_flag.set()
        self.p_detail.set("Annulation en cours…")
        p = self.cproc
        if p and p.poll() is None:
            p.terminate()

    def _compress_worker(self, sel, algo):
        verb = "Compression" if algo else "Décompression"
        self.cq.put(("detail", "Comptage des fichiers…"))
        counts = [count_files(m.path) for m in sel]
        total = max(sum(counts), 1)
        self.cq.put(("total", str(total)))
        done = [0]

        def on_file():
            done[0] += 1
            self.cq.put(("progress", str(done[0])))

        partial = []
        for i, m in enumerate(sel, 1):
            if self.cancel_flag.is_set():
                break
            self.cq.put(("detail", f"{verb} {i}/{len(sel)} : {m.name}"))
            ok = run_compact(m.path, algo, on_file, lambda p: setattr(self, "cproc", p))
            m.size, m.disk = dir_size(m.path)
            if not ok and not self.cancel_flag.is_set():
                partial.append(m.name)
        msg = "Annulé" if self.cancel_flag.is_set() else (
            "Terminé" + (f" — traitement partiel : {', '.join(partial[:5])} (fichiers en cours d'utilisation ?)"
                         if partial else ""))
        self.cq.put(("done", msg))

    def handle_cq(self, kind, text):
        if kind == "total":
            self.ptotal = int(text)
            self.pbar.stop()
            self.pbar.configure(mode="determinate", maximum=self.ptotal, value=0)
        elif kind == "progress":
            v = min(int(text), self.ptotal)
            self.pbar["value"] = v
            self.p_pct.set(f"{v}/{self.ptotal} fichiers — {100 * v // self.ptotal} %")
        elif kind == "detail":
            self.p_detail.set(text)
        else:
            self.compress_done(text)

    def compress_done(self, msg=""):
        if getattr(self, "pdlg", None):
            self.pdlg.grab_release()
            self.pdlg.destroy()
            self.pdlg = None
        self.set_busy(False)
        self.refresh_list()
        self.draw_treemap()
        if msg:
            self.status.set(msg)

    def set_busy(self, busy):
        for b in (self.del_btn, self.comp_btn, self.decomp_btn):
            b.state(["disabled" if busy else "!disabled"])

    # ---- liste
    def visible_mods(self):
        f = self.filter_var.get().strip().lower()
        src = self.src_var.get()
        mods = [m for m in self.mods if (not f or f in m.name.lower() or f in m.id)
                and (src == "Tous" or (src == "Favoris" and m.favorite)
                     or (src == "Workshop" and m.workshop) or (src == "Hors workshop" and not m.workshop))]
        mods = [m for m in mods if self.use_ok(m)]
        keys = {"name": lambda m: m.name.lower(), "size": lambda m: m.size, "disk": lambda m: m.disk,
                "used": lambda m: m.last_used,
                "id": lambda m: m.id,
                "ok": lambda m: m.selected, "fav": lambda m: m.favorite, "src": lambda m: m.source}
        return sorted(mods, key=keys[self.sort_key], reverse=self.sort_rev)

    def use_ok(self, m):
        f = self.use_var.get()
        if f == "Jamais détecté":
            return not m.last_used
        days = USE_DAYS.get(f)
        return days is None or (m.last_used and m.last_used < time.time() - days * 86400)

    def row_values(self, m):
        return (CHECK_ON if m.selected else CHECK_OFF, STAR_ON if m.favorite else STAR_OFF,
                m.name, fmt_size(m.size), fmt_size(m.disk), fmt_date(m.last_used), m.source, m.id or "-")

    def row_tags(self, m):
        tags = ["fav"] if m.favorite else ([] if m.workshop else ["ext"])
        if m.selected:
            tags.append("sel")
        return tuple(tags)

    def refresh_list(self):
        yview = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        for m in self.visible_mods():
            self.tree.insert("", "end", iid=m.path, values=self.row_values(m), tags=self.row_tags(m))
        self.tree.yview_moveto(yview[0])
        self.update_status()

    def update_status(self):
        sel = [m for m in self.mods if m.selected]
        ext = sum(1 for m in self.mods if not m.workshop)
        self.status.set(f"{len(self.mods)} mods ({fmt_size(sum(m.disk for m in self.mods))} sur disque), "
                        f"{ext} hors workshop, {len(self.favorites)} favoris — "
                        f"{len(sel)} sélectionnés ({fmt_size(sum(m.disk for m in sel))})")

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_key == col else (col in ("size", "disk"))
        self.sort_key = col
        self.refresh_list()

    def mod_by_path(self, path):
        return next((m for m in self.mods if m.path == path), None)

    def update_row(self, mod):
        if self.tree.exists(mod.path):
            self.tree.item(mod.path, values=self.row_values(mod), tags=self.row_tags(mod))
        self.update_status()
        self.draw_treemap()

    def toggle(self, mod):
        if mod.favorite and not mod.selected:
            self.status.set(f"« {mod.name} » est en favori : retire l'étoile pour pouvoir le cocher.")
            return
        mod.selected = not mod.selected
        self.update_row(mod)

    def toggle_favorite(self, mod):
        mod.favorite = not mod.favorite
        if mod.favorite:
            mod.selected = False
            self.favorites.add(mod.key)
        else:
            self.favorites.discard(mod.key)
        self.save()
        self.update_row(mod)

    def on_tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell":
            col = self.tree.identify_column(e.x)
            m = self.mod_by_path(self.tree.identify_row(e.y))
            if m and col == "#1":
                self.toggle(m)
                return "break"
            if m and col == "#2":
                self.toggle_favorite(m)
                return "break"

    def on_tree_space(self, e):
        for iid in self.tree.selection():
            m = self.mod_by_path(iid)
            if m:
                self.toggle(m)
        return "break"

    def on_tree_fav_key(self, e):
        for iid in self.tree.selection():
            m = self.mod_by_path(iid)
            if m:
                self.toggle_favorite(m)
        return "break"

    # ---- menus contextuels
    def on_tree_right_click(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid:
            return "break"
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        mods = [m for m in (self.mod_by_path(i) for i in self.tree.selection()) if m]
        if not mods:
            return "break"
        n = len(mods)
        menu = tk.Menu(self, tearoff=0)
        one = mods[0] if n == 1 else None
        menu.add_command(label="Supprimer" + (f" ({n} mods)" if n > 1 else " ce mod"),
                         command=lambda: self.delete_selected(mods))
        menu.add_command(label="Compresser", command=lambda: self.compress_selected(True, mods))
        menu.add_command(label="Décompresser", command=lambda: self.compress_selected(False, mods))
        menu.add_separator()
        menu.add_command(label="Ouvrir le dossier dans l'explorateur", state="normal" if one else "disabled",
                         command=lambda: self.open_folder(one))
        menu.add_command(label="Ouvrir la page Steam Workshop",
                         state="normal" if one and one.workshop else "disabled",
                         command=lambda: self.open_steam(one))
        menu.add_command(label="Copier l'ID Steam", state="normal" if one and one.workshop else "disabled",
                         command=lambda: (self.clipboard_clear(), self.clipboard_append(one.id)))
        menu.add_separator()
        menu.add_command(label="Basculer favori (★)", command=lambda: [self.toggle_favorite(m) for m in mods])
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    def open_folder(self, m):
        if m and os.path.isdir(m.path):
            os.startfile(m.path) if os.name == "nt" else subprocess.Popen(["xdg-open", m.path])

    def open_steam(self, m):
        if m and m.id:
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={m.id}")

    def on_list_right_click(self, e):
        idx = self.listbox.nearest(e.y)
        if idx < 0 or idx >= len(self.lists):
            return "break"
        if idx not in self.listbox.curselection():
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
        lists = self.selected_lists()
        ids = set().union(*(set(l["ids"]) for l in lists))
        mods = [m for m in self.mods if m.id and m.id in ids]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Cocher les mods de la liste", command=lambda: self.apply_list(True))
        menu.add_command(label="Cocher tout SAUF la liste", command=lambda: self.apply_list(False))
        menu.add_separator()
        menu.add_command(label=f"Supprimer les mods installés de la liste ({len(mods)})",
                         state="normal" if mods else "disabled",
                         command=lambda: self.delete_selected(mods))
        menu.add_command(label="Compresser les mods de la liste", state="normal" if mods else "disabled",
                         command=lambda: self.compress_selected(True, mods))
        menu.add_command(label="Décompresser les mods de la liste", state="normal" if mods else "disabled",
                         command=lambda: self.compress_selected(False, mods))
        menu.add_separator()
        menu.add_command(label="Retirer la liste de la bibliothèque", command=self.remove_list)
        menu.tk_popup(e.x_root, e.y_root)
        return "break"

    # ---- sélection en masse (les favoris ne sont jamais cochés)
    def set_visible(self, value):
        for m in self.visible_mods():
            m.selected = value and not m.favorite
        self.refresh_list()
        self.draw_treemap()

    def set_all(self, value):
        for m in self.mods:
            m.selected = value and not m.favorite
        self.refresh_list()
        self.draw_treemap()

    def invert(self):
        for m in self.visible_mods():
            m.selected = not m.selected and not m.favorite
        self.refresh_list()
        self.draw_treemap()

    # ---- bibliothèque de listes
    def refresh_lists(self):
        installed = {m.id for m in self.mods if m.id}
        self.listbox.delete(0, "end")
        for lst in self.lists:
            n = len(installed & set(lst["ids"]))
            self.listbox.insert("end", f"{lst['name']}  ({n} installés / {len(lst['ids'])})")

    def import_html(self):
        files = filedialog.askopenfilenames(title="Listes de mods HTML (preset du launcher Arma 3)",
                                            filetypes=[("HTML", "*.html *.htm"), ("Tous", "*.*")])
        added, empty = [], []
        for fp in files:
            try:
                name, ids = parse_html_list(fp)
            except OSError as ex:
                messagebox.showerror("Erreur", f"{fp}\n{ex}")
                continue
            if not ids:
                empty.append(os.path.basename(fp))
                continue
            self.lists = [l for l in self.lists if l["name"] != name]
            self.lists.append({"name": name, "ids": ids})
            added.append(name)
        if empty:
            messagebox.showwarning("Liste vide", "Aucun ID workshop dans :\n" + "\n".join(empty))
        if added:
            self.lists.sort(key=lambda l: l["name"].lower())
            self.cfg["lists"] = self.lists
            self.save()
            self.refresh_lists()
            self.status.set(f"{len(added)} liste(s) ajoutée(s) à la bibliothèque (aucune action appliquée).")

    def selected_lists(self):
        return [self.lists[i] for i in self.listbox.curselection()]

    def remove_list(self):
        sel = self.selected_lists()
        if sel and messagebox.askyesno("Retirer", f"Retirer {len(sel)} liste(s) de la bibliothèque ?\n"
                                                  "(Les mods installés ne sont pas touchés.)"):
            self.lists = [l for l in self.lists if l not in sel]
            self.save()
            self.refresh_lists()

    def apply_list(self, check_listed):
        sel = self.selected_lists()
        if not sel:
            messagebox.showinfo("Bibliothèque", "Sélectionne d'abord une ou plusieurs listes.")
            return
        ids = set().union(*(set(l["ids"]) for l in sel))
        for m in self.mods:
            hit = m.id in ids
            m.selected = (hit if check_listed else not hit) and not m.favorite and m.workshop
        self.refresh_list()
        self.draw_treemap()

    # ---- treemap
    def draw_treemap(self):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        t = self.theme
        groups = []
        for title, pred in (("★ Favoris", lambda m: m.favorite),
                            ("Workshop", lambda m: m.workshop and not m.favorite),
                            ("Hors workshop (⚠ non géré par Steam)", lambda m: not m.workshop and not m.favorite)):
            ms = sorted((m for m in self.mods if m.disk > 0 and pred(m)), key=lambda m: m.disk, reverse=True)
            if ms:
                groups.append((title, ms, sum(m.disk for m in ms)))
        if not groups or w < 10 or h < 10:
            return
        groups.sort(key=lambda g: g[2], reverse=True)
        grects = squarify([g[2] for g in groups], 0, 0, w, h)
        head = 16
        for (title, ms, _), (gx, gy, gw, gh) in zip(groups, grects):
            c.create_rectangle(gx, gy, gx + gw, gy + gh, fill=t["canvas"], outline=t["bg"], width=2)
            if gw > 50:
                c.create_text(gx + 5, gy + 2, text=title, anchor="nw", fill=t["fg"], font=("Segoe UI", 9, "bold"))
            ix, iy, iw, ih = gx + 3, gy + head, gw - 6, gh - head - 3
            if iw < 4 or ih < 4:
                continue
            for m, (x, y, rw, rh) in zip(ms, squarify([m.disk for m in ms], ix, iy, iw, ih)):
                fill = "#d9534f" if m.selected else mod_color(m, t["val"])
                outline = "#ffc107" if m.favorite else t["edge"]
                c.create_rectangle(x, y, x + rw, y + rh, fill=fill, outline=outline,
                                   width=2 if m.favorite else 1,
                                   stipple="" if m.workshop else "gray75", tags=("mod", m.path))
                if rw > 60 and rh > 28:
                    mark = ("★ " if m.favorite else "") + ("⚠ " if not m.workshop else "")
                    c.create_text(x + 4, y + 3, text=f"{mark}{m.name}\n{fmt_size(m.disk)}", anchor="nw",
                                  fill="white", width=rw - 8, font=("Segoe UI", 8), tags=("mod", m.path))

    def mod_at_event(self, e):
        items = self.canvas.find_withtag("current")
        if not items:
            return None
        for t in self.canvas.gettags(items[0]):
            m = self.mod_by_path(t) if t not in ("mod", "current") else None
            if m:
                return m
        return None

    def on_canvas_click(self, e):
        m = self.mod_at_event(e)
        if m:
            self.toggle(m)
            if self.tree.exists(m.path):
                self.tree.see(m.path)

    def on_canvas_right_click(self, e):
        m = self.mod_at_event(e)
        if m:
            self.toggle_favorite(m)

    def on_canvas_motion(self, e):
        m = self.mod_at_event(e)
        if m:
            note = "" if m.workshop else "\n⚠ Pas un mod du workshop (aucun ID Steam)"
            fav = "\n★ Favori (protégé)" if m.favorite else ""
            self.tooltip.config(text=f"{m.name}\n{fmt_size(m.size)} (sur disque : {fmt_size(m.disk)})\nID : {m.id or '-'}\n{m.path}{note}{fav}"
                                     "\nClic : cocher — Clic droit : favori")
            self.tooltip.place(x=max(0, min(e.x_root - self.winfo_rootx() + 12, self.winfo_width() - 330)),
                               y=e.y_root - self.winfo_rooty() + 12)
        else:
            self.tooltip.place_forget()

    # ---- suppression
    def delete_selected(self, mods=None):
        src = mods if mods is not None else [m for m in self.mods if m.selected]
        sel = [m for m in src if not m.favorite]
        if not sel:
            messagebox.showinfo("Rien à supprimer", "Aucun mod coché.")
            return
        total = fmt_size(sum(m.disk for m in sel))
        ext = [m for m in sel if not m.workshop]
        warn = (f"\n\n⚠ {len(ext)} mod(s) HORS WORKSHOP : Steam ne pourra pas les retélécharger."
                if ext else "")
        if not messagebox.askyesno(
                "Confirmer la suppression",
                f"Supprimer DÉFINITIVEMENT {len(sel)} mods ({total}) ?{warn}\n\n"
                "Astuce : désabonne-toi aussi du workshop Steam, sinon Steam "
                "retéléchargera les mods. Ferme Steam/Arma avant de continuer.",
                icon="warning"):
            return
        self.del_btn.state(["disabled"])
        errors = []

        def onerror(func, path, _exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except OSError:
                pass

        for i, m in enumerate(sel, 1):
            self.status.set(f"Suppression {i}/{len(sel)} : {m.name}")
            self.update_idletasks()
            shutil.rmtree(m.path, onerror=onerror)
            if os.path.exists(m.path):
                errors.append(m.name)
        deleted_workshop = list({m.id: m for m in sel
                                 if m.workshop and not os.path.exists(m.path)}.values())
        self.del_btn.state(["!disabled"])
        self.mods = [m for m in self.mods if os.path.exists(m.path)]
        self.refresh_lists()
        self.refresh_list()
        self.draw_treemap()
        if errors:
            messagebox.showwarning("Suppression incomplète",
                                   "Impossible de supprimer :\n" + "\n".join(errors[:20]))
        if deleted_workshop and messagebox.askyesno(
                "Désabonnement Steam",
                f"{len(deleted_workshop)} mod(s) Workshop supprimé(s).\n\n"
                "Steam peut les retélécharger tant que vous y êtes abonné. "
                "Ouvrir leurs pages Workshop pour vous désabonner ?"):
            for m in deleted_workshop:
                self.open_steam(m)


if __name__ == "__main__":
    App().mainloop()
