# 노동법 모니터

노동법·고용노동부 고시 변경과 인사 관련 뉴스를 매일 아침 수집해서, **우리 회사에 해당되는 것만** 골라 이메일로 보내고 대시보드에 쌓아두는 도구입니다.

```
07:00  수집    고용노동부 RSS · 법제처 OPEN API · 뉴스
       ↓
       1차 필터  키워드 (LLM 호출 비용 절감)
       ↓
       대비표    개정 건은 신구조문(개정 전/후)을 가져옴
       ↓
       2차 판정  Claude — 조문을 근거로 해당 여부·조치등급 결정
       ↓
       저장     SQLite (중복 발송 방지 + 처리 상태 관리)
       ↓
08:30  발송    이메일 다이제스트 + 대시보드 데이터 갱신
```

---

## 빠른 시작

```bash
pip install -r requirements.txt

# 1) API 키나 네트워크 없이 파이프라인 뒷단부터 확인
python run.py demo
open data/preview.html          # 메일이 어떻게 생겼는지
open dashboard/index.html       # 대시보드

# 2) 법제처 API target 값 확인 (최초 1회, LAW_OC 설정 후)
python run.py probe

# 3) 실제 수집
python run.py collect
python run.py digest --dry-run  # 보내기 전에 내용 확인
python run.py run               # 수집 + 발송 + 대시보드 갱신
```

`config.yaml`의 `company` 블록부터 채우세요. **이 블록의 구체성이 알림 품질을 그대로 결정합니다.** "IT회사"라고만 쓰면 무관한 건이 계속 올라오고, "전원 사무직, 교대제 없음, 파견 미사용"까지 쓰면 대부분 걸러집니다.

---

## 환경변수

| 이름 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ● | 2차 판정용. console.anthropic.com에서 발급 |
| `LAW_OC` | ● | 법제처 OPEN API 아이디 (아래 참조) |
| `SMTP_HOST` / `SMTP_PORT` | ● | 메일 발송. 465면 SSL, 587이면 STARTTLS로 자동 처리 |
| `SMTP_USER` / `SMTP_PASS` | ○ | 인증이 필요한 경우만 |
| `DASHBOARD_URL` | ○ | 메일 하단 "대시보드 열기" 링크 |

### 법제처 OPEN API 신청

1. https://open.law.go.kr → OPEN API 신청 (무료, 승인까지 보통 1영업일)
2. 발급받은 이메일 아이디의 **@ 앞부분**이 `OC` 값입니다. `hong@company.co.kr`이면 `LAW_OC=hong`
3. 잘 되는지 확인:
   ```bash
   curl "https://www.law.go.kr/DRF/lawSearch.do?OC=$LAW_OC&target=admrul&type=JSON&display=5&sort=ddes"
   ```

### target 값 확인 — `python run.py probe`

법제처 DRF API는 서비스마다 `target` 값이 다른데, **틀린 값을 넣으면 에러가 아니라 조용히 빈 결과가 나옵니다.** 한참 못 알아챕니다.

`probe`는 `config.yaml`의 `target_candidates`를 순서대로 실제로 찔러보고 어떤 값이 동작하는지 알려줍니다. 확인된 값을 config에 적고 `verified: true`로 바꾸세요. `verified: false`인 소스는 실패해도 경고만 남기고 넘어가므로, 확인 전에도 나머지 파이프라인은 정상 동작합니다.

행정규칙 조회 결과가 비어 있으면 `config.yaml`의 `sources.law_api.org`(소관부처 코드)를 의심하세요. 활용가이드의 부처코드 표에서 고용노동부 값을 확인해 바꾸면 됩니다. 코드가 틀려도 `eflaw`(법령명 기반 조회)는 정상 동작하므로 전체가 죽지는 않습니다.

---

## 수집 소스

| 소스 | 상태 | 비고 |
|---|---|---|
| 고용노동부 RSS (입법·행정예고 / 알려드립니다 / 정책자료) | 확인 완료 | 인증 불필요 |
| 행정규칙 `admrul` — 고용노동부 고시·훈령·예규 | 확인 완료 | 파라미터 검증됨 |
| 현행법령 `eflaw` — 법령명 기반 조회 | 확인 완료 | |
| 고용노동부 법령해석 | probe 필요 | 부처 유권해석. 뉴스보다 근거로 유용 |
| 노동위원회 결정문 | probe 필요 | 부당해고·부당노동행위 판단 사례 |
| 고용보험심사위원회 결정문 | probe 필요 | |
| 산업재해보상보험재심사위원회 결정문 | probe 필요 | |
| 구글 뉴스 RSS | 확인 완료 | 조치등급 상한 강제 |

### 신구조문 대비

법령·고시가 개정되면 법제처가 **개정 전/후 조문을 나란히 비교한 자료**를 제공합니다. 수집 단계에서 제목에 "개정"이 들어간 건에 대해 이걸 가져와 세 곳에 씁니다.

1. **대시보드** — 항목의 "신구 대비 N개 조문" 버튼에 실제 조문 표시
2. **이메일** — 앞 4개 조문까지 2열 표로
3. **판정 프롬프트** — 이게 가장 큽니다

