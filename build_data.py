#!/usr/bin/env python3
"""Build data files for the Khawatir Quraniyah bilingual reader site.

Outputs into site/data/:
  surahs.json  - shared surah table (names + start pages in each book)
  ar.json      - per-page Arabic text (OCR preferred, text-layer fallback)
  en.json      - per-page English text
And into site/pdf/:
  ar.pdf, en.pdf - the PDFs used by the PDF-pair reader
"""
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
AR_PDF = ROOT / "ar" / "khawatir-quraniyah-ar-amr-khaled.pdf"
EN_PDFS = [ROOT / "en" / f"quranic-reflections-en-part{i}-of-3.pdf" for i in (1, 2, 3)]
OCR_DIR = Path("/tmp/arocr/pages")
TMP = Path("/tmp/kqsite")
TMP.mkdir(exist_ok=True)

SURAH_NAMES_AR = [
    "الفاتحة", "البقرة", "آل عمران", "النساء", "المائدة", "الأنعام", "الأعراف",
    "الأنفال", "التوبة", "يونس", "هود", "يوسف", "الرعد", "إبراهيم", "الحجر",
    "النحل", "الإسراء", "الكهف", "مريم", "طه", "الأنبياء", "الحج", "المؤمنون",
    "النور", "الفرقان", "الشعراء", "النمل", "القصص", "العنكبوت", "الروم", "لقمان",
    "السجدة", "الأحزاب", "سبإ", "فاطر", "يس", "الصافات", "ص", "الزمر", "غافر",
    "فصلت", "الشورى", "الزخرف", "الدخان", "الجاثية", "الأحقاف", "محمد", "الفتح",
    "الحجرات", "ق", "الذاريات", "الطور", "النجم", "القمر", "الرحمن", "الواقعة",
    "الحديد", "المجادلة", "الحشر", "الممتحنة", "الصف", "الجمعة", "المنافقون",
    "التغابن", "الطلاق", "التحريم", "الملك", "القلم", "الحاقة", "المعارج", "نوح",
    "الجن", "المزمل", "المدثر", "القيامة", "الإنسان", "المرسلات", "النبإ",
    "النازعات", "عبس", "التكوير", "الانفطار", "المطففين", "الانشقاق", "البروج",
    "الطارق", "الأعلى", "الغاشية", "الفجر", "البلد", "الشمس", "الليل", "الضحى",
    "الشرح", "التين", "العلق", "القدر", "البينة", "الزلزلة", "العاديات", "القارعة",
    "التكاثر", "العصر", "الهمزة", "الفيل", "قريش", "الماعون", "الكوثر", "الكافرون",
    "النصر", "اللهب", "الإخلاص", "الفلق", "الناس",
]
assert len(SURAH_NAMES_AR) == 114

