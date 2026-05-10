"""
setup.py — One-time setup for NuzlockeSolver on a new machine.

Run with any Python 3.x:
    python3 setup.py

What this does:
    1. Creates .venv (CPython 3.14) and installs requirements.txt
    2. Writes mgba_build.pth into .venv so 'import mgba' works
    3. Creates .venv_t (free-threaded Python 3.14t) and installs requirements_t.txt
    4. Checks that Node.js is installed and that npm packages are present

Prerequisites that must be installed manually before running this script:
    - Python 3.14  (python.org)
    - Python 3.14t free-threaded build  (python.org — same installer, tick the option)
    - Node.js  (nodejs.org)
    - The mgba build directory, either bundled as mgba_build/ or at MGBA_BUILD_PATH in config.py
"""

import glob
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN  = 'Scripts' if sys.platform == 'win32' else 'bin'
_EXE  = '.exe'    if sys.platform == 'win32' else ''


def _which(name):
    """Return True if `name` is findable on PATH."""
    try:
        subprocess.run([name, '--version'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _run(cmd):
    print(f'  $ {" ".join(str(c) for c in cmd)}')
    subprocess.run(cmd, check=True)


def _pip(venv_dir):
    return os.path.join(venv_dir, _BIN, 'pip' + _EXE)


def _site_packages(venv_dir):
    """Return the site-packages path inside a venv, handling Python version variation."""
    if sys.platform == 'win32':
        return os.path.join(venv_dir, 'Lib', 'site-packages')
    matches = glob.glob(os.path.join(venv_dir, 'lib', 'python3*', 'site-packages'))
    return matches[0] if matches else os.path.join(venv_dir, 'lib', 'python3.14', 'site-packages')


# ---------------------------------------------------------------------------

def main():
    warnings = []

    # ── 1. Create .venv (CPython 3.14) ───────────────────────────────────────
    py314 = 'python3.14' + _EXE
    venv  = os.path.join(_HERE, '.venv')
    if not os.path.exists(venv):
        print('\nCreating .venv ...')
        _run([py314, '-m', 'venv', venv])
    print('\nInstalling requirements.txt into .venv ...')
    _run([_pip(venv), 'install', '-r', os.path.join(_HERE, 'requirements.txt')])

    # ── 2. Write mgba_build.pth ───────────────────────────────────────────────
    from config import MGBA_BUILD_PATH
    pth_file = os.path.join(_site_packages(venv), 'mgba_build.pth')
    if os.path.exists(MGBA_BUILD_PATH):
        print(f'\nWriting mgba_build.pth → {MGBA_BUILD_PATH}')
        with open(pth_file, 'w') as f:
            f.write(MGBA_BUILD_PATH + '\n')
    else:
        warnings.append(
            f'mgba build not found at: {MGBA_BUILD_PATH}\n'
            '    Bundle the compiled mgba directory as mgba_build/ inside the project, or\n'
            '    update MGBA_BUILD_PATH in config.py to point to your local build.\n'
            '    On Linux: build mgba from source at https://github.com/mgba-emu/mgba'
        )

    # ── 3. Create .venv_t (free-threaded Python 3.14t) ───────────────────────
    py314t = 'python3.14t' + _EXE
    venv_t = os.path.join(_HERE, '.venv_t')
    if _which(py314t):
        if not os.path.exists(venv_t):
            print('\nCreating .venv_t ...')
            _run([py314t, '-m', 'venv', venv_t])
        print('\nInstalling requirements_t.txt into .venv_t ...')
        _run([_pip(venv_t), 'install', '-r', os.path.join(_HERE, 'requirements_t.txt')])
    else:
        warnings.append(
            f'{py314t} not found in PATH — free-threaded parallel search unavailable.\n'
            '    Install the free-threaded Python 3.14 build from python.org\n'
            '    (same installer as CPython 3.14; enable "free-threaded" option).'
        )

    # ── 4. Check Node.js ─────────────────────────────────────────────────────
    if not _which('node'):
        warnings.append(
            'node not found in PATH — the Pokemon Showdown simulator will not work.\n'
            '    Install Node.js from https://nodejs.org'
        )
    else:
        from config import NODE_SCRIPT
        node_dir = os.path.dirname(NODE_SCRIPT)
        if os.path.isdir(node_dir) and not os.path.isdir(os.path.join(node_dir, 'node_modules')):
            warnings.append(
                f'npm packages not installed in: {node_dir}\n'
                f'    Run:  cd "{node_dir}" && npm install'
            )

    # ── Summary ───────────────────────────────────────────────────────────────
    if warnings:
        print('\n' + '─' * 60)
        print('Setup complete with warnings:\n')
        for i, w in enumerate(warnings, 1):
            print(f'  [{i}] {w}\n')
    else:
        print('\nSetup complete. All dependencies found.')


if __name__ == '__main__':
    main()
