"""Relatório em texto para o terminal (ou arquivo .txt)."""
from collections import Counter

from .performance import OTHER_SIGNALS, SATURATION_SIGNALS
from .text_utils import anonymize, fmt_int, fmt_seconds, percentile, short


class _Out:
    def __init__(self, show_users):
        self.lines = []
        self.show_users = show_users

    def __call__(self, text=''):
        self.lines.append(text if self.show_users else anonymize(text))

    def title(self, text):
        self('')
        self('=' * 100)
        self(text)
        self('=' * 100)

    def sub(self, text):
        self('')
        self(f'--- {text} ---')


def investigation_lines(g, show_users, incidents):
    lines = [f'[{g.theme}] {g.cat}',
             f'Incidentes: {fmt_int(incidents)}   Eventos: {fmt_int(g.count)}   {g.first} -> {g.last}',
             f'Mensagem: {short(g.sample, 260)}']
    for root, n in g.roots.most_common(2):
        lines.append(f'Causa raiz ({fmt_int(n)}x): {short(g.root_samples.get(root, root), 220)}')
    if g.frames:
        lines.append('Cadeia de chamadas do produto (Fluig/TOTVS), da entrada até o erro:')
        for depth, (fr, n) in enumerate(g.call_chain(8)):
            lines.append(f'   {fmt_int(n):>7}  {"  " * depth}{"-> " if depth else ""}{fr}')
    if g.script_frames:
        lines.append('Frames de script/customização na stack:')
        for fr, n in g.script_frames.most_common(5):
            lines.append(f'   {fmt_int(n):>7}  {fr}')
    else:
        lines.append('Nenhum frame de script/customização na stack (erro originado no produto).')
    if g.contexts:
        lines.append('Último registro da mesma thread antes do erro:')
        for ctx, n in g.contexts.most_common(4):
            lines.append(f'   {fmt_int(n):>7}  {short(ctx, 150)}')
    if g.users:
        top_user, top_n = g.users.most_common(1)[0]
        share = top_n * 100 / sum(g.users.values())
        lines.append(f'Usuários distintos: {len(g.users)}; o mais afetado concentra {share:.0f}% das ocorrências'
                     + (f' ({top_user})' if show_users else ''))
    if g.instances:
        repeated = sum(1 for n in g.instances.values() if n > 1)
        lines.append(f'Solicitações distintas: {fmt_int(len(g.instances))}; {fmt_int(repeated)} com repetição '
                     f'(máx. {max(g.instances.values())}x na mesma)')
    return lines


