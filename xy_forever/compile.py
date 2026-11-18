"""Run the authenticated Blackfin toolchain (xy_forever/toolchain.py)."""
import hashlib
from pathlib import Path
import subprocess

from .toolchain import CXX_FLAGS, authenticate

SOURCE = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def toolchain():
    return authenticate()


def invoke(argv, commands):
    argv = list(map(str, argv))
    commands.append(argv)
    result = subprocess.run(argv, cwd=SOURCE, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'Command failed: {argv}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def compile_core(out, compiler, tools, commands, names=('ldr',), subdirectory='native', split_sections=False):
    flags = [f for f in CXX_FLAGS if f != '-O2'] + ['-Os', '-fstack-usage']
    # Narrow stage-2 modes need the linker to remove unused helpers from core objects too.
    if split_sections:
        flags += ['-ffunction-sections', '-fdata-sections']
    for name in names:
        invoke([compiler['cxx'], *flags, '-S', SOURCE / subdirectory / (name + '.cpp'),
                '-o', out / (name + '.s')], commands)
        invoke([tools['as'], '-mcpu=bf707', '-o', out / (name + '.o'), out / (name + '.s')], commands)
        (out / (name + '.asm')).write_text(invoke([tools['objdump'], '-dr', out / (name + '.o')], commands))
    return flags
