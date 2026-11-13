"""原子保存本地 JSON，并保留上一份可读备份。"""
import json
import os
from pathlib import Path
import tempfile


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value, backup=True):
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if backup and path.exists():
        try:
            old = path.read_text(encoding='utf-8-sig')
            json.loads(old)
        except (OSError, ValueError):
            # 不用损坏的新文件覆盖最后一份可读备份。
            pass
        else:
            atomic_text(str(path) + '.bak', old)
    atomic_text(path, text)


def read_json(path, default=None):
    path = Path(path)
    backup = Path(str(path) + '.bak')
    if not path.exists() and not backup.exists():
        return default
    for candidate in (path, backup):
        try:
            return json.loads(candidate.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
    raise ValueError(f'{path.name} 及备份均无法读取，请保留文件并从其他存档恢复。')
