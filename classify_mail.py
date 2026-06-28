#!/usr/bin/env python3
"""필요할 때만 oMLX(로컬 LLM 서버)를 켜서 네이버 메일을 분류 → 폴더 이동.

흐름(1회 실행):
  omlx start  →  IMAP에서 안 읽은 메일 수집  →  oMLX OpenAI API로 분류
  →  네이버 폴더로 이동  →  omlx stop (서버/모델 RAM 회수)

의존성: 표준 라이브러리만 사용 (pip 설치 불필요). /usr/bin/python3 로 실행 가능.
LLM은 oMLX HTTP 서버가 담당하므로 mlx 파이썬 패키지가 없어도 된다.
"""
import base64
import email
import json
import os
import re
import subprocess
import sys
import time
import imaplib
import urllib.request
from email.header import decode_header

from rules import rule_classify


# ------------------------- 설정 -------------------------
def load_config():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.environ.get("MAILCLS_CONFIG", os.path.join(here, "config.json"))
    if not os.path.exists(path):
        sys.exit("설정 파일이 없습니다: %s  (config.example.json을 복사해 만드세요)" % path)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # api_key가 비어 있으면 oMLX settings.json에서 읽어온다
    if not cfg.get("api_key"):
        sp = os.path.expanduser(cfg.get("omlx_settings_path", "~/.omlx/settings.json"))
        try:
            with open(sp, "r", encoding="utf-8") as f:
                cfg["api_key"] = json.load(f).get("auth", {}).get("api_key", "")
        except Exception as e:
            print("경고: oMLX settings.json에서 api_key를 못 읽음(%s). config의 api_key를 쓰세요." % e)
    return cfg


# ------------------- IMAP modified UTF-7 -------------------
# 네이버 한글 폴더명(예: 분류/광고)을 IMAP mailbox 이름으로 인코딩 (RFC 3501)
def imap_utf7_encode(s):
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if 0x20 <= ord(c) <= 0x7E:
            out.append("&-" if c == "&" else c)
            i += 1
        else:
            j = i
            while j < n and not 0x20 <= ord(s[j]) <= 0x7E:
                j += 1
            b64 = base64.b64encode(s[i:j].encode("utf-16-be")).decode("ascii")
            out.append("&" + b64.rstrip("=").replace("/", ",") + "-")
            i = j
    return "".join(out)


# ------------------------- 메일 파싱 -------------------------
def _best_decode(b, charset):
    """bytes를 적절한 인코딩으로 디코딩 (네이버 메일의 EUC-KR/CP949 대응)."""
    encs = ([charset] if charset else []) + ["utf-8", "cp949", "euc-kr"]
    for e in encs:
        try:
            return b.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


def decode_mime(value):
    """MIME 인코딩 헤더 디코딩. 비표준/깨진(surrogateescape) 제목도 복구 시도."""
    if not value:
        return ""
    try:
        parts = decode_header(value)
    except Exception:
        parts = [(value, None)]
    out = []
    for data, charset in parts:
        if isinstance(data, bytes):
            out.append(_best_decode(data, charset))
        else:
            # 이미 str: 원바이트가 surrogateescape로 깨졌을 수 있어 복원 후 재디코딩
            try:
                raw = data.encode("latin1", "surrogateescape")
                out.append(_best_decode(raw, charset))
            except UnicodeEncodeError:
                out.append(data)  # 정상 유니코드 문자열 → 그대로
    return "".join(out)


def extract_text(msg, limit):
    text, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if str(part.get("Content-Disposition", "")).startswith("attachment"):
                continue
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                chunk = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ctype == "text/plain" and not text:
                text = chunk
            elif ctype == "text/html" and not html:
                html = chunk
    else:
        try:
            payload = msg.get_payload(decode=True)
            chunk = payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""
        except Exception:
            chunk = ""
        (html, text) = (chunk, "") if msg.get_content_type() == "text/html" else ("", chunk)

    body = re.sub(r"<[^>]+>", " ", text or html)
    return re.sub(r"\s+", " ", body).strip()[:limit]


# ------------------------- 분류(oMLX) -------------------------
def build_prompt(categories, subject, sender, body):
    return (
        "너는 이메일 분류기다. 아래 메일을 정확히 하나의 카테고리로 분류한다.\n"
        "카테고리: %s\n"
        "- 광고: 마케팅, 뉴스레터, 세일/할인, 프로모션, 쿠폰, 게임 이벤트, 구독 콘텐츠 알림\n"
        "- 결제: 결제내역, 영수증, 청구서, 주문/배송, 금융 거래 내역\n"
        "- 보안: 로그인 알림, 보안 경고, 인증코드, 비밀번호 변경 등 계정 보안 관련\n"
        "- 고지: 이용약관/개인정보처리방침 개정, 개인정보 이용내역 통지, 정보제공 사실 통보 등 법정 의무 고지\n"
        "- 업무: 채용, 면접, 일/계약 등 실제 업무 관련\n"
        "- 개인: 실제 사람이 개인적으로 보낸 사적인 메일 (자동발송이 아님)\n"
        "- 기타: 위 어디에도 안 맞는 서비스 공지/점검 등\n\n"
        "보낸사람: %s\n제목: %s\n본문: %s\n\n"
        "위 카테고리 중 하나의 단어만 출력하라. 설명 금지." %
        ("/".join(categories.keys()), sender, subject, body)
    )


