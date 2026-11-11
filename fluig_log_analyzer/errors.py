"""Agrupamento de erros: eventos (erro + stack), assinaturas, incidentes, temas e investigação."""
import re
from collections import Counter, defaultdict

from .text_utils import normalize, strip_tags

AT_RE = re.compile(r'^\s*(at\s|\.\.\.\s*\d+\s+more|Suppressed:)')
FRAME_RE = re.compile(r'^\s*at\s+(.+?)(\(.*\))?\s*$')
CAUSE_RE = re.compile(r'^\s*Caused by:\s*(.*)$')
CUSTOM_RE = re.compile(
    r'Processo:\s*(\S+).*?Executando evento\s*:\s*(\w+?)ERRO AO EXECUTAR SCRIPT -> NOK - (.*)$', re.S)
CUSTOM_NOPROC_RE = re.compile(r'Executando evento\s*:\s*(\w+?)ERRO AO EXECUTAR SCRIPT -> NOK - (.*)$', re.S)
USER_RE = re.compile(r'(?:Usu[aá]rio Executor\s*[:=]\s*|colleagueId=)([^\s,\]]+)')
INSTANCE_RE = re.compile(r'(?:Solicita[cç][aã]o:\s*|processInstanceId=|ProcessInstanceId:\s*)(\d+)')
VENDOR_FRAME_RE = re.compile(r'^(com\.(fluig|datasul|totvs)\.|sun\.org\.mozilla|org\.mozilla)')
SCRIPT_FRAME_RE = re.compile(r'mozilla|javascript|rhino|CustomizationManager|ScriptEngine|\.js[:)]', re.I)
STDOUT_CATS = ('stderr', 'stdout')

THEME_RULES = [
    ('SSL / certificado', r'CertificateExpired|PKIX|trusted certificate|SSLHandshake|CertPathValidator'),
    ('E-mail (SMTP)', r'SMTPSend|EMailSender|Concurrent connections limit|MailConnectException|AuthenticationFailedException'),
    ('Registro de ciência de tarefa (UserKnowledgeTask)',
     r'registerUserKnowledgeTask|UserKnowledgeTask.*different object with the same identifier'),
    ('Banco de dados / transação',
     r'IJ000453|Unable to get managed connection|ARJUNA0121|TransactionReaper|deadlock|lock wait|SQLException|JDBCConnectionException'),
    ('Memória / recursos', r'OutOfMemory|GC overhead|Java heap space|Metaspace|unable to create (new )?native thread'),
    ('Validação de formulário (regra de negócio)',
     r'Obrigat[oó]rio|J[aá] existe um lan[cç]amento|CardIndexListenerException|validateForm.*ERRO AO EXECUTAR SCRIPT'),
    ('Bug em script customizado',
     r'ERRO AO EXECUTAR SCRIPT|TypeError|ReferenceError|EcmaError|ScriptingLog|WrappedException|CardIndexJSListener|ExternalScriptable'),
    ('Permissão / responsabilidade', r'permiss[aã]o|responsabilidade'),
    ('Dataset (sincronização/serviço)', r'sincroniza[cç][aã]o deste dataset|DatasetService'),
    ('Timeout de rede / integração', r'SocketTimeout|Read timed out|connect timed out|ConnectTimeout|UnknownHost|Connection refused'),
    ('Arquivo / volume / indexação', r'LocalStream|File not found|PDFHelper|Solr|private folder|encrypted|NoSuchFile'),
    ('Conexão encerrada pelo cliente (ruído)', r'Broken pipe|Stream is closed|Response already commited|Connection reset'),
    ('Renderização WCM / Freemarker', r'freemarker|Freemarker|RequestPipeline|PageServiceBean|renderTheme'),
    ('Genérico (consequência de outro erro)', r'Erro ao movimentar atividade|Erro ao salvar dados d[eo] formul'),
]
THEME_RULES = [(name, re.compile(rx, re.I)) for name, rx in THEME_RULES]
OTHER_THEME = 'Outros'
NOISE_THEMES = {'Conexão encerrada pelo cliente (ruído)', 'Genérico (consequência de outro erro)',
                'Validação de formulário (regra de negócio)', 'Permissão / responsabilidade'}


def classify(cat, msg, causes):
    blob = ' '.join([cat, msg] + list(causes))
    for rank, (name, rx) in enumerate(THEME_RULES):
        if rx.search(blob):
            return rank, name
    return len(THEME_RULES), OTHER_THEME


