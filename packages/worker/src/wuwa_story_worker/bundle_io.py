"""Bounded-memory loading and writing of retained sequence bundles.

Roles decide where an entry lives while a bundle is rewritten:
* ``decoded/``, ``source/`` and ``media/`` entries are streamed to a spill directory (verified by
  size and SHA-256 on the way) and handled as ``Path`` values;
* everything else (manifest, controls, presentation, spine files, evidence) stays in memory as ``bytes``
  under a small bound.
Nothing is skipped: every entry is still checked against the manifest.
"""

import hashlib
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from wuwa_story_worker.sequence_composition import (
    MAX_ARCHIVE_ENTRIES, MAX_ARCHIVE_ENTRY_SIZE, MAX_ARCHIVE_TOTAL_SIZE, entry_size_limit, ArchiveValidationError)

# Total of the entries kept in memory at once (manifest, controls, presentation, spine rig, evidence).
MAX_MEMORY_BYTES = 512 * 1024 * 1024
# Free disk space that must remain after spilling, so a corrupt archive cannot fill the volume.
DISK_RESERVE_BYTES = int(os.getenv('WUWA_BUNDLE_DISK_RESERVE_BYTES', str(8 * 1024 ** 3)))
CHUNK = 1024 * 1024


def spilled(name):
    return name.startswith(('decoded/', 'source/', 'media/'))


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('Invalid retained sequence archive path')
    return name


def digest(value):
    """``{'sha256', 'size_bytes'}`` for bytes or a file on disk."""
    if isinstance(value, bytes):
        return {'sha256': hashlib.sha256(value).hexdigest(), 'size_bytes': len(value)}
    sha, size = hashlib.sha256(), 0
    with Path(value).open('rb') as handle:
        while chunk := handle.read(CHUNK):
            sha.update(chunk)
            size += len(chunk)
    return {'sha256': sha.hexdigest(), 'size_bytes': size}


def same_content(left, right):
    return digest(left) == digest(right)


def check_inventory(inventory, spill):
    """Bound entry count, per-role sizes, in-memory total and the disk the spill would need."""
    if len(inventory) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveValidationError(f'Retained bundle exceeds entry count limit: {len(inventory)} > {MAX_ARCHIVE_ENTRIES}')
    memory = disk = 0
    for name, expected in inventory.items():
        size = expected['size_bytes']
        limit = entry_size_limit(name)
        if size < 0 or size > limit:
            raise ArchiveValidationError(
                f'Retained bundle exceeds per-entry size limit: {name} {size} > {limit} bytes')
        if spilled(name):
            disk += size
        else:
            memory += size
    if memory > MAX_MEMORY_BYTES:
        raise ArchiveValidationError(f'Retained bundle in-memory entries exceed {MAX_MEMORY_BYTES} bytes: {memory}')
    if memory + disk > MAX_ARCHIVE_TOTAL_SIZE:
        raise ArchiveValidationError(f'Retained bundle exceeds total uncompressed size limit: {memory + disk}')
    spill.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(spill).free
    if disk + DISK_RESERVE_BYTES > free:
        raise OSError(f'Not enough free disk to unpack the bundle: need {disk} bytes plus reserve, have {free}')


def load_entries(archive, inventory, spill):
    """Verified entries: ``bytes`` for in-memory roles, ``Path`` (inside ``spill``) for spilled roles."""
    spill = Path(spill)
    check_inventory(inventory, spill)
    entries = {}
    for index, (name, expected) in enumerate(inventory.items()):
        safe_name(name)
        info = archive.getinfo(name)
        if info.file_size != expected['size_bytes']:
            raise ValueError('Retained sequence entry size differs')
        if not spilled(name):
            data = archive.read(info)
            if hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('Retained sequence entry checksum differs')
            entries[name] = data
            continue
        target = spill / f'{index:06d}.bin'
        sha, size = hashlib.sha256(), 0
        with archive.open(info) as source, target.open('wb') as output:
            while chunk := source.read(CHUNK):
                sha.update(chunk)
                size += len(chunk)
                output.write(chunk)
        if size != expected['size_bytes'] or sha.hexdigest() != expected['sha256']:
            target.unlink(missing_ok=True)
            raise ValueError('Retained sequence entry checksum differs')
        entries[name] = target
    return entries


@contextmanager
def spill_directory(parent):
    """Scratch space next to the bundle (same volume), always removed."""
    Path(parent).mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='.bundle-spill-', dir=parent))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def write_entry(archive, name, value):
    info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    if isinstance(value, bytes):
        archive.writestr(info, value)
        return
    with Path(value).open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
        while chunk := source.read(CHUNK):
            target.write(chunk)


def write_archive(path, entries):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(entries.items()):
            write_entry(archive, name, value)


def materialize(value, destination):
    """Put an entry at ``destination`` without holding it in memory."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        destination.write_bytes(value)
    else:
        shutil.copyfile(value, destination)
    return destination
