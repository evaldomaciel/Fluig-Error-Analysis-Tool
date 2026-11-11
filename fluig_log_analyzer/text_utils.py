import re

_NORMALIZERS = [
    (re.compile(r'Usu[aá]rio Executor\s*[:=]\s*\S+(\s+-\s+[A-ZÀ-Ú ]+(?= -))?'), 'Usuário Executor=<USER>'),
    (re.compile(r'colleagueId=[^,\]\s]+'), 'colleagueId=<USER>'),
    (re.compile(r'Hostname=[^\]\s]+'), 'Hostname=<HOST>'),
    (re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'), '<UUID>'),
    (re.compile(r'(https?://[^\s?"\']+)\?\S*'), r'\1?<QS>'),
    (re.compile(r'0x[0-9a-fA-F]+'), '<HEX>'),
    (re.compile(r'@[0-9a-fA-F]{5,}'), '@<HEX>'),
    (re.compile(r'<br\s*/?>|</?b>'), ' '),
    (re.compile(r'\d+'), 'N'),
    (re.compile(r'\s+'), ' '),
]

_USER_PATTERNS = [
    (re.compile(r'(Usu[aá]rio Executor\s*[:=]\s*)\S+(\s+-\s+[A-ZÀ-Ú ]+(?= -))?'), r'\1<usuário>'),
    (re.compile(r'(colleagueId=)[^,\]\s]+'), r'\1<usuário>'),
    (re.compile(r"(USER_CODE\s*=\s*')[^']+"), r'\1<usuário>'),
    (re.compile(r'(\b[Uu]ser(?:name|Id)?\s*[:=]\s*)[\w.@-]+'), r'\1<usuário>'),
]


_TAG_RE = re.compile(r'<br\s*/?>|</?[a-zA-Z][^<>]{0,40}>')


def strip_tags(text):
    return re.sub(r'\s+', ' ', _TAG_RE.sub(' ', text)).strip()


def normalize(text):
    for rx, rep in _NORMALIZERS:
        text = rx.sub(rep, text)
    return text.strip()


def anonymize(text):
    for rx, rep in _USER_PATTERNS:
        text = rx.sub(rep, text)
    return text


def short(text, n):
    text = re.sub(r'\s+', ' ', text or '')
    return text if len(text) <= n else text[:n - 3] + '...'


def percentile(values, p):
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * p))]


def fmt_int(n):
    return f'{n:,}'.replace(',', '.')


def fmt_seconds(secs):
    secs = int(round(secs))
    if secs < 60:
        return f'{secs} s'
    if secs < 3600:
        return f'{secs // 60} min {secs % 60:02d} s'
    return f'{secs // 3600} h {(secs % 3600) // 60:02d} min'
