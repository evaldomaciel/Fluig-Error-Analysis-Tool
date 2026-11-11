"""Relatório em PDF (requer reportlab)."""
import datetime
import os
from xml.sax.saxutils import escape

from .performance import OTHER_SIGNALS, SATURATION_SIGNALS
from .report_text import investigation_lines
from .text_utils import anonymize, fmt_int, fmt_seconds, short

try:
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    HAS_REPORTLAB = True
except ImportError:  # pragma: no cover
    HAS_REPORTLAB = False

FONT_CANDIDATES = [
    ('C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/arialbd.ttf', 'C:/Windows/Fonts/consola.ttf'),
    ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
     '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),
    ('/usr/share/fonts/dejavu/DejaVuSans.ttf', '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
     '/usr/share/fonts/dejavu/DejaVuSansMono.ttf'),
    ('/usr/share/fonts/TTF/DejaVuSans.ttf', '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
     '/usr/share/fonts/TTF/DejaVuSansMono.ttf'),
    ('/Library/Fonts/Arial.ttf', '/Library/Fonts/Arial Bold.ttf', '/Library/Fonts/Courier New.ttf'),
]


class _Fonts:
    regular = 'Helvetica'
    bold = 'Helvetica-Bold'
    mono = 'Courier'
    unicode = False


def _setup_fonts():
    for reg, bold, mono in FONT_CANDIDATES:
        if all(os.path.exists(p) for p in (reg, bold, mono)):
            try:
                pdfmetrics.registerFont(TTFont('FLA-Regular', reg))
                pdfmetrics.registerFont(TTFont('FLA-Bold', bold))
                pdfmetrics.registerFont(TTFont('FLA-Mono', mono))
                pdfmetrics.registerFontFamily('FLA-Regular', normal='FLA-Regular', bold='FLA-Bold',
                                              italic='FLA-Regular', boldItalic='FLA-Bold')
                _Fonts.regular, _Fonts.bold, _Fonts.mono, _Fonts.unicode = 'FLA-Regular', 'FLA-Bold', 'FLA-Mono', True
                return
            except Exception:
                continue


def _plain(text):
    return text if _Fonts.unicode else text.encode('cp1252', 'replace').decode('cp1252')


def _safe(text):
    return _plain(escape(str(text)))


class _Styles:
    def __init__(self):
        ink = colors.HexColor('#1F2933')
        self.accent = colors.HexColor('#1F4E79')
        self.muted = colors.HexColor('#5F6B7A')
        self.rule = colors.HexColor('#D5DCE4')
        self.head_bg = colors.HexColor('#E8EEF5')
        self.sev_colors = {'Alta': '#B42318', 'Média': '#B54708', 'Baixa': '#027A48', 'Info': '#5F6B7A'}
        f = _Fonts
        self.body = ParagraphStyle('b', fontName=f.regular, fontSize=9.3, leading=13, textColor=ink, spaceAfter=5)
        self.small = ParagraphStyle('s', parent=self.body, fontSize=7.8, leading=10, spaceAfter=3)
        self.cell = ParagraphStyle('c', parent=self.body, fontSize=8, leading=10.4, spaceAfter=0)
        self.cell_b = ParagraphStyle('cb', parent=self.cell, fontName=f.bold)
        self.cell_r = ParagraphStyle('cr', parent=self.cell, alignment=2)
        self.bullet = ParagraphStyle('bl', parent=self.body, leftIndent=12, bulletIndent=2, spaceAfter=2)
        self.h1 = ParagraphStyle('h1', parent=self.body, fontName=f.bold, fontSize=14, leading=18,
                                 textColor=self.accent, spaceBefore=10, spaceAfter=6)
        self.h2 = ParagraphStyle('h2', parent=self.body, fontName=f.bold, fontSize=10.8, leading=14,
                                 spaceBefore=8, spaceAfter=4)
        self.title = ParagraphStyle('t', parent=self.body, fontName=f.bold, fontSize=20, leading=25,
                                    textColor=self.accent, spaceAfter=4)
        self.sub = ParagraphStyle('st', parent=self.body, fontSize=10.5, leading=14, textColor=self.muted)
        self.code = ParagraphStyle('code', parent=self.body, fontName=f.mono, fontSize=7.4, leading=9.6,
                                   backColor=colors.HexColor('#F4F6F8'), borderPadding=5, spaceBefore=8,
                                   spaceAfter=10)
        self.inv_title = ParagraphStyle('it', parent=self.body, fontName=f.bold, fontSize=8.5, leading=11,
                                        spaceBefore=6, spaceAfter=2)
        self.callout = ParagraphStyle('co', parent=self.body, backColor=colors.HexColor('#EEF4FA'),
                                      borderPadding=7, spaceBefore=4, spaceAfter=10)