3번이 핵심입니다. 대비표가 없으면 LLM은 제목과 메타데이터만 보고 요약합니다. 조문 원문이 들어가면 "무엇이 바뀌었나"를 추측이 아니라 근거로 쓰게 되고, `action_level` 판정도 같이 정확해집니다.

모든 개정에 비교 자료가 붙어 있는 건 아니라, 없으면 기존 방식으로 조용히 떨어집니다. 한 실행당 조회 건수는 `diff.max_per_run`으로 제한됩니다.

## 배포

### 실행 — GitHub Actions

`.github/workflows/daily.yml`이 평일 아침 7시(KST)에 돕니다. 리포 Settings → Secrets에 위 환경변수를 등록하면 끝입니다. 서버가 필요 없고, 실행 이력이 Actions 탭에 남아 인수인계가 됩니다.

상태(SQLite DB)는 실행 후 리포에 다시 커밋해서 유지합니다. 규모가 커지면 Supabase 같은 외부 DB로 옮기세요 — `monitor/store.py`의 `Store` 클래스만 갈아끼우면 나머지 코드는 그대로입니다.

### 대시보드 — 접근 통제를 반드시 거세요

`dashboard/`는 `data.json`만 있으면 도는 정적 파일이라 어디든 올라갑니다. **다만 GitHub Pages 공개 배포는 하지 마세요.** `data.json`에는 우리 회사가 어떤 법령 리스크를 검토 중이고 누가 담당인지가 들어 있습니다.

- 사내망 정적 호스팅 (가장 안전)
- Vercel + 비밀번호 보호, 또는 Cloudflare Access
- 사내 SSO가 있으면 그 뒤에 배치

---

## 운영

**첫 2주는 `--dry-run`으로만 돌리세요.** 매일 아침 미리보기를 열어보면서 이런 걸 확인합니다.

- 무관한 건이 계속 올라오는가 → `config.yaml`의 `keywords.exclude`에 추가하거나 `company.notes`를 더 구체적으로
- 놓친 건이 있는가 → `keywords.include`에 키워드 추가
- `action_level` 판정이 너무 관대한가 → `monitor/triage.py`의 `SYSTEM` 프롬프트에서 urgent 기준을 조정

튜닝이 끝난 다음에 실제 발송을 켜는 게 훨씬 빠릅니다. 처음부터 팀 전체에 보내면 노이즈 때문에 2주 안에 아무도 안 읽습니다.

**검수 루프를 넣으세요.** 이 도구는 요약을 만들 뿐 판단하지 않습니다.

- 모든 메일과 카드에 원문 링크가 붙습니다. 요약만 보고 결정하지 마세요.
- `urgent` 항목은 채널 공유로 끝내지 말고 담당자를 지정하고 티켓을 만드세요.
- 월 1회 노무사나 법무 검토로 놓친 건이 없었는지 역추적하는 걸 권합니다. 특히 취업규칙 변경이나 임금 산정으로 이어지는 건은 자동 요약을 근거로 삼으면 안 됩니다.

판정에 실패한 항목은 버리지 않고 `review` 등급으로 올라옵니다. 놓치는 쪽이 잘못 올리는 쪽보다 위험하다는 판단입니다.

---

## 파일 구조

```
config.yaml               회사 정보·키워드·소스. 여기만 고치면 됨
run.py                    CLI 진입점
monitor/
  config.py               설정 로딩, Item 데이터 구조
  sources.py              수집 — RSS / 법제처 DRF / 뉴스 + 신구조문 대비
  probe.py                DRF target 값 확인기
  triage.py               2차 판정 — 회사 컨텍스트 기준 해당 여부·조치등급
  store.py                SQLite. 중복 발송 방지가 핵심 역할
  digest.py               이메일 HTML 생성 + SMTP 발송
  export.py               대시보드용 data.json
dashboard/
  index.html              시행일 축 · 목록 · 신구대비 · 질의응답
  data.json               export가 생성 (없으면 샘플 데이터로 동작)
.github/workflows/daily.yml
```

## 명령어

```bash
python run.py probe                      법제처 API target 값 확인 (최초 1회)
python run.py collect                    수집 → 대비표 → 판정 → 저장
python run.py digest --dry-run           발송 없이 data/preview.html 생성
python run.py digest                     실제 발송
python run.py run                        전체 (Actions가 쓰는 명령)
python run.py export                     data.json 갱신
python run.py status <uid> 검토중 --assignee 김담당
python run.py demo                       샘플로 흐름 확인
```

## 더 붙일 만한 것

- **Slack 동시 발송** — `digest.py`에 Incoming Webhook 호출 추가. 조치 필요 건만 보내는 게 좋습니다.
- **질의응답 백엔드** — 지금 대시보드의 물어보기는 화면에 로드된 항목만 근거로 씁니다. 아카이브가 수백 건을 넘어가면 검색 단계를 서버로 옮기세요.
- **취업규칙 매핑** — 사내 규정 조항과 법령을 연결해두면 "이 개정으로 손봐야 할 조항"까지 자동으로 나옵니다.