# Core name tokens (the distinctive part after "Surat Al-/An-/..." ) used to
# find EN mentions for surahs 58-114. Each entry is a list of alternates.
# Match rule: normalized mention == core (article-less names) or
# ^a(l|n|s|t|d|z|r|sh|th)?CORE$ (prefixed names).
EN_CORES_58_114 = [
    ["mujadila"], ["hashr"], ["mumtahanah"], ["saff"], ["jumuah", "jumua"],
    ["munafiqun"], ["taghabun"], ["talaq"], ["tahrim", "tahreem"],
    ["mulk", "mulq"], ["qalam"], ["haqqah"], ["maarij"], ["nuh"], ["jinn"],
    ["muzzammil", "muzammil"], ["muddathir", "mudathir", "muddaththir", "mudaththir"],
    ["qiyamah", "kiyamah"], ["insan"], ["mursalat"], ["naba"], ["naziat"],
    ["abasa", "abas"], ["takwir"], ["infitar"], ["mutaffifin", "mutafifin"],
    ["inshiqaq"], ["buruj"], ["tariq"], ["ala"], ["ghashiyah"], ["fajr"],
    ["balad"], ["shams"], ["layl"], ["duha"], ["inshirah", "sharh", "sharah"],
    ["tin"], ["alaq"], ["qadr"], ["bayyinah"], ["zalzalah"], ["adiyat"],
    ["qariah"], ["takathur"], ["asr"], ["humazah"], ["fil", "feel"],
    ["quraysh"], ["maun"], ["kawthar"], ["kafirun"], ["nasr"], ["masad"],
    ["ikhlas"], ["falaq"], ["nas"],
]
EN_DISPLAY_58_114 = [
    "Al-Mujadila (The Pleader)", "Al-Hashr (The Exile)", "Al-Mumtahanah (She Who Is Examined)",
    "As-Saff (The Ranks)", "Al-Jumu'ah (Friday)", "Al-Munafiqun (The Hypocrites)",
    "At-Taghabun (Dispossession)", "At-Talaq (Divorce)", "At-Tahrim (The Prohibition)",
    "Al-Mulk (The Sovereignty)", "Al-Qalam (The Pen)", "Al-Haqqah (The Reality)",
    "Al-Ma'arij (The Ascending Stairways)", "Nuh (Noah)", "Al-Jinn (The Jinn)",
    "Al-Muzzammil (The Enshrouded One)", "Al-Muddaththir (The Cloaked One)",
    "Al-Qiyamah (The Resurrection)", "Al-Insan (The Human)", "Al-Mursalat (Those Sent Forth)",
    "An-Naba' (The Great News)", "An-Nazi'at (Those Who Drag Forth)", "Abasa (He Frowned)",
    "At-Takwir (The Overthrowing)", "Al-Infitar (The Cleaving)", "Al-Mutaffifin (The Defrauding)",
    "Al-Inshiqaq (The Sundering)", "Al-Buruj (The Mansions of Stars)", "At-Tariq (The Night Visitor)",
    "Al-A'la (The Most High)", "Al-Ghashiyah (The Overwhelming)", "Al-Fajr (The Dawn)",
    "Al-Balad (The City)", "Ash-Shams (The Sun)", "Al-Layl (The Night)",
    "Ad-Duha (The Forenoon)", "Al-Inshirah (The Expansion)", "At-Tin (The Fig)",
    "Al-Alaq (The Clot)", "Al-Qadr (The Decree)", "Al-Bayyinah (The Clear Proof)",
    "Az-Zalzalah (The Earthquake)", "Al-Adiyat (The Courser)", "Al-Qari'ah (The Calamity)",
    "At-Takathur (The Rivalry in Increase)", "Al-Asr (The Declining Day)", "Al-Humazah (The Slanderer)",
    "Al-Fil (The Elephant)", "Quraysh", "Al-Ma'un (Small Kindnesses)", "Al-Kawthar (The Abundance)",
    "Al-Kafirun (The Disbelievers)", "An-Nasr (The Divine Support)", "Al-Masad (The Palm Fiber)",
    "Al-Ikhlas (The Sincerity)", "Al-Falaq (The Daybreak)", "An-Nas (Mankind)",
]
assert len(EN_CORES_58_114) == len(EN_DISPLAY_58_114) == 57

AR_DIAC = re.compile(r"[ً-ْٰـ]")
AR_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي",
                         "ؤ": "و", "ئ": "ي", "ة": "ه", "ء": ""})
BIDI_MARKS = dict.fromkeys(range(0x202A, 0x202F), None)
BIDI_MARKS.update(dict.fromkeys(range(0x2066, 0x206A), None))


def strip_bidi(s: str) -> str:
    return s.translate(BIDI_MARKS)


def norm_ar(s: str) -> str:
    s = unicodedata.normalize("NFKC", strip_bidi(s))
    s = AR_DIAC.sub("", s)
    s = s.translate(AR_ALEF)
    return re.sub(r"\s+", "", s)


CANON_AR_NORM = [norm_ar(n) for n in SURAH_NAMES_AR]


def match_ar_name(raw: str, prev_idx: int) -> int:
    """Match a header name fragment to a canonical surah index (monotonic)."""
    nn = norm_ar(raw)
    if not nn:
        return -1
    cands = []
    for i, c in enumerate(CANON_AR_NORM):
        if i < prev_idx:
            continue
        if nn == c or (len(nn) >= 2 and c.startswith(nn)):
            cands.append(i)
    if not cands:
        return -1
    return min(cands)


def detect_ar_surah(page_text: str, prev_idx: int) -> int:
    lines = [strip_bidi(l).strip() for l in page_text.splitlines() if l.strip()]
    head = lines[:6]
    # pass 1: lines containing سورة
    for l in head:
        if "سورة" in l:
            m = re.search(r"سورة\s*(.+)$", l)
            if m:
                frag = re.sub(r"[0-9٠-٩]+", "", m.group(1))
                frag = frag.strip(" .،:؛!؟?-–—")
                idx = match_ar_name(frag, prev_idx)
                if idx >= 0:
                    return idx
    # pass 2: bare short line equal to a surah name
    for l in head:
        if len(l) <= 25:
            idx = match_ar_name(l, prev_idx)
            if idx >= 0:
                return idx
    return prev_idx  # inherit previous page's surah


