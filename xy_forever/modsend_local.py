"""Read and verify a host package set before publication (modsend.local_set)."""
import hashlib
from pathlib import Path

from . import depth, modstore, package_records, package_store
from .modsend import ROOT, LIST, TOKEN, MAX_PATH, Refused


def local_set(package_dir, only=()):
    """(profile, generation, {package: [(relative path, bytes)]}) for a generation folder: each package's
    files in send order (listed payloads, selected metadata, then files.sha256), every one checked
    against its files.sha256. only: package names (base is always included)."""
    root = Path(package_dir)
    metadata = package_records.local_name(root / 'base')
    if metadata is None:
        raise Refused('%s: no base package metadata (give the generation folder modkit split wrote)' % root)
    base = package_records.parse((root / 'base' / metadata).read_bytes(), metadata)
    profile, generation = base.get('profile', ''), base.get('generation', '')
    if base.get('type') != 'mod-base' or not TOKEN.match(profile) or not TOKEN.match(generation):
        raise Refused('base/%s: not a mod-base with a profile and generation' % metadata)
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / LIST).is_file())
    wanted = ['base'] + [n for n in only if n != 'base'] if only else names
    unknown = [n for n in wanted if n not in names]
    if unknown:
        raise Refused('no such package in %s: %s' % (root, ', '.join(unknown)))
    packages = {}
    for name in sorted(set(wanted), key=lambda n: (n != 'base', n)):
        if not package_store.package_name(name):
            raise Refused('unsafe package name: %s' % name)
        folder = root / name
        entries = package_store.read_files_sha256((folder / LIST).read_bytes())
        metadata = package_records.local_name(folder)
        if metadata is None or metadata not in [p for _, p in entries]:
            raise Refused('%s/%s does not list %s' % (name, LIST, metadata or 'manifest.json or info.txt'))
        files = []
        for sha, rel in entries:
            device = '%s/%s/%s/%s' % (ROOT, generation, name, modstore.stored_name(rel, sha))
            parts = rel.split('/')
            if rel == LIST or any(p in ('', '.', '..') for p in parts) or len(device.encode()) > MAX_PATH:
                raise Refused('%s: unusable path %r' % (name, rel))
            if depth.refusal(device):
                raise Refused('%s: %s' % (name, depth.refusal(device)[len('refused: '):]))
            data = (folder / rel).read_bytes() if (folder / rel).is_file() else None
            if data is None or hashlib.sha256(data).hexdigest() != sha:
                raise Refused('%s/%s: missing or differs from its %s line' % (name, rel, LIST))
            if not data:
                raise Refused('%s/%s: empty (the device stages 1 byte or more)' % (name, rel))
            files.append((rel, data))
        files.sort(key=lambda f: f[0] == metadata)            # selected metadata after payloads
        files.append((LIST, (folder / LIST).read_bytes()))     # files.sha256 last
        try:
            modstore.stored_files(name, files)
        except modstore.Clash as error:
            raise Refused(str(error))
        fields = package_store.validate(dict(files))
        if (fields.get('generation') != generation or fields.get('profile', profile) != profile or
                fields.get('type') != ('mod-base' if name == 'base' else 'mod')):
            raise Refused('%s: generation/profile differs from its base' % name)
        packages[name] = files
    return profile, generation, packages
