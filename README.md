# 네이버 메일 로컬 LLM 분류기 (oMLX)

필요할 때만 로컬 LLM 서버(oMLX)를 켜서 네이버 메일을
**광고 / 결제 / 보안 / 고지 / 업무 / 개인 / 기타**로 분류하고 해당 폴더로 이동시킵니다.

## 동작 방식
1회 실행하면:
`omlx start` → IMAP에서 안 읽은 메일 수집 → **규칙 선처리(rules.py)로 명확한 건 즉시 확정**,
애매한 건만 oMLX LLM이 판단 → 네이버 폴더로 이동 → `omlx stop`(서버·모델 RAM 회수).
평소엔 서버가 꺼져 있어 메모리를 쓰지 않습니다. (실측: 받은편지함의 ~92%는 규칙만으로 분류)

분류 기준을 바꾸려면 `rules.py`의 발신자 도메인/키워드 목록을 편집하세요.

- LLM은 oMLX HTTP 서버(`127.0.0.1:8000`)가 담당 → 파이썬 mlx 패키지 불필요
- IMAP은 표준 라이브러리 `imaplib`만 사용 → **pip 설치/venv 불필요**, `/usr/bin/python3`로 실행
- API 키는 `~/.omlx/settings.json`의 `auth.api_key`를 자동으로 읽음 (하드코딩 X)

## 1. 네이버 메일 설정
1. 네이버 메일 → 환경설정 → **POP3/IMAP 설정** → IMAP/SMTP **사용함**
2. 2단계 인증 사용 시 **애플리케이션 비밀번호** 발급
3. 서버: `imap.naver.com` / 993 / SSL

## 2. 설정 파일
```bash
cd ~/mail-classifier
cp config.example.json config.json
chmod 600 config.json
```
`config.json`에 네이버 **아이디 / 앱비밀번호**만 채우면 됩니다.
(`api_key`는 비워두면 oMLX settings.json에서 자동으로 읽음)

주요 옵션:
- `model` : `mlx-community/Qwen3-4B-Instruct-2507-4bit` (기본)
- `manage_server` : `true`면 실행 시 omlx를 켜고 끝나면 끔
- `dry_run` : 처음엔 `true`로 두고 **이동 없이 분류 결과만** 확인

## 3. 시험 실행 (dry-run)
```bash
python3 classify_mail.py
```
분류가 마음에 들면 `config.json`에서 `"dry_run": false`로 변경.

## 4. 자동 실행 (launchd, 하루 1번 · 매일 9시)
```bash
cp com.user.mailclassifier.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.mailclassifier.plist

# 즉시 한 번 테스트
launchctl start com.user.mailclassifier
tail -f run.log
```
시간 변경은 plist의 `StartCalendarInterval`(Hour/Minute) 수정 후
`launchctl unload`/`load` 다시.

중지/제거:
```bash
launchctl unload ~/Library/LaunchAgents/com.user.mailclassifier.plist
```

## 모델 바꾸기
`config.json`의 `"model"`만 교체 (oMLX가 인식하는 모델 id):
- `mlx-community/Qwen3-4B-Instruct-2507-4bit` (기본, 권장)
- 더 가볍게: `mlx-community/gemma-4-e4b-it-4bit`