CONTENTS_RE = re.compile(r"^\s*(Surat\s+.+?)\s*\.{3,}\s*(\d+)\s*$")
SURAT_TOK_RE = re.compile(r"Surat\s+([A-Za-z'’\-]+)", re.I)


def norm_en(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def parse_contents(pages: list[str]) -> list[tuple[str, int]]:
    """Ordered (heading, printed_page) pairs from a part's contents page."""
    out = []
    for pg in pages[:15]:
        for line in pg.splitlines():
            m = CONTENTS_RE.match(line.strip())
            if m and m.group(1).strip().lower().startswith("surat"):
                out.append((m.group(1).strip(), int(m.group(2))))
        if out:
            break
    return out


ARTICLELESS = {"nuh", "yaseen", "sad", "qaf", "hud", "luqman", "quraysh", "saba",
               "maryam", "yasin", "tin"}
EN_PREFIXED = re.compile(r"^a(?:l|n|s|t|d|z|r|sh|th)?(.+)$")


def tok_matches_core(tok: str, core: str) -> bool:
    if core in ARTICLELESS:
        return tok == core
    m = EN_PREFIXED.match(tok)
    return bool(m) and m.group(1) == core


def en_mention_pages(en_pages: list[str], cores: list[str], strict: bool = True) -> list[int]:
    """Pages containing a 'Surat <core>' mention (0-based).

    strict=True requires heading-ish context (gloss/paren/comma/colon/EOL after
    the name); strict=False accepts any mention on a reasonably short line.
    """
    hits = []
    cores_n = [norm_en(c) for c in cores]
    for i, pg in enumerate(en_pages):
        for line in pg.splitlines():
            s = line.strip()
            if "surat" not in s.lower() or len(s) > 140:
                continue
            for m in SURAT_TOK_RE.finditer(s):
                tok = norm_en(m.group(1))
                if not any(tok_matches_core(tok, c) for c in cores_n):
                    continue
                if strict:
                    rest = s[m.end():m.end() + 40]
                    if not (re.match(r"[\s]*(\(|,|-|:|\)|$)", rest) or rest.strip() == ""):
                        continue
                hits.append(i)
                break
            if hits and hits[-1] == i:
                break
    return hits


def unwrap(text: str) -> str:
    paras, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        if s:
            cur.append(s)
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras)


FURNITURE_RES = [
    re.compile(r"AmrKhaled\.net", re.I),
    re.compile(r"^\s*Contents\s*$", re.I),
    re.compile(r"^\s*\d{1,3}\s*$"),
    re.compile(r"private purposes", re.I),
    re.compile(r"original\s*$", re.I),
]


def is_contents_page(page: str) -> bool:
    leaders = sum(1 for l in page.splitlines() if re.search(r"\.{5,}\s*\d+\s*$", l))
    return leaders >= 3


def clean_en_page(page: str, drop_whole: bool) -> str:
    if drop_whole:
        return ""
    out = []
    for line in page.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if any(r.search(s) for r in FURNITURE_RES):
            continue
        if re.search(r"\.{5,}\s*\d+\s*$", s):
            continue
        out.append(s)
    return unwrap("\n".join(out))