def render(analysis):
    cfg, st, errors, perf = analysis.cfg, analysis.stats, analysis.errors, analysis.perf
    out = _Out(cfg.show_users)
    top = cfg.top

    out.title('ANÁLISE DE LOG DO SERVIDOR FLUIG')
    for p in st.files:
        out(f'Arquivo: {p}')
    out(f'Período: {st.first_ts} -> {st.last_ts}   Tamanho: {st.bytes / 1024 / 1024:,.0f} MB   '
        f'Processado em {analysis.elapsed:.0f} s')
    out(f'Linhas: {fmt_int(st.total_lines)}   Registros com timestamp: {fmt_int(st.records)}   '
        f'Linhas de continuação: {fmt_int(st.total_lines - st.records - st.orphan_lines)}')
    out('Por nível: ' + ', '.join(f'{k}={fmt_int(v)}' for k, v in perf.by_level.most_common()))

    # ------------------------------------------------------------------ achados
    out.title('ACHADOS (ordenados por severidade)')
    for i, f in enumerate(analysis.findings, 1):
        out(f'{i:>2}. [{f.severity}] {f.title}  ({f.owner})')
        out(f'    Evidência: {f.evidence}')
        out(f'    Recomendação: {f.recommendation}')

    # ------------------------------------------------------------------ erros
    out.title('ERROS')
    inc_theme = errors.incidents_by_theme()
    n_inc = sum(inc_theme.values()) or 1
    out(f'Linhas nos níveis {",".join(cfg.levels)}: {fmt_int(errors.level_lines)} '
        f'(das quais {fmt_int(errors.stderr_stack_lines)} são linhas de stack do [stderr])')
    out(f'Eventos (erro + stack): {fmt_int(errors.events)}   Incidentes: {fmt_int(len(errors.incidents))}   '
        f'Assinaturas distintas: {fmt_int(len(errors.groups))}')

    out.sub('Incidentes por tema')
    for theme, n in inc_theme.most_common():
        out(f'{fmt_int(n):>8}  {n * 100 / n_inc:5.1f}%  {theme}')

    out.sub(f'Top {top} incidentes (assinatura principal)')
    inc_key = errors.incidents_by_key()
    for (cat, sig), n in inc_key.most_common(top):
        g = errors.groups[(cat, sig)]
        out(f'{fmt_int(n):>8}  [{g.theme}]  {cat.rsplit(".", 1)[-1]}: {short(sig, 160)}')

    if errors.custom:
        out.sub('Erros de customização (processo | evento | mensagem)')
        for key, n in errors.custom.most_common(top):
            proc, evt, _ = key
            users = len(errors.custom_users[key])
            out(f'{fmt_int(n):>8}  usuários={users:<3} {proc} | {evt} | {short(errors.custom_samples[key], 130)}')

    out.sub(f'Top {top} assinaturas por logger (detalhe)')
    for i, g in enumerate(errors.ranked_groups()[:top], 1):
        out(f'#{i} {fmt_int(g.count)}x [{g.theme}] {g.cat}  {g.first} -> {g.last}')
        out(f'    Mensagem: {short(g.sample, 220)}')
        for root, n in g.roots.most_common(2):
            out(f'    Causa raiz ({fmt_int(n)}x): {short(g.root_samples.get(root, root), 200)}')

    out.sub('Incidentes por hora (3 temas principais)')
    by_hour = errors.incidents_by_hour()
    peak = max((sum(c.values()) for c in by_hour.values()), default=1)
    for hour in sorted(by_hour):
        c = by_hour[hour]
        total = sum(c.values())
        tops = ', '.join(f'{t.split(" (")[0]}={n}' for t, n in c.most_common(3))
        out(f'{hour}h {fmt_int(total):>6} {"#" * max(1, int(40 * total / peak)):<40} {tops}')

    # ------------------------------------------------------------------ investigação
    out.title('INVESTIGAÇÃO DAS PRINCIPAIS ASSINATURAS (exclui ruído e validações)')
    for g in errors.investigate(cfg.investigate_top):
        out('')
        for line in investigation_lines(g, cfg.show_users, inc_key.get((g.cat, g.signature), 0)):
            out(line)

    # ------------------------------------------------------------------ lentidão
    out.title('LENTIDÃO')
    out.sub('Sinais de saturação')
    for name, _ in SATURATION_SIGNALS:
        out(f'{fmt_int(perf.signal_count[name]):>8}  {name}')
    out.sub('Outros sinais')
    for name, _ in OTHER_SIGNALS:
        n = perf.signal_count[name]
        if n:
            hours = ' '.join(f'{h}h:{c}' for h, c in sorted(perf.signal_hours[name].items()))
            out(f'{fmt_int(n):>8}  {name}\n          {hours}\n          ex.: {short(perf.signal_sample[name], 200)}')

    chrono = perf.chrono_summary()
    if chrono:
        out.sub('JSChronos — operações lentas registradas pelo Fluig')
        for r in chrono[:top]:
            out(f'n={fmt_int(r["n"]):>7} total={fmt_seconds(r["total_s"]):>12} média={r["media_s"]:7.1f}s '
                f'p95={percentile(r["v"], .95):>5}s máx={r["max_s"]:>5}s  {r["operacao"]}')
        attr = perf.dataset_attribution()
        if attr:
            out.sub('createDataset/datasetSync lentos por dataset (último dataset executado na mesma thread)')
            for r in attr[:top]:
                out(f'n={fmt_int(r["n"]):>7} total={fmt_seconds(r["total_s"]):>12} média={r["media_s"]:7.1f}s '
                    f'máx={r["max_s"]:>5}s  {r["tipo"]:<14} {r["dataset"]}')

    if perf.dataset_exec:
        out.sub('Datasets mais executados')
        for (ds, tipo), n in perf.dataset_exec.most_common(top):
            out(f'{fmt_int(n):>8}  {tipo:<9} {ds}')

    if perf.exports:
        out.sub('Exportações da pesquisa de solicitações (exportProcessSearcher)')
        for e in perf.exports:
            dominant = max(e['steps'].items(), key=lambda kv: kv[1], default=('', 0))
            out(f'término {e["end"]}  duração {fmt_seconds(e["total"]):>12}  solicitações {fmt_int(e["requests"]):>7}'
                f'  etapa dominante: {dominant[0]} ({fmt_seconds(dominant[1])})')

    out.sub(f'Intervalos >= {cfg.gap_seconds:.0f}s sem nenhum log entre {cfg.business_hours[0]}h e '
            f'{cfg.business_hours[1]}h')
    out(f'Total: {fmt_int(len(perf.gaps))}')
    for g, ts, before, after in sorted(perf.gaps, reverse=True)[:10]:
        out(f'{g:7.1f}s até {ts}\n          antes : {short(before, 140)}\n          depois: {short(after, 140)}')

    out.sub(f'Concorrência (maior "default task-N": {perf.max_task_thread})')
    for minute, n in perf.peak_minutes(10):
        out(f'  {minute}  threads de requisição distintas: {n}')

    out.sub('Volume de log por hora (linhas)')
    peak = max(perf.lines_by_hour.values(), default=1)
    for h in sorted(perf.lines_by_hour):
        n = perf.lines_by_hour[h]
        out(f'{h}h {fmt_int(n):>10} {"#" * max(1, int(40 * n / peak))}')

    total_lines = perf.total_lines() or 1
    out.sub('Loggers que mais geram linhas (incluindo continuação)')
    for lg, n in perf.lines_by_logger.most_common(15):
        out(f'{fmt_int(n):>10}  {n * 100 / total_lines:5.1f}%  {lg}')

    out.sub('Mensagens mais repetidas (normalizadas)')
    for (level, cat, msg), n in perf.messages.most_common(top):
        out(f'{fmt_int(n):>9}  {level:<5} [{cat}] {msg}')

    return '\n'.join(out.lines) + '\n'
