"""Sinais de lentidão: saturação, JSChronos, datasets, exportações, intervalos, concorrência e volume de log."""
import re
from collections import Counter, defaultdict

from .text_utils import normalize

SATURATION_SIGNALS = [
    ('Pool de conexões do banco esgotado',
     r'IJ000453|IJ000655|Unable to get managed connection|No managed connections available'),
    ('Timeout de transação (Arjuna)', r'ARJUNA012117|ARJUNA012121|TransactionReaper.*(timeout|cancel)|Transaction.*timed out'),
    ('Memória / GC', r'OutOfMemoryError|GC overhead|Java heap space|Metaspace|unable to create (new )?native thread'),
    ('Threads travadas / pool de threads',
     r'stuck thread|RejectedExecution|WFLYEJB0378|WFLYEJB0241|pool limit|Timed out waiting for|thread pool is full'),
    ('Timeout de rede / integração', r'SocketTimeoutException|Read timed out|connect timed out|ConnectTimeoutException'),
    ('Lock / deadlock no banco', r'deadlock|lock wait timeout|LockAcquisitionException|ORA-00060|could not obtain lock'),
]
OTHER_SIGNALS = [
    ('Paginação em memória (Hibernate HHH000104)', r'HHH000104|firstResult/maxResults specified with collection fetch'),
    ('Transações XA heurísticas (Arjuna)', r'heuristic participant|restored heuristic|XAResourceRecord < resource:null'),
    ('Sincronização de dataset concorrente', r'sincroniza[cç][aã]o deste dataset em execu'),
    ('SMTP: limite de conexões simultâneas', r'Concurrent connections limit'),
    ('Sessão de alerta encerrada', r'AlertAppSender\S*\s.*Session is closed'),
]
SATURATION_SIGNALS = [(n, re.compile(rx, re.I)) for n, rx in SATURATION_SIGNALS]
OTHER_SIGNALS = [(n, re.compile(rx, re.I)) for n, rx in OTHER_SIGNALS]

CHRONO_RE = re.compile(r'^(.*?) executou por (\d+) segundos')
DS_EXEC_RE = re.compile(r'Executando Dataset com ID (\S+) do tipo (\S+?)\.?$')
EXPORT_RE = re.compile(r'## exportProcessSearcher # (.+?) # total (\d+)s(?:, executado (\d+) vezes)?')
TASK_THREAD_RE = re.compile(r'default task-(\d+)')
SCRIPT_LOGGERS = ('ScriptingLog',)


