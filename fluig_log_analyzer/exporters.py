"""Exportação dos dados agregados em CSV (para Excel) e JSON (para integração)."""
import csv
import json
import os

from .text_utils import anonymize, percentile


def _a(text, show_users):
    return text if show_users else anonymize(text)


def write_csv(analysis, directory):
    os.makedirs(directory, exist_ok=True)
    su = analysis.cfg.show_users
    errors, perf = analysis.errors, analysis.perf
    inc_key = errors.incidents_by_key()
    written = []

    def _w(name, header, rows):
        path = os.path.join(directory, name)
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh, delimiter=';')
            w.writerow(header)
            w.writerows(rows)
        written.append(path)

    _w('erros_assinaturas.csv',
       ['eventos', 'incidentes', 'tema', 'categoria', 'primeira', 'ultima', 'assinatura', 'mensagem_exemplo',
        'causa_raiz_principal', 'qtd_causa_raiz'],
       [[g.count, inc_key.get((g.cat, g.signature), 0), g.theme, g.cat, g.first, g.last,
         _a(g.signature, su), _a(g.sample, su),
         _a(g.root_samples.get(g.roots.most_common(1)[0][0], '') if g.roots else '', su),
         g.roots.most_common(1)[0][1] if g.roots else 0]
        for g in errors.ranked_groups()])

    inc_theme = errors.incidents_by_theme()
    _w('erros_por_tema.csv', ['tema', 'incidentes'], inc_theme.most_common())

    _w('erros_customizacao.csv', ['processo', 'evento', 'mensagem', 'ocorrencias', 'usuarios'],
       [[p, e, _a(errors.custom_samples.get((p, e, m), m), su), n, len(errors.custom_users[(p, e, m)])]
        for (p, e, m), n in errors.custom.most_common()])

    _w('lentidao_jschronos.csv', ['operacao', 'execucoes', 'total_s', 'media_s', 'p95_s', 'max_s'],
       [[r['operacao'], r['n'], r['total_s'], round(r['media_s'], 1), percentile(r['v'], .95), r['max_s']]
        for r in perf.chrono_summary()])

    _w('lentidao_por_dataset.csv', ['tipo', 'dataset', 'execucoes', 'total_s', 'media_s', 'max_s'],
       [[r['tipo'], r['dataset'], r['n'], r['total_s'], round(r['media_s'], 1), r['max_s']]
        for r in perf.dataset_attribution()])

    _w('datasets_execucoes.csv', ['dataset', 'tipo', 'execucoes'],
       [[ds, t, n] for (ds, t), n in perf.dataset_exec.most_common()])

    _w('exportacoes.csv', ['termino', 'duracao_s', 'solicitacoes'],
       [[e['end'], e['total'], e['requests']] for e in perf.exports])

    _w('volume_por_logger.csv', ['nivel_logger', 'linhas', 'registros'],
       [[k, n, perf.records_by_logger[k]] for k, n in perf.lines_by_logger.most_common()])

    _w('achados.csv', ['severidade', 'titulo', 'evidencia', 'recomendacao', 'frente'],
       [[f.severity, f.title, _a(f.evidence, su), f.recommendation, f.owner] for f in analysis.findings])
    return written


def write_json(analysis, path):
    su = analysis.cfg.show_users
    st, errors, perf = analysis.stats, analysis.errors, analysis.perf
    inc_key = errors.incidents_by_key()
    data = {
        'arquivos': st.files,
        'periodo': {'inicio': st.first_ts, 'fim': st.last_ts},
        'linhas': st.total_lines,
        'registros': st.records,
        'por_nivel': dict(perf.by_level),
        'achados': [dict(f.as_dict(), evidencia=_a(f.evidence, su)) for f in analysis.findings],
        'erros': {
            'linhas_no_nivel': errors.level_lines,
            'eventos': errors.events,
            'incidentes': len(errors.incidents),
            'por_tema': dict(errors.incidents_by_theme()),
            'assinaturas': [
                {'eventos': g.count, 'incidentes': inc_key.get((g.cat, g.signature), 0), 'tema': g.theme,
                 'categoria': g.cat, 'primeira': g.first, 'ultima': g.last, 'assinatura': _a(g.signature, su),
                 'exemplo': _a(g.sample, su),
                 'causas_raiz': [{'causa': _a(g.root_samples.get(r, r), su), 'n': n} for r, n in g.roots.most_common(3)]}
                for g in errors.ranked_groups()],
        },
        'lentidao': {
            'sinais': dict(perf.signal_count),
            'jschronos': [{k: v for k, v in r.items() if k not in ('v', 'horas')} for r in perf.chrono_summary()],
            'por_dataset': [{k: v for k, v in r.items() if k != 'v'} for r in perf.dataset_attribution()],
            'exportacoes': perf.exports,
            'intervalos_sem_log': len(perf.gaps),
            'max_default_task': perf.max_task_thread,
            'linhas_por_hora': dict(sorted(perf.lines_by_hour.items())),
        },
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path
