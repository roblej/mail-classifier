# 네이버 메일 자동 분류기

네이버 메일을 **광고 / 결제 / 보안 / 고지 / 업무 / 개인 / 기타**로 분류하고 해당 폴더로 이동시킵니다.

## 두 가지 버전

| | oMLX 버전 | Hermes 버전 |
|---|---|---|
| 디렉토리 | `omlx/` | `hermes/` |
| LLM | 로컬 oMLX 서버 (macOS) | DeepSeek API (클라우드) |
| 실행 환경 | macOS + oMLX | Linux + Hermes Agent |
| 서버 관리 | 자동 시작/종료 | 불필요 (API 호출) |
| 비용 | 무료 (로컬) | API 사용량만큼 |

## 공통 특징

- **규칙 선처리**: `rules.py`에서 명확한 메일은 즉시 분류 (전체의 ~92%)
- **LLM 판단**: 규칙으로 못 잡은 애매한 메일만 LLM에 위임
- **표준 라이브러리만 사용**: pip 설치 불필요

## Hermes 버전 사용법

```bash
cd hermes
cp config.example.json config.json
chmod 600 config.json
```

`config.json`에 네이버 아이디, 앱비밀번호, DeepSeek API 키를 입력하세요.

```bash
# dry-run (이동 없이 분류 결과만 확인)
python3 classify_mail.py

# 실제 분류하려면 config.json에서 "dry_run": false
```

## oMLX 버전 사용법

[omlx/README.md](omlx/) 참고.
