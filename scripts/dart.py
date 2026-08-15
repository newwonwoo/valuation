#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DART 정기보고서 원문 추출기 (API 키 불필요)

사용법:
    python dart.py find   <회사명> [YYYYMMDD_시작]      # 접수번호 조회
    python dart.py toc    <접수번호>                     # 목차 출력
    python dart.py get    <접수번호> <섹션명일부>        # 섹션 본문 추출
    python dart.py facts  <접수번호>                     # 핵심 수치 자동 추출

예:
    python dart.py find 제너셈 20260801
    python dart.py get 20260813000859 수주상황
    python dart.py facts 20260813000859

주의:
  - web_fetch는 dart.fss.or.kr 조회 URL을 거부한다. 반드시 이 스크립트(requests)를 쓸 것.
  - KIND(kind.krx.co.kr) 원문은 403으로 막힌다. DART 뷰어 경로만 동작한다.
  - 세션 쿠키가 없으면 detailSearch.ax가 빈 결과를 준다. get('https://dart.fss.or.kr/') 선행 필수.
"""
import sys, re, html, json
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    s.get("https://dart.fss.or.kr/", timeout=30)   # 세션 쿠키 확보 (생략하면 빈 결과)
    return s


def find(s, name, start="20260101", end=None):
    from datetime import date
    end = end or date.today().strftime("%Y%m%d")
    r = s.post("https://dart.fss.or.kr/dsab007/detailSearch.ax",
               data={"currentPage": "1", "maxResults": "15", "maxLinks": "10",
                     "sort": "date", "series": "desc",
                     "textCrpNm": name, "textCrpNm2": name,
                     "startDate": start, "endDate": end,
                     "finalReport": "recent", "reportNamePopYn": "Y"},
               headers={"Referer": "https://dart.fss.or.kr/dsab007/main.do",
                        "X-Requested-With": "XMLHttpRequest"}, timeout=60)
    rcps = re.findall(r"rcpNo=(\d+)", r.text)
    titles = re.findall(r"rcpNo=\d+[^>]*>\s*([^<]{2,80})", r.text)
    out, seen = [], set()
    for i, rc in enumerate(rcps):
        if rc in seen:
            continue
        seen.add(rc)
        t = re.sub(r"\s+", " ", titles[i]).strip() if i < len(titles) else ""
        out.append({"rcpNo": rc, "title": t})
    return out


def sections(s, rcp):
    t = s.get(f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}", timeout=60,
              headers={"Referer": "https://dart.fss.or.kr/dsab007/main.do"}).text
    pat = (r"node[12]\['text'\]\s*=\s*\"([^\"]+)\";[\s\S]{0,500}?"
           r"node[12]\['dcmNo'\]\s*=\s*\"(\d+)\";[\s\S]{0,500}?"
           r"node[12]\['eleId'\]\s*=\s*\"(\d+)\";[\s\S]{0,500}?"
           r"node[12]\['offset'\]\s*=\s*\"(\d+)\";[\s\S]{0,500}?"
           r"node[12]\['length'\]\s*=\s*\"(\d+)\";")
    return [m.groups() for m in re.finditer(pat, t)]


def body(s, rcp, dcm, ele, off, ln):
    u = (f"https://dart.fss.or.kr/report/viewer.do?rcpNo={rcp}&dcmNo={dcm}"
         f"&eleId={ele}&offset={off}&length={ln}&dtd=dart4.xsd")
    r = s.get(u, timeout=60, headers={"Referer": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}"})
    r.encoding = r.apparent_encoding or "utf-8"
    x = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", r.text)
    x = re.sub(r"</(td|th)>", "\t", x)
    x = re.sub(r"</tr>", "\n", x)
    x = re.sub(r"<[^>]+>", " ", x)
    x = html.unescape(x)
    x = re.sub(r"[ \u3000]+", " ", x)
    return re.sub(r"\n\s*\n+", "\n", x)


# 밸류에이션에 실제로 쓰이는 항목만 추출한다. 더 늘리지 말 것 — 노이즈가 판단을 흐린다.
FACTS = {
    "수주잔고":   ("4. 매출 및 수주상황", [r"수주잔고", r"수주현황"]),
    "계약부채":   ("3. 연결재무제표 주석", [r"계약부채", r"유동 선수금"]),
    "차입금":     ("3. 연결재무제표 주석", [r"차입금의 구성내역", r"단기차입금의 내역"]),
    "매출채권연령": ("3. 연결재무제표 주석", [r"연체일을 기준", r"만기 경과"]),
    "대손충당금": ("3. 연결재무제표 주석", [r"대손충당금 변동"]),
    "고객집중도": ("3. 연결재무제표 주석", [r"주요 고객에 대한", r"10% 이상을 차지"]),
    "요약재무":   ("1. 요약재무정보", [r"자산총계", r"부채총계"]),
}


def facts(s, rcp, window=1400):
    secs = sections(s, rcp)
    cache = {}
    out = {}
    for key, (secname, pats) in FACTS.items():
        hit = next((x for x in secs if x[0].strip() == secname), None)
        if not hit:
            hit = next((x for x in secs if secname.split(". ")[-1] in x[0]), None)
        if not hit:
            out[key] = None
            continue
        k = hit[1] + hit[2]
        if k not in cache:
            cache[k] = body(s, rcp, *hit[1:])
        b = cache[k]
        chunks = []
        for p in pats:
            m = re.search(p, b)
            if m:
                chunks.append(b[max(0, m.start() - 200): m.start() + window])
        out[key] = "\n---\n".join(chunks) if chunks else None
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    s = session()
    cmd = sys.argv[1]
    if cmd == "find":
        name = sys.argv[2]
        start = sys.argv[3] if len(sys.argv) > 3 else "20260101"
        for it in find(s, name, start):
            print(it["rcpNo"], it["title"])
    elif cmd == "toc":
        for t in sections(s, sys.argv[2]):
            print(t[0])
    elif cmd == "get":
        rcp, key = sys.argv[2], sys.argv[3]
        for t in sections(s, rcp):
            if key in t[0]:
                print(f"===== {t[0]} =====")
                print(body(s, rcp, *t[1:]))
    elif cmd == "facts":
        res = facts(s, sys.argv[2])
        for k, v in res.items():
            print(f"\n{'=' * 60}\n[{k}]")
            print(v if v else "  (미검출 — 해당 항목 없음 또는 목차명 상이. toc로 확인할 것)")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
