#!/usr/bin/env python3
"""규칙 기반 선처리. 실제 받은편지함 200건 분석으로 도출한 발신자/키워드 규칙.

rule_classify(sender, subject) -> 카테고리 문자열 또는 None(애매 → LLM이 판단).
위에서부터 순서대로 평가하며, 먼저 걸리는 규칙이 이긴다.
순서가 중요: 보안/고지가 광고보다 먼저(예: pixiv '로그인'은 보안, pixiv '공개'는 광고).
"""
import re

# --- 보안: 로그인/인증/계정 보안 (가장 민감 → 최우선) ---
SECURITY_KW = [
    "보안 경고", "로그인", "디바이스에서 사용", "iCloud에 로그인",
    "비밀번호가 변경", "비밀번호 변경", "2단계 인증", "패스키", "애플리케이션 비밀번호",
    "인증코드", "인증 코드", "인증번호", "추가인증", "본인확인", "본인 확인",
    "보안 코드", "Steam Guard",
    "verification code", "confirmation code", "sign-in", "sign in", "signed in",
    "access code", "one-time",
]

# --- 고지: 약관/개인정보/정보제공 등 법정 의무 고지 ---
NOTICE_KW = [
    "이용약관", "약관 개정", "약관 변경", "약관 개정 안내", "처리방침", "개인정보처리방침",
    "개인정보 이용내역", "이용내역 안내", "이용내역을 안내", "개인정보 이용제공", "이용제공 내역",
    "정보제공 사실", "정보제공사실", "금융거래정보 제공", "금융거래 정보제공", "고객정보 제공",
    "개인정보 유출", "유출 사실", "유출에 따른", "유출 조회", "유출 사실 통지",
    "수신 동의", "수신동의", "개정 안내", "개정안내",
]

# --- 업무: 채용/일 관련 (퇴사한 이전 직장 규칙은 제거) ---
WORK_KW = ["포지션제안", "포지션 제안", "채용", "면접", "이력서"]

# --- 결제: 결제/영수증/금융 거래 내역 ---
PAY_KW = [
    "결제 내역", "결제가 완료", "결제 완료", "영수증", "청구서", "주문 내역", "배송",
    "상품계약서", "사용내역 안내", "컬쳐캐쉬",
]

# --- 기타: 서비스 공지/점검/종료 ---
MISC_KW = ["서비스 종료", "종료 안내", "중단 안내", "일시 중단", "점검 안내", "소집통지서"]

# --- 광고: 마케팅/뉴스레터/세일 (도메인 + 키워드) ---
# 주의: navercorp.com / pixiv.net 등은 보안·결제·고지에도 쓰이므로 도메인 광고규칙에서 제외
# steampowered.com은 로그인/구매 알림도 보내므로 도메인 광고규칙에서 제외
# (스팀 광고는 '할인'/'찜 목록' 키워드로 잡음)
AD_DOMAINS = [
    "iknewsletter.com", "neuraldsp.com", "square-enix.com",
    "perfectworld.com", "codeweavers.com", "keychron.kr", "playcomet.net", "mega.nz",
    "iknewsletter", "actoz.com",
]
AD_KW = [
    "할인", "세일", "뉴스레터", "프로모션", "쿠폰", "이벤트", "캠페인", "공개했습니다",
    "지원해주셔서", "광고성 정보", "찜 목록", "위시리스트", "wishlist",
    "sale", "% off", "50%", "off!", "登場", "セール",
]


def _domain(sender):
    m = re.search(r"[\w.+-]+@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""


def _has(text, kws):
    t = (text or "").lower()
    return any(k.lower() in t for k in kws)


def rule_classify(sender, subject):
    s = subject or ""
    dom = _domain(sender)

    if _has(s, SECURITY_KW):
        return "보안"
    if _has(s, NOTICE_KW):
        return "고지"
    if _has(s, WORK_KW):
        return "업무"
    if _has(s, PAY_KW):
        return "결제"
    if _has(s, MISC_KW):
        return "기타"
    if any(d in dom for d in AD_DOMAINS) or _has(s, AD_KW) \
            or s.lstrip().startswith("(광고)") or s.lstrip().startswith("[광고]"):
        return "광고"
    return None  # 규칙으로 확정 못 함 → LLM에 위임
