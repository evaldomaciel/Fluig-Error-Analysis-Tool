import gzip
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from fluig_log_analyzer.analysis import Analysis, Config
from fluig_log_analyzer.cli import main
from fluig_log_analyzer.parser import ParseStats, iter_records
from fluig_log_analyzer.report_text import render
from fluig_log_analyzer.text_utils import anonymize, normalize

SAMPLE = os.path.join(os.path.dirname(__file__), 'fixtures', 'sample_server.log')


def run(**kw):
    return Analysis([SAMPLE], Config(**kw)).run()


class ParserTest(unittest.TestCase):
    def test_counts_records_and_continuation_lines(self):
        stats = ParseStats()
        records = list(iter_records([SAMPLE], stats))
        self.assertEqual(stats.records, len(records))
        self.assertEqual(stats.first_ts, '2026-11-09 08:00:00')
        self.assertEqual(stats.last_ts, '2026-11-09 08:30:00')
        json_rec = next(r for r in records if r.short_cat == 'ScriptingLog')
        self.assertEqual(json_rec.extra_count, 3)

    def test_reads_gzip(self):
        tmp = tempfile.mkdtemp()
        try:
            gz = os.path.join(tmp, 'server.log.gz')
            with open(SAMPLE, 'rb') as src, gzip.open(gz, 'wb') as dst:
                dst.write(src.read())
            a = Analysis([gz], Config()).run()
            self.assertEqual(a.stats.records, run().stats.records)
        finally:
            shutil.rmtree(tmp)


class ErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = run()

    def test_stderr_stack_is_one_event(self):
        stderr_groups = [g for g in self.a.errors.groups.values() if g.cat == 'stderr']
        self.assertEqual(len(stderr_groups), 1)
        self.assertEqual(stderr_groups[0].count, 1)
        self.assertEqual(self.a.errors.stderr_stack_lines, 4)

    def test_same_thread_and_second_is_one_incident(self):
        themes = self.a.errors.incidents_by_theme()
        self.assertEqual(themes['Registro de ciência de tarefa (UserKnowledgeTask)'], 2)

    def test_instances_counted_once_per_event(self):
        g = next(g for g in self.a.errors.groups.values()
                 if g.cat.endswith('WorkflowBO') and 'registerUserKnowledgeTask' in g.sample)
        self.assertEqual(g.instances['1001'], 2)
        self.assertEqual(len(g.users), 2)

    def test_call_chain_goes_from_entry_to_error(self):
        g = next(g for g in self.a.errors.groups.values()
                 if g.cat.endswith('WorkflowBO') and 'registerUserKnowledgeTask' in g.sample)
        chain = [fr for fr, _ in g.call_chain()]
        self.assertTrue(chain[0].endswith('WorkflowViewRest.getAttachments'))
        self.assertTrue(chain[-1].endswith('AbstractDAO.create'))
        self.assertFalse(g.script_frames)

    def test_root_cause_keeps_original_text(self):
        hits = self.a.errors.find_causes('CertificateExpiredException')
        self.assertTrue(any('Nov 02 08:52:46 BRT 2026' in h for h in hits))

    def test_customization_errors_parsed(self):
        keys = {(p, e) for p, e, _ in self.a.errors.custom}
        self.assertIn(('compra_teste', 'validateForm'), keys)
        self.assertIn(('reembolso_teste', 'displayFields'), keys)

    def test_theme_classification(self):
        themes = self.a.errors.incidents_by_theme()
        self.assertEqual(themes['Validação de formulário (regra de negócio)'], 1)
        self.assertEqual(themes['Bug em script customizado'], 1)
        self.assertEqual(themes['E-mail (SMTP)'], 1)
        self.assertEqual(themes['SSL / certificado'], 2)


class PerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = run()

    def test_jschronos(self):
        ops = {r['operacao']: r for r in self.a.perf.chrono_summary()}
        self.assertEqual(ops['DatasetFactory.getDataset.ds_lento_teste']['max_s'], 30)
        attr = {(r['tipo'], r['dataset']) for r in self.a.perf.dataset_attribution()}
        self.assertIn(('createDataset', 'ds_lento_teste'), attr)

    def test_export(self):
        e = self.a.perf.exports[0]
        self.assertEqual(e['total'], 1114)
        self.assertEqual(e['requests'], 9238)

    def test_no_saturation(self):
        self.assertEqual(self.a.perf.saturation_found(), {})


class FindingsTest(unittest.TestCase):
    def test_expected_findings(self):
        a = run()
        titles = {f.title: f for f in a.findings}
        self.assertEqual(titles['Problemas de certificado SSL'].severity, 'Alta')
        self.assertIn('Nov 02 08:52:46 BRT 2026', titles['Problemas de certificado SSL'].evidence)
        self.assertIn('fluig.exemplo.com.br', titles['Problemas de certificado SSL'].evidence)
        self.assertEqual(titles['Sincronização de datasets demorada ou sobreposta'].severity, 'Alta')
        self.assertIn('Datasets customizados lentos', titles)
        self.assertIn('Exportações grandes da pesquisa de solicitações', titles)
        self.assertIn('Nenhum sinal de saturação do servidor no log', titles)
        severities = [f.severity for f in a.findings]
        self.assertEqual(severities, sorted(severities, key=['Alta', 'Média', 'Baixa', 'Info'].index))


class PrivacyTest(unittest.TestCase):
    def test_users_hidden_by_default(self):
        text = render(run())
        for login in ('joao.silva', 'maria.souza', 'ana.lima', 'pedro.alves'):
            self.assertNotIn(login, text)

    def test_users_shown_on_request(self):
        self.assertIn('joao.silva', render(run(show_users=True)))

    def test_helpers(self):
        self.assertEqual(normalize('task 123 id=0xff'), 'task N id=<HEX>')
        self.assertNotIn('joao', anonymize('Usuário Executor : joao.silva - Gestor'))


class CliTest(unittest.TestCase):
    def test_all_outputs(self):
        tmp = tempfile.mkdtemp()
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main([SAMPLE, '--tudo', tmp, '-q'])
            self.assertEqual(code, 0)
            produced = os.listdir(tmp)
            self.assertIn('relatorio_sample_server.txt', produced)
            self.assertIn('dados_sample_server.json', produced)
            self.assertTrue(os.listdir(os.path.join(tmp, 'csv')))
            try:
                import reportlab  # noqa: F401
                self.assertIn('relatorio_sample_server.pdf', produced)
            except ImportError:
                pass
        finally:
            shutil.rmtree(tmp)

    def test_missing_file(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['nao_existe.log', '-q']), 2)


if __name__ == '__main__':
    unittest.main()
