"""Leitura em streaming do server.log: agrupa cada linha com timestamp e suas linhas de continuação."""
import calendar
import gzip
import re

LINE_RE = re.compile(
    r'^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+([A-Z]+)\s+\[([^\]]*)\]\s+\(([^)]*)\)\s?(.*)$'
)
MAX_EXTRA_LINES = 3000


class Record:
    __slots__ = ('ts', 'epoch', 'level', 'cat', 'short_cat', 'thread', 'msg', 'extra', 'extra_count')

    def __init__(self, ts, epoch, level, cat, thread, msg):
        self.ts = ts
        self.epoch = epoch
        self.level = level
        self.cat = cat
        self.short_cat = cat.rsplit('.', 1)[-1]
        self.thread = thread
        self.msg = msg
        self.extra = []
        self.extra_count = 0

    @property
    def hour(self):
        return self.ts[11:13]


class ParseStats:
    def __init__(self):
        self.files = []
        self.total_lines = 0
        self.records = 0
        self.orphan_lines = 0
        self.first_ts = None
        self.last_ts = None
        self.bytes = 0


def _open(path, encoding):
    if path.lower().endswith('.gz'):
        return gzip.open(path, 'rt', encoding=encoding, errors='replace')
    return open(path, 'r', encoding=encoding, errors='replace')


def iter_records(paths, stats, encoding='utf-8', progress=None, progress_every=1_000_000):
    """Gera Records na ordem do arquivo. `progress(linhas)` é chamado a cada `progress_every` linhas."""
    epoch_cache_key = None
    epoch_cache_val = 0.0
    for path in paths:
        stats.files.append(path)
        current = None
        with _open(path, encoding) as fh:
            for raw in fh:
                stats.total_lines += 1
                if progress and stats.total_lines % progress_every == 0:
                    progress(stats.total_lines)
                line = raw.rstrip('\r\n')
                m = LINE_RE.match(line)
                if m is None:
                    if current is None:
                        stats.orphan_lines += 1
                    else:
                        current.extra_count += 1
                        if len(current.extra) < MAX_EXTRA_LINES:
                            current.extra.append(line)
                    continue
                if current is not None:
                    yield current
                y, mo, d, h, mi, s, ms, level, cat, thread, msg = m.groups()
                ts = f'{y}-{mo}-{d} {h}:{mi}:{s}'
                if ts != epoch_cache_key:
                    epoch_cache_key = ts
                    epoch_cache_val = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, 0))
                current = Record(ts, epoch_cache_val + int(ms) / 1000.0, level, cat, thread, msg)
                stats.records += 1
                if stats.first_ts is None:
                    stats.first_ts = ts
                stats.last_ts = ts
        if current is not None:
            yield current
