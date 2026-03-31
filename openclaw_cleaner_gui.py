#!/usr/bin/env python3
import os
import sys
import shutil
import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

HOME = Path.home()
UID = os.getuid()

CANDIDATE_PATHS = [
    HOME / '.openclaw',
    HOME / '.clawdbot',
    HOME / '.moltbot',
    HOME / '.molthub',
    HOME / 'Library' / 'LaunchAgents' / 'ai.openclaw.gateway.plist',
    HOME / 'Library' / 'LaunchAgents' / 'com.openclaw.gateway.plist',
    HOME / 'Library' / 'LaunchAgents' / 'com.clawdbot.gateway.plist',
    Path('/Applications/OpenClaw.app'),
]

SHELL_FILES = [
    HOME / '.zshrc',
    HOME / '.zprofile',
    HOME / '.bashrc',
    HOME / '.bash_profile',
    HOME / '.profile',
]

SEARCH_ROOTS = [
    HOME,
    Path('/Applications'),
    Path('/Library'),
    Path('/usr/local'),
    Path('/opt/homebrew'),
]

KEYWORDS = ['openclaw', 'clawdbot']


def run(cmd, capture=False):
    try:
        return subprocess.run(cmd, text=True, capture_output=capture)
    except Exception:
        return None


def path_exists(p: Path) -> bool:
    try:
        return p.exists() or p.is_symlink()
    except Exception:
        return False


def is_openclaw_related(path: Path) -> bool:
    s = str(path).lower()
    return any(k in s for k in KEYWORDS)


def find_files_by_name(log):
    found = set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        log(f'[scan] {root}')
        try:
            for current_root, dirs, files in os.walk(root, topdown=True, onerror=lambda e: None):
                current = Path(current_root)
                if current_root.startswith('/System'):
                    dirs[:] = []
                    continue
                for name in list(dirs) + list(files):
                    p = current / name
                    if is_openclaw_related(p):
                        found.add(p)
        except Exception:
            continue
    return found


def grep_shell_files():
    hits = []
    for f in SHELL_FILES:
        if not f.exists():
            continue
        try:
            text = f.read_text(errors='ignore').splitlines()
            matched = [line for line in text if any(k in line.lower() for k in KEYWORDS)]
            if matched:
                hits.append((f, matched))
        except Exception:
            pass
    return hits


def list_global_bins():
    found = set()
    prefixes = []
    npm_prefix = run(['npm', 'config', 'get', 'prefix'], capture=True)
    if npm_prefix and npm_prefix.returncode == 0 and npm_prefix.stdout.strip():
        prefixes.append(Path(npm_prefix.stdout.strip()))
    prefixes.extend([Path('/usr/local'), Path('/opt/homebrew')])

    for prefix in prefixes:
        for candidate in [prefix / 'bin' / 'openclaw', prefix / 'bin' / 'clawdbot']:
            if path_exists(candidate):
                found.add(candidate)
        for libdir in [prefix / 'lib' / 'node_modules' / 'openclaw', prefix / 'lib' / 'node_modules' / 'clawdbot']:
            if path_exists(libdir):
                found.add(libdir)
    return found


def bootout_launchd():
    labels = ['ai.openclaw.gateway', 'com.openclaw.gateway', 'com.clawdbot.gateway']
    for label in labels:
        run(['launchctl', 'bootout', f'gui/{UID}/{label}'])
        run(['launchctl', 'remove', label])


def npm_remove_global():
    for pkg in ['openclaw', 'clawdbot']:
        run(['npm', 'rm', '-g', pkg])


def remove_path(path: Path, deleted: list, failed: list):
    try:
        if not path_exists(path):
            return
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        deleted.append(str(path))
    except Exception as e:
        failed.append((str(path), str(e)))