def main() -> None:
    (SITE / "pdf").mkdir(parents=True, exist_ok=True)
    (SITE / "data").mkdir(parents=True, exist_ok=True)

    subprocess.run(["cp", str(AR_PDF), str(SITE / "pdf" / "ar.pdf")], check=True)
    subprocess.run(["pdfunite", *[str(p) for p in EN_PDFS], str(SITE / "pdf" / "en.pdf")], check=True)

    subprocess.run(["pdftotext", "-layout", str(AR_PDF), str(TMP / "ar.txt")], check=True)
    for i, p in enumerate(EN_PDFS, 1):
        subprocess.run(["pdftotext", "-layout", str(p), str(TMP / f"en{i}.txt")], check=True)
    subprocess.run(["pdftotext", "-layout", str(SITE / "pdf" / "en.pdf"), str(TMP / "en_all.txt")], check=True)

    ar_raw_pages = []
    raw = (TMP / "ar.txt").read_text(encoding="utf-8", errors="replace")
    ar_raw_pages = raw.split("\f")
    while ar_raw_pages and not ar_raw_pages[-1].strip():
        ar_raw_pages.pop()
    en_pages = (TMP / "en_all.txt").read_text(encoding="utf-8", errors="replace").split("\f")
    while en_pages and not en_pages[-1].strip():
        en_pages.pop()
    print(f"AR pages: {len(ar_raw_pages)}  EN pages: {len(en_pages)}")

    # ---- AR page texts (OCR preferred) + surah detection on display text ----
    ocr_by_num = {}
    for f in sorted(OCR_DIR.glob("pg-*.txt")):
        m = re.search(r"(\d+)", f.stem)
        if m:
            ocr_by_num[int(m.group(1))] = f
    print(f"OCR pages: {len(ocr_by_num)}")

    ar_texts, ar_surah_idx = [], []
    prev = -1
    for i in range(1, len(ar_raw_pages) + 1):
        ocr = ocr_by_num.get(i)
        if ocr and ocr.stat().st_size > 20:
            t = ocr.read_text(encoding="utf-8", errors="replace")
            lines = t.splitlines()
            kept = []
            for j, l in enumerate(lines):
                if j < 3 and re.match(r"^\s*سورة\s+\S+.*\d+\s*$", strip_bidi(l).strip()):
                    continue
                kept.append(l)
            text = unwrap("\n".join(kept))
            detect_src = "\n".join(lines[:6])
        else:
            text = unwrap(strip_bidi(ar_raw_pages[i - 1]).replace("ـ", ""))
            detect_src = ar_raw_pages[i - 1]
        idx = detect_ar_surah(detect_src, prev)
        ar_surah_idx.append(idx)
        ar_texts.append(text)
        prev = idx

    # ---- EN: contents printed pages -> merged PDF pages ----
    part_texts = []
    for i in (1, 2, 3):
        t = (TMP / f"en{i}.txt").read_text(encoding="utf-8", errors="replace")
        ps = t.split("\f")
        while ps and not ps[-1].strip():
            ps.pop()
        part_texts.append(ps)
    # merged-page bases: cumulative page counts of parts 1..k-1
    part_bases = [0]
    for ps in part_texts[:-1]:
        part_bases.append(part_bases[-1] + len(ps))
    print("part bases:", part_bases, "part lens:", [len(p) for p in part_texts])

    en_headings = []  # (heading, printed_page, part_idx)
    for k, ps in enumerate(part_texts):
        for h, pp in parse_contents(ps):
            en_headings.append((h, pp, k))
    print(f"EN contents entries: {len(en_headings)}")

    surahs = []
    for n in range(114):
        surahs.append({"n": n + 1, "nameAr": SURAH_NAMES_AR[n]})

    # surahs covered by contents (first len(en_headings) surahs, in order)
    covered = min(len(en_headings), 114)
    for n in range(covered):
        h, pp, k = en_headings[n]
        surahs[n]["nameEn"] = re.sub(r"\s*-\s*\(", " (", h.replace(" - (", " ("))
        surahs[n]["enPage"] = part_bases[k] + pp  # 1-based merged page

    # surahs 58+: find first heading-ish mention in merged EN text
    part30_page = None
    for i, pg in enumerate(en_pages):
        if "Surahs of the Thirtieth Part" in pg:
            part30_page = i + 1  # keep last occurrence (early refs exist)
    print("part30 divider page:", part30_page)

    en_floor = part_bases[2] if len(part_bases) > 2 else 0  # surahs 58+ live in part 3
    prev_en = surahs[covered - 1].get("enPage") or en_floor if covered else en_floor
    for n in range(covered, 114):
        surahs[n]["nameEn"] = EN_DISPLAY_58_114[n - 57]
        cores = EN_CORES_58_114[n - 57]
        strict_hits = [h + 1 for h in en_mention_pages(en_pages, cores, strict=True)]
        loose_hits = [h + 1 for h in en_mention_pages(en_pages, cores, strict=False)]
        # prefer a mention after the previous surah's page (book is ~ordered),
        # else earliest mention inside part 3, else the part-30 section
        hits = [h for h in strict_hits if h > prev_en] or [h for h in strict_hits if h > en_floor]
        if not hits:
            hits = [h for h in loose_hits if h > prev_en] or [h for h in loose_hits if h > en_floor]
            if hits:
                surahs[n]["enLoose"] = True
        if hits:
            surahs[n]["enPage"] = hits[0]
            prev_en = max(prev_en, hits[0])
        else:
            surahs[n]["enPage"] = part30_page
            surahs[n]["enGuess"] = True

    # AR start pages from running headers
    ar_start = {}
    for i, idx in enumerate(ar_surah_idx):
        if idx >= 0 and idx not in ar_start:
            ar_start[idx] = i + 1
    for n in range(114):
        surahs[n]["arPage"] = ar_start.get(n)

    # AR fallback: for surahs with no header page, find first OCR page whose
    # normalized text mentions "سورة <name>" (not preceded by ال = "السورة").
    # Floor = first surah's page: earlier pages are the book's own contents,
    # which list every surah name.
    ar_floor = surahs[0].get("arPage") or 15
    ocr_norm = []
    for i in range(1, len(ar_raw_pages) + 1):
        ocr = ocr_by_num.get(i)
        if ocr and ocr.stat().st_size > 20:
            ocr_norm.append(norm_ar(ocr.read_text(encoding="utf-8", errors="replace")))
        else:
            ocr_norm.append("")
    ar_mention = {}
    for n in range(114):
        needle = norm_ar("سورة " + SURAH_NAMES_AR[n])
        for i, nt in enumerate(ocr_norm):
            if i + 1 < ar_floor:
                continue
            pos = 0
            while True:
                j = nt.find(needle, pos)
                if j < 0:
                    break
                # reject matches that are really "السورة<name>" (definite article)
                if not nt[max(0, j - 2):j].endswith("ال"):
                    ar_mention[n] = i + 1
                    break
                pos = j + 1
            if n in ar_mention:
                break
    # arPage: running header is authoritative. A "سورة X" mention only overrides
    # it when it sits just before the header page (sub-heading at a section
    # boundary, e.g. Quraysh starts mid-page under Al-Fil's header). Earlier
    # mentions are passing references inside other surahs' sections.
    for n in range(114):
        hp = surahs[n].get("arPage")
        mp = ar_mention.get(n)
        if hp and mp and 0 < hp - mp <= 10:
            surahs[n]["arPage"] = mp
        elif mp and not hp:
            surahs[n]["arPage"] = mp
            surahs[n]["arMention"] = True

    # Verified: both books end at Surat Quraysh (106); surahs 107-114 are not
    # covered by this edition (selective thematic coverage, not exhaustive).
    for n in range(106, 114):
        surahs[n]["arPage"] = None
        surahs[n]["enPage"] = None
        surahs[n]["notCovered"] = True

    # AR-uncovered in the 75-106 range: no "سورة X" mention exists anywhere in
    # the OCR text (verified by bare-name scan — only incidental common words).
    # These sit inside the book's selective part-29/30 thematic sections.
    AR_GAP_IDXS = {77, 78, 80, 81, 82, 84, 86, 87, 88, 89, 91, 93, 96, 97, 98,
                   99, 100, 103}  # 0-based surah indices
    for n in AR_GAP_IDXS:
        if n < 114 and surahs[n].get("arPage") is None:
            surahs[n]["arNotCovered"] = True

    # ---- EN per-page surah index + cleaned text ----
    en_surah_idx = [-1] * len(en_pages)
    for n in range(114):
        ep = surahs[n].get("enPage")
        if ep:
            en_surah_idx[ep - 1] = n if en_surah_idx[ep - 1] == -1 else en_surah_idx[ep - 1]
    # fill forward: page belongs to latest surah whose enPage <= page
    cur = -1
    enpage_sorted = sorted([(s.get("enPage") or 10**9, n) for n, s in enumerate(surahs)])
    enpage_sorted = [(p, n) for p, n in enpage_sorted if p < 10**9]
    for i in range(len(en_pages)):
        pg = i + 1
        while enpage_sorted and enpage_sorted[0][0] <= pg:
            cur = enpage_sorted.pop(0)[1]
        en_surah_idx[i] = cur

    en_texts = [clean_en_page(pg, is_contents_page(pg)) for pg in en_pages]

    # ---- write ----
    (SITE / "data" / "surahs.json").write_text(
        json.dumps(surahs, ensure_ascii=False, indent=1), encoding="utf-8")
    (SITE / "data" / "ar.json").write_text(json.dumps(
        {"pages": [{"p": i + 1, "s": s, "t": t} for i, (s, t) in enumerate(zip(ar_surah_idx, ar_texts))]},
        ensure_ascii=False), encoding="utf-8")
    (SITE / "data" / "en.json").write_text(json.dumps(
        {"pages": [{"p": i + 1, "s": s, "t": t} for i, (s, t) in enumerate(zip(en_surah_idx, en_texts))]},
        ensure_ascii=False), encoding="utf-8")

    missing_ar = [s["nameAr"] for s in surahs if s.get("arPage") is None]
    no_en = [s["n"] for s in surahs if not s.get("enPage")]
    guessed = [s["n"] for s in surahs if s.get("enGuess")]
    print(f"surahs: {len(surahs)}")
    print(f"missing AR: {missing_ar}")
    print(f"missing EN page: {no_en}")
    print(f"EN guessed pages: {guessed}")
    print("data files written to", SITE / "data")


if __name__ == "__main__":
    main()
