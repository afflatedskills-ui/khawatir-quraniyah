#!/usr/bin/env python3
"""Build data/book.json — the single-column bilingual book.

Pipeline: extract per-surah AR/EN paragraph blocks from data/{ar,en}.json
-> align block indices AR<->EN per surah via agy (Gemini) -> assemble pairs.

Usage:
  python3 build_book.py extract    # write /tmp/kqbook blocks + manifest (idempotent)
  python3 build_book.py align      # run agy alignment for unfinished surahs
  python3 build_book.py assemble   # write site/data/book.json
  python3 build_book.py            # all three

Alignment results cache in /tmp/kqalign/ (survives until reboot; copy
out2/*.json + merged.json somewhere durable if you want to keep them).
Surahs that fail agy validation fall back to mechanical fraction
alignment — still ordered and complete, just approximate.
"""
import json, re, os, subprocess, sys, glob, time
from concurrent.futures import ThreadPoolExecutor, as_completed

SITE = os.path.dirname(os.path.abspath(__file__)) + '/..'
SITE = os.path.normpath(SITE)
BOOK = '/tmp/kqbook'
ALN = '/tmp/kqalign'
OUT2 = ALN + '/out2'
AGY = os.path.expanduser('~/.local/bin/agy')
WORKERS = 4

# ---------- extraction ----------

def clean_blocks(t):
    bl = [b.strip() for b in re.split(r'\n\s*\n', t) if b.strip()]
    out = []
    for b in bl:
        if re.fullmatch(r'(خواطر قرآنية\s*)+', b):
            continue
        if re.fullmatch(r'[\d\s\.\-–—]+', b):
            continue
        if len(b) < 10 and out:
            out[-1] += ' ' + b
        elif len(b) >= 10:
            out.append(b)
    return out

def extract():
    os.makedirs(BOOK, exist_ok=True)
    ar = json.load(open(f'{SITE}/data/ar.json'))['pages']
    en = json.load(open(f'{SITE}/data/en.json'))['pages']
    sj = json.load(open(f'{SITE}/data/surahs.json'))

    def pick(pages, sidx, s, key):
        t = '\n'.join(p['t'] for p in pages if p['s'] == sidx and p['t'].strip())
        if len(t) > 200:
            return t
        pg = s.get(key)
        if not pg:
            return ''
        nxt = min([x[key] for x in sj if x.get(key) and x[key] > pg] or [9999])
        return '\n'.join(p['t'] for p in pages if pg <= p['p'] < nxt and p['t'].strip())

    manifest = {}
    for n in range(1, 115):
        s = sj[n - 1]
        if s.get('notCovered'):
            continue
        ab = clean_blocks(pick(ar, n - 1, s, 'arPage'))
        eb = clean_blocks(pick(en, n - 1, s, 'enPage'))
        manifest[n] = {'arBlocks': len(ab), 'enBlocks': len(eb),
                       'arCh': sum(len(b) for b in ab), 'enCh': sum(len(b) for b in eb)}
        if ab:
            open(f'{BOOK}/{n:03d}_ar.txt', 'w').write(
                '\n\n'.join(f'[{i}] {b}' for i, b in enumerate(ab)))
        if eb:
            open(f'{BOOK}/{n:03d}_en.txt', 'w').write(
                '\n\n'.join(f'[{i}] {b}' for i, b in enumerate(eb)))
    json.dump(manifest, open(f'{BOOK}/manifest.json', 'w'))
    print('extracted', sum(1 for m in manifest.values() if m['arBlocks'] and m['enBlocks']), 'both-covered surahs')

# ---------- alignment ----------

def load_blocks(n, lang):
    f = f'{BOOK}/{n:03d}_{lang}.txt'
    if not os.path.exists(f):
        return []
    out = []
    for l in open(f).read().split('\n\n'):
        m = re.match(r'\[(\d+)\] (.*)', l, re.S)
        if m:
            out.append(m.group(2))
    return out

def make_prompt(n, arB, enB):
    total = sum(len(b) for b in arB) + sum(len(b) for b in enB)
    if total <= 20000:
        arT = '\n\n'.join(f'[{i}] {b}' for i, b in enumerate(arB))
        enT = '\n\n'.join(f'[{i}] {b}' for i, b in enumerate(enB))
        note = 'Each block is a full paragraph.'
    else:
        arT = '\n'.join(f'[{i}] {b[:110]}' for i, b in enumerate(arB))
        enT = '\n'.join(f'[{i}] {b[:110]}' for i, b in enumerate(enB))
        note = 'Each line is "[index] first ~110 chars" of a paragraph block.'
    return (f'Align two block lists from the same book section. {note} Arabic original vs '
            f'English translation of the same book section (Amr Khaled, Khawatir Qur\'aniyah). '
            f'Order is preserved but paragraph boundaries differ.\n\n'
            f'Output ONLY a JSON array of pairs: {{"ar": [indices], "en": [indices]}} '
            f'covering EVERY index exactly once on both sides, in order. '
            f'Group consecutive indices that translate each other.\n\n'
            f'ARABIC:\n{arT}\n\nENGLISH:\n{enT}\n')