class PerformanceAnalyzer:
    def __init__(self, business_hours=(8, 18), gap_seconds=5.0, scan_extra_lines=40):
        self.business_hours = business_hours
        self.gap_seconds = gap_seconds
        self.scan_extra_lines = scan_extra_lines

        self.by_level = Counter()
        self.lines_by_logger = Counter()
        self.records_by_logger = Counter()
        self.messages = Counter()
        self.lines_by_hour = Counter()
        self.signal_count = Counter()
        self.signal_hours = defaultdict(Counter)
        self.signal_sample = {}

        self.chrono = defaultdict(list)
        self.chrono_hours = defaultdict(Counter)
        self.chrono_by_dataset = defaultdict(list)
        self.dataset_exec = Counter()
        self._last_ds = {}

        self.exports = []
        self._export_open = None

        self.gaps = []
        self._prev_epoch = None
        self._prev_desc = ''

        self.threads_per_minute = defaultdict(set)
        self.max_task_thread = 0

    # ------------------------------------------------------------------ alimentação
    def feed(self, rec):
        lines = 1 + rec.extra_count
        self.by_level[rec.level] += 1
        logger_key = f'{rec.level} {rec.short_cat}'
        self.lines_by_logger[logger_key] += lines
        self.records_by_logger[logger_key] += 1
        self.lines_by_hour[rec.ts[:13]] += lines
        self.messages[(rec.level, rec.short_cat, normalize(rec.msg)[:140])] += 1

        self._gaps(rec)
        self._concurrency(rec)
        self._signals(rec)
        self._datasets(rec)
        self._exports(rec)

    def _gaps(self, rec):
        if self._prev_epoch is not None:
            delta = rec.epoch - self._prev_epoch
            h = int(rec.hour)
            if delta >= self.gap_seconds and self.business_hours[0] <= h < self.business_hours[1]:
                self.gaps.append((delta, rec.ts, self._prev_desc, f'{rec.short_cat}: {rec.msg[:150]}'))
        self._prev_epoch = rec.epoch
        self._prev_desc = f'{rec.short_cat}: {rec.msg[:150]}'

    def _concurrency(self, rec):
        t = TASK_THREAD_RE.search(rec.thread)
        if t:
            self.threads_per_minute[rec.ts[:16]].add(rec.thread)
            n = int(t.group(1))
            if n > self.max_task_thread:
                self.max_task_thread = n

    def _signals(self, rec):
        blob = rec.cat + ' ' + rec.msg
        if rec.level in ('ERROR', 'FATAL', 'SEVERE', 'WARN', 'WARNING') and rec.extra:
            blob += ' ' + ' '.join(l for l in rec.extra[:self.scan_extra_lines] if 'Caused by' in l)
        for name, rx in SATURATION_SIGNALS + OTHER_SIGNALS:
            if rx.search(blob):
                self.signal_count[name] += 1
                self.signal_hours[name][rec.hour] += 1
                self.signal_sample.setdefault(name, f'{rec.ts} {rec.level} [{rec.short_cat}] {rec.msg[:240]}')

    def _datasets(self, rec):
        d = DS_EXEC_RE.search(rec.msg)
        if d:
            self._last_ds[rec.thread] = d.group(1)
            self.dataset_exec[(d.group(1), d.group(2))] += 1
            return
        if rec.short_cat != 'JSChronos':
            return
        c = CHRONO_RE.match(rec.msg)
        if not c:
            return
        what = c.group(1).strip()
        secs = int(c.group(2))
        self.chrono[what].append(secs)
        self.chrono_hours[what][rec.hour] += 1
        if 'createDataset' in what or 'datasetSync' in what:
            self.chrono_by_dataset[(what.rsplit('.', 1)[-1], self._last_ds.get(rec.thread, '?'))].append(secs)

    def _exports(self, rec):
        if 'exportProcessSearcher' not in rec.msg:
            return
        m = EXPORT_RE.search(rec.msg)
        if not m:
            return
        step, total, times = m.group(1).strip(), int(m.group(2)), m.group(3)
        if step.startswith('Tempo total'):
            self._export_open = {'end': rec.ts, 'total': total, 'requests': 0, 'steps': {}}
            self.exports.append(self._export_open)
            return
        if self._export_open is None or self._export_open['end'] != rec.ts:
            return
        self._export_open['steps'][step] = total
        if step.startswith('Exportando dados principais') and times:
            self._export_open['requests'] = int(times)

    def finish(self):
        pass

    # ------------------------------------------------------------------ consultas
    def total_records(self):
        return sum(self.by_level.values())

    def total_lines(self):
        return sum(self.lines_by_logger.values())

    def script_log_lines(self):
        return sum(n for k, n in self.lines_by_logger.items() if k.split(' ', 1)[1] in SCRIPT_LOGGERS)

    def chrono_summary(self):
        rows = []
        for what, v in self.chrono.items():
            rows.append({'operacao': what, 'n': len(v), 'total_s': sum(v), 'media_s': sum(v) / len(v),
                         'max_s': max(v), 'v': v, 'horas': self.chrono_hours[what]})
        return sorted(rows, key=lambda r: r['total_s'], reverse=True)

    def getdataset_summary(self):
        return [r for r in self.chrono_summary() if r['operacao'].startswith('DatasetFactory.getDataset.')]

    def dataset_attribution(self):
        rows = []
        for (kind, ds), v in self.chrono_by_dataset.items():
            rows.append({'tipo': kind, 'dataset': ds, 'n': len(v), 'total_s': sum(v),
                         'media_s': sum(v) / len(v), 'max_s': max(v), 'v': v})
        return sorted(rows, key=lambda r: r['total_s'], reverse=True)

    def big_exports(self, min_seconds=120):
        return [e for e in self.exports if e['total'] >= min_seconds]

    def peak_minutes(self, top=10):
        return sorted(((m, len(s)) for m, s in self.threads_per_minute.items()), key=lambda x: x[1], reverse=True)[:top]

    def saturation_found(self):
        return {n: self.signal_count[n] for n, _ in SATURATION_SIGNALS if self.signal_count[n]}