def custom_parts(msg):
    m = CUSTOM_RE.search(msg)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    m = CUSTOM_NOPROC_RE.search(msg)
    if m:
        return '(sem processo)', m.group(1), m.group(2).strip()
    return None


class Event:
    __slots__ = ('ts', 'epoch', 'level', 'cat', 'thread', 'msg', 'causes', 'frames', 'context', '_pos')

    def __init__(self, rec, msg=None):
        self.ts = rec.ts
        self.epoch = rec.epoch
        self.level = rec.level
        self.cat = rec.cat
        self.thread = rec.thread
        self.msg = rec.msg if msg is None else msg
        self.causes = []
        self.frames = []
        self.context = None
        self._pos = 0
        c = CAUSE_RE.match(self.msg)
        if c:
            self.causes.append(c.group(1))

    def absorb(self, line):
        c = CAUSE_RE.match(line)
        if c:
            self.causes.append(c.group(1))
            self._pos = 0
            return
        f = FRAME_RE.match(line)
        if f and len(self.frames) < 400:
            self.frames.append((f.group(1).split('//')[-1], self._pos))
            self._pos += 1


class Group:
    __slots__ = ('cat', 'signature', 'count', 'first', 'last', 'theme', 'sample', 'roots', 'root_samples',
                 'threads', 'frames', 'frame_pos', 'script_frames', 'contexts', 'users', 'instances', 'hours')

    def call_chain(self, limit=8):
        """Frames do produto mais frequentes, ordenados do ponto de entrada até onde o erro estoura."""
        if not self.frames:
            return []
        threshold = self.frames.most_common(1)[0][1] * 0.5
        common = [(fr, n) for fr, n in self.frames.items() if n >= threshold]
        closest_to_error = sorted(common, key=lambda fn: self.frame_pos[fn[0]] / fn[1])[:limit]
        return list(reversed(closest_to_error))

    def __init__(self, cat, signature, ev, theme):
        self.cat = cat
        self.signature = signature
        self.count = 0
        self.first = ev.ts
        self.last = ev.ts
        self.theme = theme
        self.sample = ev.msg[:800]
        self.roots = Counter()
        self.root_samples = {}
        self.threads = set()
        self.frames = Counter()
        self.frame_pos = Counter()
        self.script_frames = Counter()
        self.contexts = Counter()
        self.users = Counter()
        self.instances = Counter()
        self.hours = Counter()