def parse_align(txt):
    m = re.search(r'\[.*\]', txt or '', re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.JSONDecoder().raw_decode(m.group(0))[0]
        except Exception:
            return None

def validate(al, na, ne):
    if not isinstance(al, list) or not al:
        return False
    au, eu = [], []
    for p in al:
        if not isinstance(p, dict):
            return False
        au += p.get('ar', []); eu += p.get('en', [])
    return (sorted(au) == list(range(na)) and sorted(eu) == list(range(ne))
            and au == sorted(au) and eu == sorted(eu))

def mech_align(arB, enB):
    ta = sum(len(b) for b in arB) or 1; te = sum(len(b) for b in enB) or 1
    af = []; c = 0.0
    for b in arB:
        af.append((c / ta, (c + len(b)) / ta)); c += len(b)
    assign = [[] for _ in arB]; pos = 0.0
    for j, b in enumerate(enB):
        mid = (pos + len(b) / 2) / te; pos += len(b) + 1
        for i, (f0, f1) in enumerate(af):
            if f0 <= mid < f1 or i == len(af) - 1:
                assign[i].append(j); break
    return [{'ar': [i], 'en': assign[i]} for i in range(len(arB))]

# ---------- section re-slicing ----------
# A few regions were extracted across section boundaries: guessed page
# ranges pulled a neighbouring part's shared "conclusion" section (the
# 25 identical "Thirtieth Part Conclusion" files) or duplicated another
# surah's section. For those regions we ignore the per-surah block files
# and re-derive blocks from data/{ar,en}.json over explicit page ranges,
# splitting the combined section at surah headers. Source files under
# /tmp/kqbook are left untouched (provenance preserved).
#
# RESLICE entries: (arPages, enPages, surahs, introSurah, extraSplits)
#   introSurah: surah that receives the leading intro blocks (before the
#     first surah header); None = leave intro where the neighbouring
#     surah's own block file already keeps it.
#   extraSplits: {lang: [(bucketSurah, regex, newSurah)]} — within a
#     header bucket, move blocks from the first regex match onward into
#     newSurah's bucket (for sections whose header is mid-sentence).

RESLICE = [
    ((393, 409), (330, 338), [58, 59, 60, 61], None,
     {'ar': [(60, r'سوره\s+الصف\s+واضح', 61)]}),
    ((425, 430), (352, 356), list(range(78, 107)), 80, {}),
]

# Surahs whose English block file is a shared multi-surah section while the
# Arabic file is already specific to this surah. For these we keep only this
# surah's dedicated "Surat X" subsection (plus the section intro when this
# surah opens the file); sibling-surah subsections belong to their own
# entries. Arabic is left whole — its section boundaries are not reliably
# header-delimited, and where both sides span the same surahs (75-77) the
# pairing is already consistent.
EN_TRIM = {11, 36, 45, 46, 49, 68, 70, 71}

def trim_en_blocks(n, enB):
    """Return enB reduced to surah n's dedicated section, or enB unchanged."""
    if n not in EN_TRIM or not enB:
        return enB
    bk = _split_region(enB, 'en', _en_name_map(), _ar_name_list())
    intro = bk.pop(0, [])
    first = next((k for k in bk if k != 0), None)
    keep = list(bk.get(n, []))
    if intro and first == n:
        keep = intro + keep
    return keep or enB

_AR_DIAC = re.compile('[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]')  # harakat, dagger alef, tatweel, qc marks
def _norm_ar(s):
    s = _AR_DIAC.sub('', s)
    for a, b in (('أ','ا'),('إ','ا'),('آ','ا'),('ٱ','ا'),('ة','ه'),('ى','ي')):
        s = s.replace(a, b)
    return re.sub(r'\s+', ' ', s).strip()

# English header-token -> surah. Built from surahs.json nameEn plus the
# spelling variants the book actually uses in section headers.
_EN_ALIAS = {
    'almujadalah': 58, 'almujadila': 58, 'almumtahina': 60,
    'almumtahanah': 60, 'assaf': 61, 'assaff': 61, 'aljumuaa': 62,
    'aljumua': 62, 'aljumuaah': 62, "aljumu'ah": 62, 'atteen': 95,
    'alfeel': 105, 'alfil': 105, 'annasr': 110, 'alshams': 91,
    'aldhuha': 93, 'adduha': 93, 'abasa': 80, 'abbasa': 80,
    'attariq': 86, 'altariq': 86, 'alaq': 96, 'alaalaq': 96,
}
_EN_HDR = re.compile(r"^\s*=?\s*Surat\s+([A-Z][A-Za-z'’\-]+)")
_AR_HDR = re.compile(r'^سوره\s+')

def _en_name_map():
    m = {}
    for s in json.load(open(f'{SITE}/data/surahs.json')):
        base = s.get('nameEn', '').split('(')[0].strip()
        base = re.sub(r'^Surat\s+', '', base, flags=re.I)
        key = re.sub(r'[^a-z]', '', base.lower())
        if key:
            m[key] = s['n']
    m.update(_EN_ALIAS)
    return m

def _ar_name_list():
    out = []
    for s in json.load(open(f'{SITE}/data/surahs.json')):
        k = _norm_ar(s['nameAr'])
        if k:
            out.append((k, s['n']))
    return sorted(out, key=lambda x: -len(x[0]))

def _en_hdr_num(block, en2n):
    m = _EN_HDR.match(block)
    if not m:
        return None
    tok = re.sub(r"[^a-z]", '', m.group(1).lower())
    return en2n.get(tok)

def _ar_hdr_num(block, ar_names):
    nb = _norm_ar(block)
    m = _AR_HDR.match(nb)
    if not m:
        return None
    rest = nb[m.end():]
    for name, num in ar_names:
        if rest.startswith(name):
            return num
    return None

def _region_blocks(lang, p0, p1):
    pages = json.load(open(f'{SITE}/data/{lang}.json'))['pages']
    t = '\n\n'.join(p['t'] for p in pages
                    if p0 <= p['p'] <= p1 and p['t'].strip())
    return clean_blocks(t)

def _split_region(bs, lang, en2n, ar_names):
    buckets = {}
    cur = 0
    for b in bs:
        num = _en_hdr_num(b, en2n) if lang == 'en' else _ar_hdr_num(b, ar_names)
        if num:
            cur = num
        buckets.setdefault(cur, []).append(b)
    return buckets

def resliced_blocks():
    """Return {surah: {'ar': [...], 'en': [...]}} for re-sliced regions."""
    en2n = _en_name_map()
    ar_names = _ar_name_list()
    out = {}
    for ar_rng, en_rng, surahs, intro_to, extra in RESLICE:
        for lang, rng in (('ar', ar_rng), ('en', en_rng)):
            p0, p1 = rng
            bk = _split_region(_region_blocks(lang, p0, p1), lang, en2n, ar_names)
            intro = bk.pop(0, [])
            for bnum, pat, newn in extra.get(lang, []):
                if bnum in bk:
                    idx = next((i for i, b in enumerate(bk[bnum])
                                if re.search(pat, _norm_ar(b) if lang == 'ar' else b)),
                               len(bk[bnum]))
                    bk.setdefault(newn, []).extend(bk[bnum][idx:])
                    bk[bnum] = bk[bnum][:idx]
            for n in surahs:
                bl = bk.get(n)
                if bl:
                    out.setdefault(n, {'ar': [], 'en': []})[lang] = bl
            if intro_to is not None and intro:
                e = out.setdefault(intro_to, {'ar': [], 'en': []})
                e[lang] = intro + e[lang]
    return out

def done_set():
    done = set()
    if os.path.exists(f'{ALN}/out/align.json'):
        done |= {int(k) for k in json.load(open(f'{ALN}/out/align.json'))}
    done |= {int(json.load(open(f))['n']) for f in glob.glob(f'{OUT2}/*.json')}
    return done

def align():
    os.makedirs(OUT2, exist_ok=True)
    man = json.load(open(f'{BOOK}/manifest.json'))
    todo = [int(n) for n, m in man.items() if m['arBlocks'] and m['enBlocks']]
    done = done_set()
    todo = [n for n in todo if n not in done]
    print(f'align: {len(todo)} surahs remaining', flush=True)

    def work(n):
        arB = load_blocks(n, 'ar'); enB = load_blocks(n, 'en')
        res = {'n': n, 'ok': False, 'pairs': None, 'err': None}
        try:
            p = subprocess.run([AGY, '--model', 'gemini-3.8-flash-medium', '--effort', 'medium',
                                '--dangerously-skip-permissions', '--print', make_prompt(n, arB, enB)],
                               capture_output=True, text=True, timeout=900)
            al = parse_align(p.stdout)
            if al is not None and validate(al, len(arB), len(enB)):
                res.update(ok=True, pairs=al)
            else:
                res['err'] = 'parse/validate'
        except Exception as e:
            res['err'] = str(e)[:120]
        if not res['ok']:
            res['pairs'] = mech_align(arB, enB); res['fallback'] = True
        json.dump(res, open(f'{OUT2}/{n}.json', 'w'))
        return res

    d = 0; t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(work, n) for n in todo]
        for f in as_completed(futs):
            r = f.result(); d += 1
            print(f'[{d}/{len(todo)}] s{r["n"]} {"OK" if r["ok"] else "FB"}', flush=True)
    print(f'align done {(time.time()-t0)/60:.1f}min', flush=True)