class _Builder:
    def __init__(self, analysis):
        self.a = analysis
        self.s = _Styles()
        self.story = []

    def anon(self, text):
        return text if self.a.cfg.show_users else anonymize(text)

    def p(self, text, style=None, raw=False):
        self.story.append(Paragraph(text if raw else _safe(text), style or self.s.body))

    def bullets(self, items):
        for t in items:
            self.story.append(Paragraph(_safe(t), self.s.bullet, bulletText='•'))

    def code(self, lines):
        self.story.append(Paragraph('<br/>'.join(_safe(l).replace(' ', '&nbsp;') for l in lines), self.s.code))

    def sev(self, label):
        return Paragraph(f'<font color="{self.s.sev_colors[label]}"><b>{_safe(label)}</b></font>', self.s.cell)

    def table(self, rows, widths, num_cols=()):
        data = []
        for r_i, row in enumerate(rows):
            cells = []
            for c_i, v in enumerate(row):
                if not isinstance(v, str):
                    cells.append(v)
                    continue
                st = self.s.cell_b if r_i == 0 else (self.s.cell_r if c_i in num_cols else self.s.cell)
                cells.append(Paragraph(_safe(v), st))
            data.append(cells)
        t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1)
        style = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), 0.4, self.s.rule),
                 ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                 ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                 ('BACKGROUND', (0, 0), (-1, 0), self.s.head_bg), ('LINEBELOW', (0, 0), (-1, 0), 0.8, self.s.accent)]
        for i in range(2, len(data), 2):
            style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FAFBFC')))
        t.setStyle(TableStyle(style))
        self.story.append(t)
        self.story.append(Spacer(1, 6))

    def bar(self, value, peak, color, width=4.6 * cm, height=7):
        d = Drawing(width, height)
        if value and peak:
            d.add(Rect(0, 1, max(1.5, width * value / peak), height - 2, fillColor=colors.HexColor(color),
                       strokeColor=None))
        return d

    # ------------------------------------------------------------------ seções
    def build(self):
        a = self.a
        st, cfg, errors, perf = a.stats, a.cfg, a.errors, a.perf
        files = ', '.join(os.path.basename(p) for p in st.files)

        self.p('Análise do log do servidor Fluig', self.s.title)
        self.p(f'Erros recorrentes e sinais de lentidão — {st.first_ts[:10] if st.first_ts else ""}', self.s.sub)
        self.p(f'Arquivo(s): {files} ({st.bytes / 1024 / 1024:,.0f} MB, {fmt_int(st.total_lines)} linhas, '
               f'{st.first_ts} a {st.last_ts}) · Relatório gerado em '
               f'{datetime.datetime.now():%d/%m/%Y %H:%M}', self.s.small)
        self.story.append(Spacer(1, 6))

        # 1. Resumo
        self.p('1. Resumo executivo', self.s.h1)
        self.p(self._summary_text(), self.s.callout, raw=True)
        rows = [['#', 'Achado', 'Evidência', 'Severidade']]
        for i, f in enumerate(a.findings, 1):
            rows.append([str(i), f.title, short(self.anon(f.evidence), 330), self.sev(f.severity)])
        self.table(rows, [0.7, 5.0, 8.8, 1.8])
        self.story.append(PageBreak())

        # 2. Método
        self.p('2. Escopo e método', self.s.h1)
        self.p('O log foi processado em uma única passada, linha a linha. Cada registro de erro foi agrupado com '
               'sua stack trace; mensagens foram normalizadas (números, UUIDs e usuários) para agrupar ocorrências '
               'iguais; erros de loggers diferentes na mesma thread e no mesmo segundo foram unificados como um '
               'incidente. Para lentidão, foram usados os tempos medidos pelo próprio Fluig (JSChronos e '
               'exportProcessSearcher), sinais de saturação, intervalos sem log, concorrência e volume por logger.')
        self.table([
            ['Item', 'Quantidade'],
            ['Linhas totais', fmt_int(st.total_lines)],
            ['Registros com timestamp', fmt_int(st.records)],
            ['Linhas de continuação (stack traces, JSON multilinha)',
             fmt_int(st.total_lines - st.records - st.orphan_lines)],
            ['Registros por nível', ' · '.join(f'{k} {fmt_int(v)}' for k, v in perf.by_level.most_common())],
        ], [8.5, 7.8])
        self.p('Limitações: o log não traz métricas de CPU, memória, GC ou banco de dados; confirme as conclusões '
               'no monitoramento. A associação entre avisos de createDataset/datasetSync e o nome do dataset é '
               'aproximada (último dataset executado pela mesma thread); itens DatasetFactory.getDataset.<nome> '
               'trazem o nome exato.', self.s.small)

        # 3. Lentidão
        self.p('3. Lentidão', self.s.h1)
        self.p('3.1 Sinais de saturação', self.s.h2)
        self.table([['Sinal procurado', 'Ocorrências']] +
                   [[n, fmt_int(perf.signal_count[n])] for n, _ in SATURATION_SIGNALS], [13.8, 2.5], (1,))
        other = [[n, fmt_int(perf.signal_count[n])] for n, _ in OTHER_SIGNALS if perf.signal_count[n]]
        if other:
            self.p('Outros sinais relevantes', self.s.h2)
            self.table([['Sinal', 'Ocorrências']] + other, [13.8, 2.5], (1,))

        chrono = perf.chrono_summary()
        if chrono:
            self.p('3.2 Operações lentas registradas pelo Fluig (JSChronos)', self.s.h2)
            rows = [['Operação', 'Execuções', 'Média', 'Máximo', 'Total']]
            for r in chrono[:cfg.top]:
                rows.append([r['operacao'], fmt_int(r['n']), f'{r["media_s"]:.1f} s', f'{r["max_s"]} s',
                             fmt_seconds(r['total_s'])])
            self.table(rows, [8.6, 1.8, 1.8, 1.8, 2.3], (1, 2, 3, 4))
            attr = [r for r in perf.dataset_attribution() if r['dataset'] != '?']
            if attr:
                self.p('3.3 createDataset / datasetSync lentos por dataset', self.s.h2)
                rows = [['Dataset', 'Tipo', 'Execuções', 'Média', 'Máximo', 'Total']]
                for r in attr[:cfg.top]:
                    rows.append([r['dataset'], r['tipo'], fmt_int(r['n']), f'{r["media_s"]:.1f} s',
                                 f'{r["max_s"]} s', fmt_seconds(r['total_s'])])
                self.table(rows, [6.0, 2.6, 1.7, 1.7, 1.7, 2.6], (2, 3, 4, 5))

        if perf.exports:
            self.p('3.4 Exportações da pesquisa de solicitações', self.s.h2)
            rows = [['Término', 'Duração', 'Solicitações', 'Etapa dominante']]
            for e in perf.exports:
                dom = max(e['steps'].items(), key=lambda kv: kv[1], default=('', 0))
                rows.append([e['end'], fmt_seconds(e['total']), fmt_int(e['requests']),
                             f'{dom[0]}: {fmt_seconds(dom[1])}'])
            self.table(rows, [3.4, 2.4, 2.2, 8.3], (2,))

        total_lines = perf.total_lines() or 1
        self.p('3.5 Volume de log por logger', self.s.h2)
        self.table([['Nível e logger', 'Linhas', '% do arquivo']] +
                   [[k, fmt_int(n), f'{n * 100 / total_lines:.1f}%'] for k, n in perf.lines_by_logger.most_common(10)],
                   [10.8, 3.0, 2.5], (1, 2))
        self.p(f'Concorrência: maior identificador "default task-N" observado = {perf.max_task_thread}; pico de '
               f'{max((n for _, n in perf.peak_minutes(1)), default=0)} threads de requisição distintas logando no '
               f'mesmo minuto. Intervalos de {cfg.gap_seconds:.0f} s ou mais sem nenhum log entre {cfg.business_hours[0]}h e '
               f'{cfg.business_hours[1]}h: {fmt_int(len(perf.gaps))}.')

        self.story.append(PageBreak())

        # 4. Erros
        self.p('4. Erros', self.s.h1)
        inc_theme = errors.incidents_by_theme()
        n_inc = sum(inc_theme.values()) or 1
        self.table([
            ['Nível de contagem', 'Quantidade'],
            [f'Linhas nos níveis {", ".join(cfg.levels)}', fmt_int(errors.level_lines)],
            ['  das quais linhas de stack do [stderr]', fmt_int(errors.stderr_stack_lines)],
            ['Eventos (erro + stack trace)', fmt_int(errors.events)],
            ['Incidentes (mesma thread e segundo unificados)', fmt_int(len(errors.incidents))],
            ['Assinaturas distintas', fmt_int(len(errors.groups))],
        ], [12.3, 4.0], (1,))

        self.p('4.1 Incidentes por tema', self.s.h2)
        self.table([['Tema', 'Incidentes', '%']] +
                   [[t, fmt_int(n), f'{n * 100 / n_inc:.1f}%'] for t, n in inc_theme.most_common()],
                   [11.5, 2.6, 2.2], (1, 2))

        self.p('4.2 Principais incidentes', self.s.h2)
        inc_key = errors.incidents_by_key()
        rows = [['Qtd', 'Tema', 'Logger: mensagem']]
        for (cat, sig), n in inc_key.most_common(cfg.top):
            g = errors.groups[(cat, sig)]
            rows.append([fmt_int(n), g.theme, f'{cat.rsplit(".", 1)[-1]}: {short(self.anon(sig), 230)}'])
        self.table(rows, [1.4, 4.2, 10.7], (0,))

        inv = errors.investigate(cfg.investigate_top)
        if inv:
            self.p('4.3 Investigação das principais assinaturas', self.s.h2)
            self.p('Para cada assinatura relevante (excluindo ruído e validações de negócio): frames do produto na '
                   'stack trace, presença de código customizado, o que a thread fazia antes do erro e a dispersão '
                   'por usuários e solicitações.', self.s.small)
            for g in inv:
                lines = investigation_lines(g, cfg.show_users, inc_key.get((g.cat, g.signature), 0))
                self.p(lines[0], self.s.inv_title)
                self.code([self.anon(l if len(l) <= 150 else l[:147] + '...') for l in lines[1:]])

        if errors.custom:
            self.p('4.4 Erros de customização (processo | evento)', self.s.h2)
            rows = [['Processo', 'Evento', 'Mensagem', 'Qtd', 'Usuários']]
            for (proc, evt, msg), n in errors.custom.most_common(cfg.top):
                rows.append([proc, evt, short(self.anon(errors.custom_samples.get((proc, evt, msg), msg)), 160),
                             fmt_int(n), str(len(errors.custom_users[(proc, evt, msg)]))])
            self.table(rows, [3.9, 2.3, 7.2, 1.2, 1.7], (3, 4))

        # 5. Por hora
        self.p('5. Distribuição ao longo do dia', self.s.h1)
        by_hour = errors.incidents_by_hour()
        slow_hour = {}
        for r in chrono:
            for h, n in r['horas'].items():
                slow_hour[h] = slow_hour.get(h, 0) + n
        hours = sorted(set(k[11:13] for k in by_hour) | set(slow_hour))
        inc_h = {}
        for k, c in by_hour.items():
            inc_h[k[11:13]] = inc_h.get(k[11:13], 0) + sum(c.values())
        peak_i = max(inc_h.values(), default=1)
        peak_s = max(slow_hour.values(), default=1)
        rows = [['Hora', 'Incidentes', '', 'Operações lentas', '']]
        for h in hours:
            rows.append([f'{h}h', fmt_int(inc_h.get(h, 0)), self.bar(inc_h.get(h, 0), peak_i, '#1F4E79'),
                         fmt_int(slow_hour.get(h, 0)), self.bar(slow_hour.get(h, 0), peak_s, '#B54708')])
        self.table(rows, [1.3, 2.0, 5.0, 2.8, 5.2], (1, 3))
        if len(st.files) > 1 or (st.first_ts and st.last_ts and st.first_ts[:10] != st.last_ts[:10]):
            self.p('Observação: o período cobre mais de um dia; as horas acima somam todos os dias.', self.s.small)

        # 6. Recomendações
        self.story.append(PageBreak())
        self.p('6. Recomendações priorizadas', self.s.h1)
        rows = [['Prioridade', 'Achado e ação recomendada', 'Frente']]
        for f in a.findings:
            rows.append([self.sev(f.severity),
                         Paragraph(f'<b>{_safe(f.title)}</b><br/>{_safe(f.recommendation)}', self.s.cell), f.owner])
        self.table(rows, [2.0, 11.4, 2.9])

        self.p('Anexo — parâmetros utilizados', self.s.h2)
        self.p(f'Níveis: {", ".join(cfg.levels)} · horário comercial: {cfg.business_hours[0]}h–{cfg.business_hours[1]}h · '
               f'dataset lento: média a partir de {cfg.slow_dataset_seconds:.0f} s · sincronização longa: a partir de '
               f'{fmt_seconds(cfg.sync_alert_seconds)} · exportação grande: a partir de '
               f'{fmt_seconds(cfg.export_alert_seconds)} · log de script excessivo: a partir de '
               f'{cfg.script_log_ratio * 100:.0f}% do arquivo · usuários '
               f'{"exibidos" if cfg.show_users else "anonimizados"}.', self.s.small)
        return self.story

    def _summary_text(self):
        a = self.a
        sat = a.perf.saturation_found()
        high = [f for f in a.findings if f.severity == 'Alta']
        if sat:
            first = ('<b>Há sinais de saturação do servidor no log</b> (' +
                     _safe(', '.join(f'{k}: {fmt_int(v)}' for k, v in sat.items())) + ').')
        else:
            first = ('<b>Nenhum sinal de saturação do servidor no log</b> (pool de conexões, memória, '
                     'transações, threads, rede e deadlock). Se há lentidão, ela tende a vir de operações '
                     'específicas.')
        inc = len(a.errors.incidents)
        rest = (f' Foram identificados <b>{fmt_int(inc)} incidentes de erro</b> em '
                f'{fmt_int(len(a.errors.groups))} assinaturas distintas')
        if high:
            rest += ', e <b>' + str(len(high)) + ' achado(s) de severidade alta</b>: ' + \
                    _safe('; '.join(f.title for f in high)) + '.'
        else:
            rest += '. Nenhum achado de severidade alta.'
        return first + rest

    def on_page(self, canvas, doc):
        canvas.saveState()
        canvas.setFont(_Fonts.regular, 7.5)
        canvas.setFillColor(self.s.muted)
        canvas.drawString(2 * cm, 1.2 * cm, _plain('Análise do log do servidor Fluig'))
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, _plain(f'Página {doc.page}'))
        canvas.setStrokeColor(self.s.rule)
        canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
        canvas.restoreState()


def write_pdf(analysis, path, author=''):
    if not HAS_REPORTLAB:
        raise RuntimeError('Geração de PDF requer o pacote reportlab: pip install reportlab')
    _setup_fonts()
    b = _Builder(analysis)
    story = b.build()
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2 * cm, title='Análise do log do servidor Fluig', author=author,
                            subject='Erros recorrentes e sinais de lentidão')
    doc.build(story, onFirstPage=b.on_page, onLaterPages=b.on_page)
    return path