class App:
    def __init__(self, root):
        self.root = root
        self.root.title('OpenClaw Cleaner')
        self.root.geometry('980x580')
        self.root.minsize(860, 480)
        self.root.configure(bg='#f5f5f5')

        self.log_queue = queue.Queue()
        self.running = False

        self.build_ui()
        self.poll_logs()

    def build_ui(self):
        outer = tk.Frame(self.root, bg='#f5f5f5', padx=18, pady=18)
        outer.pack(fill='both', expand=True)

        left = tk.Frame(outer, bg='#f5f5f5', width=220)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        right = tk.Frame(outer, bg='#f5f5f5')
        right.pack(side='left', fill='both', expand=True, padx=(14, 0))

        title = tk.Label(left, text='OpenClaw\nCleaner', justify='left', anchor='w',
                         font=('SF Pro Display', 22, 'bold'), bg='#f5f5f5', fg='#111111')
        title.pack(anchor='w', pady=(4, 12))

        desc = tk.Label(left, text='搜索磁盘中的 OpenClaw 相关文件，\n并在右侧显示删除日志。',
                        justify='left', anchor='w', font=('SF Pro Text', 12),
                        bg='#f5f5f5', fg='#555555')
        desc.pack(anchor='w', pady=(0, 18))

        self.start_btn = tk.Button(
            left,
            text='开始清理',
            command=self.start_cleanup,
            font=('SF Pro Text', 14, 'bold'),
            bg='#111111',
            fg='white',
            activebackground='#222222',
            activeforeground='white',
            relief='flat',
            bd=0,
            padx=18,
            pady=14,
            cursor='hand2'
        )
        self.start_btn.pack(anchor='w', fill='x')

        hint = tk.Label(left, text='可能会弹出 sudo 权限请求。', justify='left', anchor='w',
                        font=('SF Pro Text', 11), bg='#f5f5f5', fg='#7a7a7a')
        hint.pack(anchor='w', pady=(10, 0))

        terminal_wrap = tk.Frame(right, bg='#d9d9d9', bd=0, highlightthickness=0)
        terminal_wrap.pack(fill='both', expand=True)

        terminal_bar = tk.Frame(terminal_wrap, bg='#e9e9e9', height=34)
        terminal_bar.pack(fill='x')
        terminal_bar.pack_propagate(False)

        for color in ['#ff5f57', '#febc2e', '#28c840']:
            dot = tk.Canvas(terminal_bar, width=12, height=12, bg='#e9e9e9', highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
            dot.pack(side='left', padx=(10 if color == '#ff5f57' else 4, 0), pady=11)

        bar_title = tk.Label(terminal_bar, text='terminal', bg='#e9e9e9', fg='#666666',
                             font=('SF Pro Text', 11))
        bar_title.pack(side='left', padx=14)

        self.output = ScrolledText(
            terminal_wrap,
            wrap='word',
            bg='#0f1115',
            fg='#e8e8e8',
            insertbackground='#e8e8e8',
            relief='flat',
            bd=0,
            font=('Menlo', 12),
            padx=14,
            pady=14
        )
        self.output.pack(fill='both', expand=True)
        self.output.insert('end', 'Ready. Click “开始清理” to scan and remove OpenClaw files.\n')
        self.output.configure(state='disabled')

    def log(self, message):
        self.log_queue.put(message)

    def poll_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.output.configure(state='normal')
                self.output.insert('end', msg + '\n')
                self.output.see('end')
                self.output.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.poll_logs)

    def start_cleanup(self):
        if self.running:
            return
        confirm = messagebox.askyesno('确认', '将开始扫描并删除 OpenClaw 相关文件，是否继续？')
        if not confirm:
            return
        self.running = True
        self.start_btn.configure(state='disabled', text='清理中...')
        worker = threading.Thread(target=self.cleanup_task, daemon=True)
        worker.start()

    def cleanup_task(self):
        try:
            self.log('OpenClaw macOS cleanup started')
            self.log('Checking documented locations...')

            discovered = set(p for p in CANDIDATE_PATHS if path_exists(p))
            discovered.update(list_global_bins())
            discovered.update(find_files_by_name(self.log))
            shell_hits = grep_shell_files()

            if not discovered:
                self.log('No obvious OpenClaw files found.')
            else:
                self.log('Planned removals:')
                for p in sorted(discovered, key=lambda x: str(x)):
                    self.log(f'  - {p}')

            if shell_hits:
                self.log('Shell profile references found (not auto-edited):')
                for f, lines in shell_hits:
                    self.log(f'  - {f}')
                    for line in lines[:3]:
                        self.log(f'      {line.strip()}')

            self.log('Stopping launch agents...')
            bootout_launchd()

            self.log('Removing global npm packages if present...')
            npm_remove_global()

            deleted = []
            failed = []
            for path in sorted(discovered, key=lambda x: len(str(x)), reverse=True):
                remove_path(path, deleted, failed)

            self.log('')
            self.log('Deleted paths:')
            if deleted:
                for p in deleted:
                    self.log(f'  - {p}')
            else:
                self.log('  (none)')

            if failed:
                self.log('')
                self.log('Failed removals:')
                for p, err in failed:
                    self.log(f'  - {p} -> {err}')

            self.log('')
            self.log('Done.')
        finally:
            self.root.after(0, self.cleanup_finished)

    def cleanup_finished(self):
        self.running = False
        self.start_btn.configure(state='normal', text='开始清理')


if __name__ == '__main__':
    root = tk.Tk()
    app = App(root)
    root.mainloop()