# ---------- assemble ----------

def gather():
    """Merge durable cache + /tmp out2 results.

    The durable cache can hold older mechanical-fallback entries for surahs
    that later succeeded in out2. Prefer ok=True over fallback; on ties keep
    the fresher out2 result (it was aligned against the current block files).
    """
    merged = {}
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alignment-cache', 'merged.json')
    if os.path.exists(cache):
        for k, v in json.load(open(cache)).items():
            merged[int(k)] = v
    if os.path.exists(f'{ALN}/out/align.json'):
        for k, v in json.load(open(f'{ALN}/out/align.json')).items():
            merged[int(k)] = v
    for f in glob.glob(f'{OUT2}/*.json'):
        r = json.load(open(f))
        n = int(r['n']); cur = merged.get(n)
        if (cur is None or (r.get('ok') and not cur.get('ok'))
                or bool(r.get('ok')) == bool(cur.get('ok'))):
            merged[n] = r
    return merged

def assemble():
    AL = gather()
    sj = json.load(open(f'{SITE}/data/surahs.json'))
    SL = resliced_blocks()
    REGION = set()
    for _, _, ss, _, _ in RESLICE:
        REGION.update(ss)
    surahs = []
    for n in range(1, 115):
        s = sj[n - 1]
        # Surahs inside a re-sliced region that produced no dedicated
        # section (e.g. part-30 surahs the book never discusses) are not
        # covered — do not show a neighbouring section's text for them.
        if s.get('notCovered') or (n in REGION and n not in SL):
            surahs.append({'n': n, 'nameAr': s['nameAr'], 'nameEn': s.get('nameEn', ''),
                           'notCovered': True, 'pairs': []})
            continue
        resliced = n in SL
        if resliced:
            arB, enB = SL[n]['ar'], SL[n]['en']
        else:
            arB = load_blocks(n, 'ar'); enB = load_blocks(n, 'en')
            enB = trim_en_blocks(n, enB)
        e = {'n': n, 'nameAr': s['nameAr'], 'nameEn': s.get('nameEn', ''),
             'arOnly': bool(arB) and not enB, 'enOnly': bool(enB) and not arB,
             'pairs': []}
        if resliced:
            e['resliced'] = True
        if arB and enB:
            if resliced:
                # Re-sliced sections have no model alignment; mechanical
                # pairing is honest and flagged as approximate.
                pairs = mech_align(arB, enB)
                e['aligned'] = False; e['fallback'] = True
            else:
                r = AL.get(n)
                ok = bool(r and r.get('ok')
                          and validate(r['pairs'], len(arB), len(enB)))
                pairs = r['pairs'] if ok else mech_align(arB, enB)
                e['aligned'] = ok
                e['fallback'] = not ok
            for p in pairs:
                e['pairs'].append({'ar': [arB[i] for i in p['ar']],
                                   'en': [enB[j] for j in p['en']]})
        else:
            for b in arB: e['pairs'].append({'ar': [b], 'en': []})
            for b in enB: e['pairs'].append({'ar': [], 'en': [b]})
        surahs.append(e)
    out = f'{SITE}/data/book.json'
    json.dump({'surahs': surahs}, open(out, 'w'), ensure_ascii=False)
    alg = sum(1 for e in surahs if e.get('aligned'))
    fb = sum(1 for e in surahs if e.get('fallback'))
    print(f'book.json {os.path.getsize(out)/1e6:.2f}MB | aligned={alg} fallback={fb}')

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd in ('extract', 'all'): extract()
    if cmd in ('align', 'all'): align()
    if cmd in ('assemble', 'all'): assemble()
