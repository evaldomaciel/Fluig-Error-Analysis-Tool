"""Orquestra a leitura única do(s) arquivo(s) e alimenta todos os analisadores."""
import os
import time

from .errors import ErrorAnalyzer
from .findings import build_findings
from .parser import ParseStats, iter_records
from .performance import PerformanceAnalyzer


class Config:
    def __init__(self, **kw):
        self.levels = ('ERROR', 'FATAL', 'SEVERE')
        self.key_len = 250
        self.top = 25
        self.encoding = 'utf-8'
        self.business_hours = (8, 18)
        self.gap_seconds = 5.0
        self.slow_dataset_seconds = 10.0
        self.sync_alert_seconds = 600
        self.export_alert_seconds = 300
        self.script_log_ratio = 0.30
        self.minor_warn_threshold = 1000
        self.investigate_top = 3
        self.show_users = False
        for k, v in kw.items():
            if not hasattr(self, k):
                raise ValueError(f'Opção desconhecida: {k}')
            setattr(self, k, v)


class Analysis:
    def __init__(self, paths, cfg):
        self.paths = list(paths)
        self.cfg = cfg
        self.stats = ParseStats()
        self.errors = ErrorAnalyzer(levels=cfg.levels, key_len=cfg.key_len)
        self.perf = PerformanceAnalyzer(business_hours=cfg.business_hours, gap_seconds=cfg.gap_seconds)
        self.findings = []
        self.elapsed = 0.0

    def run(self, progress=None):
        start = time.time()
        for path in self.paths:
            self.stats.bytes += os.path.getsize(path)
        for rec in iter_records(self.paths, self.stats, encoding=self.cfg.encoding, progress=progress):
            self.perf.feed(rec)
            self.errors.feed(rec)
        self.errors.finish()
        self.perf.finish()
        self.findings = build_findings(self, self.cfg)
        self.elapsed = time.time() - start
        return self