def classify(cfg, categories, subject, sender, body):
    payload = {
        "model": cfg["model"],
        "max_tokens": 8,
        "temperature": 0,
        "messages": [{"role": "user", "content": build_prompt(categories, subject, sender, body)}],
    }
    req = urllib.request.Request(
        cfg["omlx_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg.get("api_key", "")},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read().decode("utf-8"))
    text = out["choices"][0]["message"]["content"].strip()
    for name in categories:
        if name in text:
            return name
    return "기타"


# ------------------------- 서버 제어 -------------------------
def omlx(cfg, *args):
    subprocess.run([cfg.get("omlx_bin", "omlx"), *args], check=False)


def wait_ready(cfg, timeout=120):
    url = cfg["omlx_url"].rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cfg.get("api_key", "")})
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            time.sleep(2)
    return False


# ------------------------- 메인 -------------------------
def main():
    cfg = load_config()
    categories = cfg["categories"]
    dry_run = cfg.get("dry_run", True)
    manage = cfg.get("manage_server", True)

    if manage:
        print("[0/4] oMLX 서버 기동...")
        omlx(cfg, "start", "--timeout", "120")
        if not wait_ready(cfg):
            sys.exit("oMLX 서버가 준비되지 않았습니다.")

    M = None
    try:
        # --- 1. IMAP 수집 ---
        print("[1/4] 네이버 IMAP 접속...")
        M = imaplib.IMAP4_SSL(cfg["imap_host"], cfg.get("imap_port", 993))
        M.login(cfg["username"], cfg["app_password"])
        M.select(cfg.get("source_folder", "INBOX"))

        typ, data = M.uid("SEARCH", None, "UNSEEN")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        limit = cfg.get("max_mails_per_run", 0) or 0  # 0 또는 미설정 = 전체
        if limit > 0:
            uids = uids[:limit]
        if not uids:
            print("분류할 안 읽은 메일이 없습니다. 종료.")
            return

        # --- 분류: 헤더만 먼저 읽어 규칙 적용, 애매한 건만 본문+LLM ---
        print("[1/3] 분류 시작: 안 읽은 메일 %d건 (헤더 우선, 미확정만 본문+LLM)\n" % len(uids))
        buckets = {}
        n_rule = n_llm = 0
        for idx, uid in enumerate(uids, 1):
            typ, d = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
            if typ != "OK" or not d or not d[0]:
                continue
            hdr = email.message_from_bytes(d[0][1])
            subject = decode_mime(hdr.get("Subject", ""))
            sender = decode_mime(hdr.get("From", ""))

            label = rule_classify(sender, subject)
            if label is None:  # 규칙 미확정 → 본문까지 받아 LLM 판단
                t2, d2 = M.uid("FETCH", uid, "(BODY.PEEK[])")
                body = ""
                if t2 == "OK" and d2 and d2[0]:
                    body = extract_text(email.message_from_bytes(d2[0][1]),
                                        cfg.get("body_chars", 800))
                label = classify(cfg, categories, subject, sender, body)
                via, n_llm = "LLM", n_llm + 1
            else:
                via, n_rule = "규칙", n_rule + 1

            buckets.setdefault(label, []).append(uid)
            print("  %4d/%d [%-2s|%s] %s" % (idx, len(uids), label, via, subject[:50]))
        print("\n  (규칙 %d건 · LLM %d건)" % (n_rule, n_llm))

        # --- 폴더 이동 (uid가 많을 수 있어 200개씩 나눠서 처리) ---
        print("\n[2/3] 폴더 이동:")
        read_cats = set(cfg.get("read_categories", ["광고", "고지"]))
        for label, ids in buckets.items():
            target = categories.get(label, categories.get("기타"))
            enc = '"%s"' % imap_utf7_encode(target)
            mark_read = label in read_cats
            print("  %s → %s : %d건%s%s" %
                  (label, target, len(ids), "  [읽음처리]" if mark_read else "",
                   "  (DRY-RUN)" if dry_run else ""))
            if dry_run:
                continue
            # 상위 폴더부터 차례로 생성 (이미 있으면 NO 응답 → 무시)
            segs = target.split("/")
            for i in range(1, len(segs) + 1):
                M.create('"%s"' % imap_utf7_encode("/".join(segs[:i])))
            for k in range(0, len(ids), 200):
                uid_set = b",".join(ids[k:k + 200]).decode()
                if mark_read:  # 이동 전에 읽음(\Seen) 표시 → 이동 후에도 유지
                    M.uid("STORE", uid_set, "+FLAGS", "(\\Seen)")
                typ, _ = M.uid("MOVE", uid_set, enc)
                if typ != "OK":  # MOVE 미지원 서버 대비: COPY + 삭제플래그 + EXPUNGE
                    M.uid("COPY", uid_set, enc)
                    M.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
                    M.expunge()
    finally:
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass
        if manage:
            print("\n[3/3] oMLX 서버 종료 → RAM 회수")
            omlx(cfg, "stop")
    print("완료.")


if __name__ == "__main__":
    main()
