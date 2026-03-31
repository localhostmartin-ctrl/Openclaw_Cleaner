#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
from pathlib import Path

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


def run(cmd, check=False, capture=False):
    try:
        return subprocess.run(
            cmd,
            check=check,
            text=True,
            capture_output=capture,
        )
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


def find_files_by_name():
    found = set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
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
    labels = [
        'ai.openclaw.gateway',
        'com.openclaw.gateway',
        'com.clawdbot.gateway',
    ]
    for label in labels:
        run(['launchctl', 'bootout', f'gui/{UID}/{label}'])
        run(['launchctl', 'remove', label])


def npm_remove_global():
    packages = ['openclaw', 'clawdbot']
    for pkg in packages:
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


def main():
    print('OpenClaw macOS cleanup script')
    print('=' * 32)
    print('This script will search for OpenClaw-related files and remove them.')
    print('It uses documented locations plus a disk search for obvious leftovers.')
    print()
    print('Documented/default macOS locations checked:')
    print('- ~/.openclaw (state, config, workspace, credentials)')
    print('- ~/Library/LaunchAgents/ai.openclaw.gateway.plist')
    print('- ~/Library/LaunchAgents/com.openclaw.gateway.plist')
    print('- Legacy launch agent names such as com.clawdbot.gateway.plist')
    print('- /Applications/OpenClaw.app')
    print('- npm global package/binary locations')
    print()
    print('Note: official docs mention OPENCLAW_CONFIG_PATH or profile-specific state dirs may exist elsewhere.')
    print('This script also performs a broader filename search for paths containing openclaw/clawdbot.')
    print()

    discovered = set(p for p in CANDIDATE_PATHS if path_exists(p))
    discovered.update(find_files_by_name())
    discovered.update(list_global_bins())

    shell_hits = grep_shell_files()

    if not discovered and not shell_hits:
        print('No obvious OpenClaw files were found.')
    else:
        print('Planned removals:')
        for p in sorted(discovered, key=lambda x: str(x)):
            print(f'  - {p}')
        if shell_hits:
            print('\nShell profile lines mentioning OpenClaw were found but will NOT be auto-edited:')
            for f, lines in shell_hits:
                print(f'  - {f}')
                for line in lines[:5]:
                    print(f'      {line.strip()}')
        print()

    answer = input('Continue and delete the listed files? [y/N]: ').strip().lower()
    if answer not in {'y', 'yes'}:
        print('Aborted. Nothing was deleted.')
        return

    print('\nStopping/unregistering launchd services...')
    bootout_launchd()

    print('Removing global npm packages if present...')
    npm_remove_global()

    deleted = []
    failed = []

    for path in sorted(discovered, key=lambda x: len(str(x)), reverse=True):
        remove_path(path, deleted, failed)

    print('\nDeleted paths:')
    if deleted:
        for p in deleted:
            print(f'  - {p}')
    else:
        print('  (none)')

    if failed:
        print('\nFailed removals:')
        for p, err in failed:
            print(f'  - {p} -> {err}')

    if shell_hits:
        print('\nManual follow-up recommended:')
        print('- Open the shell files shown above and remove any openclaw/clawdbot PATH or alias lines.')
        print('- If you used OPENCLAW_CONFIG_PATH or profiles, remove those custom locations manually.')
        print('- If you installed via pnpm or bun, run: pnpm remove -g openclaw  OR  bun remove -g openclaw')

    print('\nBasic verification commands:')
    print('- which openclaw')
    print('- command -v openclaw')
    print('- ls ~/Library/LaunchAgents | grep -i claw')
    print('- find ~/.openclaw ~/Library/LaunchAgents /Applications /usr/local /opt/homebrew 2>/dev/null | grep -Ei "openclaw|clawdbot"')


if __name__ == '__main__':
    main()
