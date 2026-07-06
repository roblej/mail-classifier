#!/usr/bin/env python3
"""DeepSeek API로 네이버 메일을 분류 → 폴더 이동.

흐름(1회 실행):
  IMAP에서 안 읽은 메일 수집 → 규칙 선처리(rules.py) → 애매한 건 DeepSeek API로 분류
  → 네이버 폴더로 이동

클라우드 API를 사용하므로 서버 관리 불필요.
의존성: 표준 라이브러리만 사용 (pip 설치 불필요).
"""
import base64
import email
import json
import os
import re
import ssl
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

    # api_key가 비어 있으면 환경변수에서 읽기
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("DEEPSEEK_API_KEY", "")
    if not cfg.get("api_key"):
        print("경고: api_key가 설정되지 않았습니다. config.json 또는 DEEPSEEK_API_KEY 환경변수를 확인하세요.")
    return cfg


# ------------------- IMAP modified UTF-7 -------------------
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
    encs = ([charset] if charset else []) + ["utf-8", "cp949", "euc-kr"]
    for e in encs:
        try:
            return b.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


def decode_mime(value):
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
            try:
                raw = data.encode("latin1", "surrogateescape")
                out.append(_best_decode(raw, charset))
            except UnicodeEncodeError:
                out.append(data)
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


# ------------------------- 분류 (DeepSeek API) -------------------------
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
        "model": cfg.get("model", "deepseek-chat"),
        "max_tokens": 8,
        "temperature": 0,
        "messages": [{"role": "user", "content": build_prompt(categories, subject, sender, body)}],
    }
    api_url = cfg.get("api_url", "https://api.deepseek.com/v1").rstrip("/")
    req = urllib.request.Request(
        api_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg.get("api_key", ""),
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
        out = json.loads(r.read().decode("utf-8"))
    text = out["choices"][0]["message"]["content"].strip()
    for name in categories:
        if name in text:
            return name
    return "기타"


# ------------------------- IMAP 재접속 래퍼 -------------------------
class IMAPClient:
    _TRANSIENT = (imaplib.IMAP4.abort, OSError)

    def __init__(self, cfg, retries=3):
        self.cfg = cfg
        self.retries = retries
        self._conn = None
        self._selected = None
        self.connect()

    def connect(self):
        c = imaplib.IMAP4_SSL(self.cfg["imap_host"], self.cfg.get("imap_port", 993))
        c.login(self.cfg["username"], self.cfg["app_password"])
        self._conn = c
        if self._selected:
            c.select(self._selected)

    def reconnect(self):
        try:
            if self._conn is not None:
                self._conn.logout()
        except Exception:
            pass
        self._conn = None
        self.connect()

    def select(self, mailbox):
        self._selected = mailbox
        return self._conn.select(mailbox)

    def _call(self, method, *args):
        for attempt in range(1, self.retries + 1):
            try:
                return getattr(self._conn, method)(*args)
            except self._TRANSIENT as e:
                if attempt == self.retries:
                    raise
                wait = 2 * attempt
                print("  ! IMAP %s 연결 끊김(%s) → %d초 후 재접속·재시도 %d/%d"
                      % (method, e, wait, attempt, self.retries))
                time.sleep(wait)
                try:
                    self.reconnect()
                except self._TRANSIENT as re_err:
                    print("  ! 재접속 실패(%s), 계속 재시도" % re_err)

    def uid(self, *args):
        return self._call("uid", *args)

    def create(self, *args):
        return self._call("create", *args)

    def expunge(self, *args):
        return self._call("expunge", *args)

    def logout(self):
        try:
            if self._conn is not None:
                self._conn.logout()
        except Exception:
            pass
        self._conn = None


# ------------------------- 메인 -------------------------
def main():
    cfg = load_config()
    categories = cfg["categories"]
    dry_run = cfg.get("dry_run", True)

    print("[1/3] DeepSeek API 사용 (모델: %s)" % cfg.get("model", "deepseek-chat"))

    M = None
    try:
        # --- 1. IMAP 수집 ---
        print("[1/3] 네이버 IMAP 접속...")
        M = IMAPClient(cfg)
        M.select(cfg.get("source_folder", "INBOX"))

        typ, data = M.uid("SEARCH", None, "UNSEEN")
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        limit = cfg.get("max_mails_per_run", 0) or 0
        if limit > 0:
            uids = uids[:limit]
        if not uids:
            print("분류할 안 읽은 메일이 없습니다. 종료.")
            return

        # --- 분류: 헤더만 먼저 읽어 규칙 적용, 애매한 건만 본문+LLM ---
        print("[2/3] 분류 시작: 안 읽은 메일 %d건 (헤더 우선, 미확정만 본문+LLM)\n" % len(uids))
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
            if label is None:
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

        # --- 폴더 이동 ---
        print("\n[3/3] 폴더 이동:")
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
            segs = target.split("/")
            for i in range(1, len(segs) + 1):
                M.create('"%s"' % imap_utf7_encode("/".join(segs[:i])))
            for k in range(0, len(ids), 200):
                uid_set = b",".join(ids[k:k + 200]).decode()
                if mark_read:
                    M.uid("STORE", uid_set, "+FLAGS", "(\\Seen)")
                typ, _ = M.uid("MOVE", uid_set, enc)
                if typ != "OK":
                    M.uid("COPY", uid_set, enc)
                    M.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
                    M.expunge()
    finally:
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass
    print("완료.")


if __name__ == "__main__":
    main()
