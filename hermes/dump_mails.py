#!/usr/bin/env python3
"""분류 기준 설계용: 최근 메일의 제목+발신자만 가볍게 추출 (본문 X, 읽음표시 X).
사용: python3 dump_mails.py [개수]   (기본 200)
결과: mails_dump.txt 에 '발신자 | 제목' 한 줄씩 저장.
"""
import email
import imaplib
import sys
from classify_mail import load_config, decode_mime


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    cfg = load_config()
    M = imaplib.IMAP4_SSL(cfg["imap_host"], cfg.get("imap_port", 993))
    M.login(cfg["username"], cfg["app_password"])
    M.select(cfg.get("source_folder", "INBOX"))

    typ, data = M.uid("SEARCH", None, "ALL")
    uids = data[0].split() if typ == "OK" and data and data[0] else []
    uids = uids[-n:]  # 최근 n개
    print("총 %d건 추출" % len(uids))

    lines = []
    for uid in uids:
        typ, d = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
        if typ != "OK" or not d or not d[0]:
            continue
        msg = email.message_from_bytes(d[0][1])
        subject = decode_mime(msg.get("Subject", "")).replace("\n", " ").strip()
        sender = decode_mime(msg.get("From", "")).replace("\n", " ").strip()
        lines.append("%s | %s" % (sender, subject))
    M.logout()

    with open("mails_dump.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("저장: mails_dump.txt (%d줄)" % len(lines))


if __name__ == "__main__":
    main()
