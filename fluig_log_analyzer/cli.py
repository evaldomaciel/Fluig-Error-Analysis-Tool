"""Linha de comando: fluig-log-analyzer <arquivo.log> [opções]."""
import argparse
import glob
import os
import sys

from . import __version__
from .analysis import Analysis, Config

EPILOG = """exemplos:
  fluig-log-analyzer server.log
  fluig-log-analyzer server.log --pdf relatorio.pdf
  fluig-log-analyzer "logs/server.log*" --tudo saida/
  fluig-log-analyzer server.log.gz --niveis ERROR,WARN --top 40 --csv csv/
"""


def _hours(value):
    try:
        start, end = (int(x) for x in value.split('-'))
        assert 0 <= start < end <= 24
        return start, end
    except Exception:
        raise argparse.ArgumentTypeError('use o formato INICIO-FIM, ex.: 8-18')


def build_parser():
    ap = argparse.ArgumentParser(
        prog='fluig-log-analyzer',
        description='Analisa server.log do TOTVS Fluig: agrupa erros, identifica causas de lentidão e gera '
                    'relatórios em texto, CSV, JSON e PDF.',
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('logs', nargs='+', help='arquivo(s) de log (aceita curingas e .gz)')

    out = ap.add_argument_group('saídas')
    out.add_argument('--pdf', metavar='ARQUIVO', help='gera o relatório em PDF (requer reportlab)')
    out.add_argument('--txt', metavar='ARQUIVO', help='grava o relatório em texto')
    out.add_argument('--csv', metavar='PASTA', help='grava tabelas em CSV (separador ;) nesta pasta')
    out.add_argument('--json', metavar='ARQUIVO', help='grava os dados agregados em JSON')
    out.add_argument('--tudo', metavar='PASTA', help='gera PDF, TXT, CSV e JSON nesta pasta')
    out.add_argument('-q', '--silencioso', action='store_true', help='não imprime o relatório no terminal')

    an = ap.add_argument_group('análise')
    an.add_argument('--niveis', default='ERROR,FATAL,SEVERE', help='níveis tratados como erro (padrão: %(default)s)')
    an.add_argument('--top', type=int, default=25, help='itens por lista (padrão: %(default)s)')
    an.add_argument('--encoding', default='utf-8', help='codificação do log (padrão: %(default)s)')
    an.add_argument('--horario-comercial', type=_hours, default=(8, 18), metavar='INICIO-FIM',
                    help='faixa para procurar intervalos sem log (padrão: 8-18)')
    an.add_argument('--intervalo-sem-log', type=float, default=5.0, metavar='SEG',
                    help='intervalo mínimo sem log a reportar (padrão: %(default)s)')
    an.add_argument('--dataset-lento', type=float, default=10.0, metavar='SEG',
                    help='média a partir da qual um dataset é considerado lento (padrão: %(default)s)')
    an.add_argument('--sync-longo', type=int, default=600, metavar='SEG',
                    help='duração de sincronização de dataset considerada longa (padrão: %(default)s)')
    an.add_argument('--exportacao-grande', type=int, default=300, metavar='SEG',
                    help='duração de exportação considerada grande (padrão: %(default)s)')
    an.add_argument('--investigar', type=int, default=3, metavar='N',
                    help='quantas assinaturas investigar em detalhe (padrão: %(default)s)')
    an.add_argument('--mostrar-usuarios', action='store_true',
                    help='exibe logins nos relatórios (por padrão são anonimizados)')
    an.add_argument('--autor', default='', help='autor gravado nos metadados do PDF')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return ap


def _expand(patterns):
    files = []
    for p in patterns:
        matches = sorted(glob.glob(p)) if any(c in p for c in '*?[') else [p]
        files.extend(matches)
    missing = [f for f in files if not os.path.isfile(f)]
    return files, missing


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    files, missing = _expand(args.logs)
    if missing or not files:
        for m in missing or args.logs:
            print(f'Arquivo não encontrado: {m}', file=sys.stderr)
        return 2

    if args.tudo:
        os.makedirs(args.tudo, exist_ok=True)
        base = os.path.splitext(os.path.basename(files[0]))[0]
        args.pdf = args.pdf or os.path.join(args.tudo, f'relatorio_{base}.pdf')
        args.txt = args.txt or os.path.join(args.tudo, f'relatorio_{base}.txt')
        args.csv = args.csv or os.path.join(args.tudo, 'csv')
        args.json = args.json or os.path.join(args.tudo, f'dados_{base}.json')

    if args.pdf:
        from .report_pdf import HAS_REPORTLAB
        if not HAS_REPORTLAB:
            print('Para gerar PDF instale o reportlab:  pip install reportlab', file=sys.stderr)
            return 3

    cfg = Config(
        levels=tuple(l.strip().upper() for l in args.niveis.split(',') if l.strip()),
        top=args.top, encoding=args.encoding, business_hours=args.horario_comercial,
        gap_seconds=args.intervalo_sem_log, slow_dataset_seconds=args.dataset_lento,
        sync_alert_seconds=args.sync_longo, export_alert_seconds=args.exportacao_grande,
        investigate_top=args.investigar, show_users=args.mostrar_usuarios)

    total_mb = sum(os.path.getsize(f) for f in files) / 1024 / 1024
    print(f'Analisando {len(files)} arquivo(s), {total_mb:,.0f} MB...', file=sys.stderr)
    analysis = Analysis(files, cfg).run(
        progress=lambda n: print(f'  ... {n:,} linhas lidas', file=sys.stderr))
    print(f'Concluído em {analysis.elapsed:.0f} s.', file=sys.stderr)

    from .report_text import render
    text = render(analysis)
    if not args.silencioso:
        print(text)
    if args.txt:
        with open(args.txt, 'w', encoding='utf-8') as fh:
            fh.write(text)
        print(f'TXT : {args.txt}', file=sys.stderr)
    if args.csv:
        from .exporters import write_csv
        paths = write_csv(analysis, args.csv)
        print(f'CSV : {len(paths)} arquivos em {args.csv}', file=sys.stderr)
    if args.json:
        from .exporters import write_json
        write_json(analysis, args.json)
        print(f'JSON: {args.json}', file=sys.stderr)
    if args.pdf:
        from .report_pdf import write_pdf
        write_pdf(analysis, args.pdf, author=args.autor)
        print(f'PDF : {args.pdf}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