class ErrorAnalyzer:
    def __init__(self, levels=('ERROR', 'FATAL', 'SEVERE'), key_len=250):
        self.levels = set(levels)
        self.key_len = key_len
        self.groups = {}
        self.events = 0
        self.level_lines = 0
        self.stderr_stack_lines = 0
        self.incidents = {}
        self.custom = Counter()
        self.custom_users = defaultdict(set)
        self.custom_samples = {}
        self._current = None
        self._stderr_open = {}
        self._thread_ctx = {}

    # ------------------------------------------------------------------ alimentação
    def feed(self, rec):
        if rec.level not in self.levels:
            if rec.cat not in STDOUT_CATS:
                self._thread_ctx[rec.thread] = f'{rec.level} [{rec.short_cat}] {normalize(rec.msg)[:120]}'
            return
        self.level_lines += 1

        if rec.cat in STDOUT_CATS:
            self._feed_stdout(rec)
            return

        ev = Event(rec)
        ev.context = self._thread_ctx.get(rec.thread)
        for line in rec.extra:
            ev.absorb(line)
        self._add(ev)

    def _feed_stdout(self, rec):
        pend = self._stderr_open.get(rec.thread)
        msg = rec.msg
        if AT_RE.match(msg):
            self.stderr_stack_lines += 1
            if pend is not None:
                pend.absorb(msg)
            return
        if CAUSE_RE.match(msg) and pend is not None:
            self.stderr_stack_lines += 1
            pend.absorb(msg)
            return
        if pend is not None:
            self._add(pend)
        ev = Event(rec)
        ev.context = self._thread_ctx.get(rec.thread)
        for line in rec.extra:
            ev.absorb(line)
        self._stderr_open[rec.thread] = ev

    def finish(self):
        for ev in self._stderr_open.values():
            self._add(ev)
        self._stderr_open = {}

    # ------------------------------------------------------------------ agregação
    def _signature(self, ev):
        parts = custom_parts(ev.msg)
        if parts:
            return f'{parts[0]} | {parts[1]} | {normalize(parts[2])}', parts
        return normalize(ev.msg), None

    def _add(self, ev):
        if not ev.msg.strip():
            return
        self.events += 1
        sig, parts = self._signature(ev)
        rank, theme = classify(ev.cat, ev.msg, ev.causes)
        key = (ev.cat, sig[:self.key_len])
        g = self.groups.get(key)
        if g is None:
            g = self.groups[key] = Group(ev.cat, sig[:self.key_len], ev, theme)
        g.count += 1
        g.last = ev.ts
        g.hours[ev.ts[:13]] += 1
        if ev.causes:
            root = normalize(ev.causes[-1])[:300]
            g.roots[root] += 1
            g.root_samples.setdefault(root, ev.causes[-1][:400])
        if len(g.threads) < 200:
            g.threads.add(ev.thread)
        seen = set()
        for fr, pos in ev.frames:
            if fr in seen:
                continue
            seen.add(fr)
            if VENDOR_FRAME_RE.match(fr) and (fr in g.frames or len(g.frames) < 300):
                g.frames[fr] += 1
                g.frame_pos[fr] += pos
            if SCRIPT_FRAME_RE.search(fr) and not fr.startswith('io.undertow'):
                g.script_frames[fr] += 1
        if ev.context and (ev.context in g.contexts or len(g.contexts) < 200):
            g.contexts[ev.context] += 1
        users = {u.lower() for u in USER_RE.findall(ev.msg)}
        for u in users:
            g.users[u] += 1
        for i in set(INSTANCE_RE.findall(ev.msg)):
            if len(g.instances) < 5000 or i in g.instances:
                g.instances[i] += 1

        if parts:
            ck = (parts[0], parts[1], normalize(parts[2])[:200])
            self.custom[ck] += 1
            self.custom_samples.setdefault(ck, strip_tags(parts[2])[:400])
            self.custom_users[ck].update(users)

        ik = (ev.thread, ev.ts)
        inc = self.incidents.get(ik)
        if inc is None:
            self.incidents[ik] = {'rank': rank, 'theme': theme, 'key': key, 'ts': ev.ts}
        elif rank < inc['rank']:
            inc.update(rank=rank, theme=theme, key=key)

    # ------------------------------------------------------------------ consultas
    def ranked_groups(self):
        return sorted(self.groups.values(), key=lambda g: g.count, reverse=True)

    def incidents_by_theme(self):
        return Counter(i['theme'] for i in self.incidents.values())

    def incidents_by_key(self):
        return Counter(i['key'] for i in self.incidents.values())

    def incidents_by_hour(self):
        out = defaultdict(Counter)
        for i in self.incidents.values():
            out[i['ts'][:13]][i['theme']] += 1
        return out

    def theme_groups(self, theme, primary_only=False):
        """Assinaturas do tema; com primary_only, só as que são a assinatura principal de algum incidente."""
        primary = set(self.incidents_by_key()) if primary_only else None
        return [g for g in self.ranked_groups()
                if g.theme == theme and (primary is None or (g.cat, g.signature) in primary)]

    def find_causes(self, pattern):
        """Causas-raiz (texto original) que casam com o padrão, com a contagem de ocorrências."""
        rx = re.compile(pattern, re.I)
        hits = Counter()
        for g in self.groups.values():
            for root, n in g.roots.items():
                if rx.search(root):
                    hits[g.root_samples.get(root, root)] += n
            if not g.roots and rx.search(g.sample):
                hits[g.sample[:300]] += g.count
        return hits

    def investigate(self, top=3):
        """Assinaturas principais (excluindo ruído) com frames, contexto, usuários e solicitações."""
        inc_keys = self.incidents_by_key()
        candidates = sorted(
            (g for g in self.groups.values() if g.theme not in NOISE_THEMES),
            key=lambda g: inc_keys.get((g.cat, g.signature), 0), reverse=True)
        out = []
        seen_themes = set()
        for g in candidates:
            if inc_keys.get((g.cat, g.signature), 0) == 0:
                continue
            if g.theme in seen_themes and g.theme != OTHER_THEME:
                continue
            seen_themes.add(g.theme)
            out.append(g)
            if len(out) >= top:
                break
        return out
