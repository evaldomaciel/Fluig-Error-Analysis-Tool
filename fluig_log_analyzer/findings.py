"""Regras que transformam os dados agregados em achados com severidade e recomendação."""
import re

from .text_utils import fmt_int, fmt_seconds, short

SEVERITY_ORDER = {'Alta': 0, 'Média': 1, 'Baixa': 2, 'Info': 3}


class Finding:
    def __init__(self, severity, title, evidence, recommendation, owner, section):
        self.severity = severity
        self.title = title
        self.evidence = evidence
        self.recommendation = recommendation
        self.owner = owner
        self.section = section

    def as_dict(self):
        return {'severidade': self.severity, 'titulo': self.title, 'evidencia': self.evidence,
                'recomendacao': self.recommendation, 'frente': self.owner, 'secao': self.section}


def build_findings(analysis, cfg):
    errors, perf = analysis.errors, analysis.perf
    out = []
    inc_theme = errors.incidents_by_theme()
    n_inc = sum(inc_theme.values()) or 1

    # --- Saturação ---------------------------------------------------------
    sat = perf.saturation_found()
    if sat:
        ev = '; '.join(f'{k}: {fmt_int(v)}' for k, v in sorted(sat.items(), key=lambda kv: -kv[1]))
        out.append(Finding('Alta', 'Sinais de saturação do servidor', ev,
                           'Correlacionar com o monitoramento de CPU, memória e banco no mesmo horário; '
                           'revisar dimensionamento de pools (datasource, EJB, threads) e heap da JVM.',
                           'Infraestrutura', 'lentidao'))
    else:
        out.append(Finding('Info', 'Nenhum sinal de saturação do servidor no log',
                           'Sem pool de conexões esgotado, timeout de transação, OutOfMemory/GC, threads '
                           'travadas, timeout de rede ou deadlock.',
                           'A lentidão, se houver, tende a vir de operações específicas (ver datasets e '
                           'exportações). Confirmar no monitoramento de CPU/memória/banco.',
                           'Infraestrutura', 'lentidao'))

    # --- Sincronização de datasets ----------------------------------------
    sync = [r for r in perf.chrono_summary() if 'datasetSync' in r['operacao']]
    sync_conflicts = perf.signal_count['Sincronização de dataset concorrente']
    if sync and (max(r['max_s'] for r in sync) >= cfg.sync_alert_seconds or sync_conflicts):
        r = sync[0]
        ds_names = sorted({row['dataset'] for row in perf.dataset_attribution()
                           if row['media_s'] >= cfg.sync_alert_seconds / 2 and row['dataset'] != '?'})
        ev = (f'{fmt_int(r["n"])} sincronizações lentas, média {fmt_seconds(r["media_s"])}, máximo '
              f'{fmt_seconds(r["max_s"])}; {fmt_int(sync_conflicts)} recusas por sincronização ainda em execução.')
        if ds_names:
            ev += ' Datasets prováveis: ' + ', '.join(ds_names[:6]) + '.'
        out.append(Finding('Alta' if r['max_s'] >= cfg.sync_alert_seconds else 'Média',
                           'Sincronização de datasets demorada ou sobreposta', ev,
                           'Aumentar o intervalo de sincronização, mover para fora do horário comercial ou '
                           'tornar a sincronização incremental.', 'Customização', 'lentidao'))

    # --- Datasets lentos usados em tela -----------------------------------
    slow = [r for r in perf.getdataset_summary() if r['media_s'] >= cfg.slow_dataset_seconds]
    create = [r for r in perf.chrono_summary() if r['operacao'].endswith('createDataset')]
    if slow or create:
        parts = []
        if create:
            c = create[0]
            parts.append(f'{fmt_int(c["n"])} execuções lentas de createDataset somando '
                         f'{fmt_seconds(c["total_s"])}')
        if slow:
            parts.append('mais lentos: ' + '; '.join(
                f'{r["operacao"].replace("DatasetFactory.getDataset.", "")} (média {r["media_s"]:.1f} s, '
                f'máx. {r["max_s"]} s, {r["n"]}x)' for r in sorted(slow, key=lambda r: -r['media_s'])[:5]))
        worst = max([r['media_s'] for r in slow] or [0])
        sev = 'Alta' if worst >= 20 or (create and create[0]['total_s'] >= 3600) else 'Média'
        out.append(Finding(sev, 'Datasets customizados lentos', '; '.join(parts) + '.',
                           'Aplicar constraints/filtros na origem, reaproveitar tokens de autenticação em '
                           'integrações, usar cache ou convertê-los em datasets sincronizados.',
                           'Customização', 'lentidao'))

    # --- Exportações -------------------------------------------------------
    exports = perf.big_exports(cfg.export_alert_seconds)
    if exports:
        ev = '; '.join(f'término {e["end"][11:16]}, {fmt_seconds(e["total"])}, '
                       f'{fmt_int(e["requests"])} solicitações' for e in exports[:6])
        out.append(Finding('Média', 'Exportações grandes da pesquisa de solicitações', ev + '.',
                           'Orientar o uso de filtros menores ou exportar fora do horário de pico.',
                           'Usuários', 'lentidao'))

    # --- Log excessivo -----------------------------------------------------
    total_lines = perf.total_lines() or 1
    script_lines = perf.script_log_lines()
    if script_lines / total_lines >= cfg.script_log_ratio:
        out.append(Finding('Média', 'Log excessivo dos scripts customizados',
                           f'{fmt_int(script_lines)} linhas ({script_lines * 100 / total_lines:.0f}% do '
                           f'arquivo) vêm do ScriptingLog.',
                           'Remover log.info de objetos inteiros/JSON e mensagens de rastreio em produção.',
                           'Customização', 'lentidao'))

    # --- Certificado -------------------------------------------------------
    expired = errors.find_causes(r'CertificateExpiredException')
    untrusted = errors.find_causes(r'No trusted certificate|PKIX path building failed')
    cert_inc = inc_theme.get('SSL / certificado', 0)
    if expired or untrusted or cert_inc:
        details = []
        for root in list(expired)[:1]:
            m = re.search(r'NotAfter:\s*(.+)$', root)
            details.append('certificado vencido' + (f' (NotAfter: {m.group(1)})' if m else ''))
        if untrusted:
            details.append('certificado não confiável na truststore')
        hosts = sorted({h for g in errors.theme_groups('SSL / certificado')
                        for h in re.findall(r'https?://([^/\s:]+)', g.sample)})
        ev = f'{fmt_int(cert_inc)} incidentes; ' + '; '.join(details or ['falhas de handshake SSL'])
        if hosts:
            ev += '; host(s): ' + ', '.join(hosts[:5])
        out.append(Finding('Alta' if expired else 'Média', 'Problemas de certificado SSL', ev + '.',
                           'Renovar o certificado e revisar a truststore da JVM do servidor.',
                           'Infraestrutura', 'erros'))

    # --- SMTP --------------------------------------------------------------
    smtp = inc_theme.get('E-mail (SMTP)', 0)
    if smtp:
        top = errors.theme_groups('E-mail (SMTP)')[0]
        out.append(Finding('Média', 'Falhas no envio de e-mail', f'{fmt_int(smtp)} incidentes; principal: '
                           f'{short(top.sample, 160)}',
                           'Ajustar o envio ao limite do provedor (conta dedicada, relay ou menos envios '
                           'simultâneos) e revisar a configuração SMTP do Fluig.', 'Infraestrutura', 'erros'))

    # --- UserKnowledgeTask -------------------------------------------------
    ukt = inc_theme.get('Registro de ciência de tarefa (UserKnowledgeTask)', 0)
    if ukt:
        out.append(Finding('Média' if ukt / n_inc >= 0.1 else 'Baixa',
                           'Erro interno ao registrar ciência de tarefa (UserKnowledgeTask)',
                           f'{fmt_int(ukt)} incidentes ({ukt * 100 / n_inc:.0f}% do total). Ver a seção de '
                           'investigação para stack trace e contexto.',
                           'Abrir chamado na TOTVS com a stack trace e as solicitações afetadas; verificar '
                           'dados duplicados/inconsistentes na tabela de ciência de tarefas.', 'TOTVS', 'erros'))

    # --- Volume de arquivos ------------------------------------------------
    files = [g for g in errors.theme_groups('Arquivo / volume / indexação') if 'LocalStream' in g.cat]
    if files:
        n = sum(g.count for g in files)
        out.append(Finding('Média', 'Arquivos não encontrados no volume', f'{fmt_int(n)} erros: '
                           f'{short(files[0].sample, 120)}',
                           'Verificar a integridade do volume e da replicação de arquivos.',
                           'Infraestrutura', 'erros'))

    # --- Banco / memória como erro ----------------------------------------
    for theme in ('Banco de dados / transação', 'Memória / recursos'):
        n = inc_theme.get(theme, 0)
        if n:
            top = errors.theme_groups(theme)[0]
            out.append(Finding('Alta', theme, f'{fmt_int(n)} incidentes; principal: {short(top.sample, 160)}',
                               'Investigar com prioridade junto à infraestrutura/DBA.', 'Infraestrutura', 'erros'))

    # --- Timeout de integração --------------------------------------------
    n = inc_theme.get('Timeout de rede / integração', 0)
    if n:
        top = errors.theme_groups('Timeout de rede / integração')[0]
        out.append(Finding('Média', 'Timeouts de rede / integração', f'{fmt_int(n)} incidentes; principal: '
                           f'{short(top.sample, 160)}',
                           'Verificar disponibilidade e tempo de resposta dos serviços externos.',
                           'Infraestrutura', 'erros'))

    # --- Bugs de script ----------------------------------------------------
    bugs = errors.theme_groups('Bug em script customizado', primary_only=True)
    if bugs:
        ev = '; '.join(f'{short(g.signature, 110)} ({g.count}x)' for g in bugs[:5])
        out.append(Finding('Baixa', f'Bugs em scripts customizados ({len(bugs)} distintos)', ev,
                           'Corrigir os scripts indicados (processo, evento e linha constam na mensagem).',
                           'Customização', 'erros'))

    # --- Avisos menores ----------------------------------------------------
    heur = perf.signal_count['Transações XA heurísticas (Arjuna)']
    if heur >= cfg.minor_warn_threshold:
        out.append(Finding('Baixa', 'Transações XA heurísticas presas', f'{fmt_int(heur)} avisos do Arjuna.',
                           'Limpar as transações heurísticas do object store (suporte TOTVS/infra).',
                           'TOTVS', 'lentidao'))
    hhh = perf.signal_count['Paginação em memória (Hibernate HHH000104)']
    if hhh >= cfg.minor_warn_threshold:
        out.append(Finding('Info', 'Paginação em memória (Hibernate HHH000104)', f'{fmt_int(hhh)} avisos.',
                           'Consulta interna do produto; acompanhar, sem ação direta da customização.',
                           'TOTVS', 'lentidao'))

    val = inc_theme.get('Validação de formulário (regra de negócio)', 0)
    if val:
        out.append(Finding('Info', 'Validações de formulário registradas como erro',
                           f'{fmt_int(val)} incidentes — regras de negócio bloqueando envio, não defeitos.',
                           'Opcional: revisar se as mensagens orientam bem o usuário nos campos mais esquecidos.',
                           'Usuários', 'erros'))

    out.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return out
