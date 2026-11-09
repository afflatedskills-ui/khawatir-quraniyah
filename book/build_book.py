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
    merged = {}
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alignment-cache', 'merged.json')
    if os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    if os.path.exists(f'{ALN}/out/align.json'):
        for k, v in json.load(open(f'{ALN}/out/align.json')).items():
            merged[int(k)] = v
    for f in glob.glob(f'{OUT2}/*.json'):
        r = json.load(open(f))
        cur = merged.get(int(r['n']))
        if cur is None or (r.get('ok') and not cur.get('ok')):
            merged[int(r['n'])] = r
    return merged

def assemble():
    AL = gather()
    sj = json.load(open(f'{SITE}/data/surahs.json'))
    surahs = []
    for n in range(1, 115):
        s = sj[n - 1]
        if s.get('notCovered'):
            surahs.append({'n': n, 'nameAr': s['nameAr'], 'nameEn': s.get('nameEn', ''),
                           'notCovered': True, 'pairs': []})
            continue
        arB = load_blocks(n, 'ar'); enB = load_blocks(n, 'en')
        e = {'n': n, 'nameAr': s['nameAr'], 'nameEn': s.get('nameEn', ''),
             'arOnly': bool(arB) and not enB, 'enOnly': bool(enB) and not arB,
             'pairs': []}
        if arB and enB:
            r = AL.get(n)
            pairs = r['pairs'] if (r and r.get('ok')) else mech_align(arB, enB)
            e['aligned'] = bool(r and r.get('ok'))
            e['fallback'] = not e['aligned']
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
