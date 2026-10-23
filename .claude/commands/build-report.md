---
description: candidates.json을 읽어 KISTI 유발 근거(Auth·Ack·Funder) 판정과 정보 검증 후 journal_report.csv 작성
---

# 목적

`python main.py fetch` 로 만들어진 `data/candidates.json`에는 각 논문에 대해
Python(결정론적 로직)이 이미 채워 놓은 정보가 들어있다:
서비스 다운로드 원문(`raw_*`), INSPIRE-HEP/CrossRef 조회 결과(`journal`, `issn`,
`volume`, `page`, `date`, `inspire_title`), Auth 1차 판정(`auth_status`,
`auth_note`), Funder 판정(`ack_funder_status`, `ack_funder_note`) — CrossRef의
FundRef(funder) 메타데이터에서 KISTI의 funder DOI(`10.13039/501100003708`)로
확인한 결과 — 사사 원문 판정(`ack_text_status`, `ack_text_evidence`), 그리고
`ack_excerpt`/`ack_note`(arXiv 원문에서 뽑아낸 Acknowledgements 발췌).

# KISTI 유발 논문 근거 (3개 항목을 각각 따로 표기)

출판 논문은 **조건에 맞든 안 맞든 전부** CSV에 넣고, 아래 세 근거를 각 컬럼에
따로 표기한다. 셋 중 하나라도 "Y"면 KISTI 유발 논문이고, 하나도 없으면 모든
근거 컬럼이 비어 있거나 "N"인 채로 남는다(그래도 행은 포함한다).
`유발근거`·`Funder 수정요청` 컬럼은 csv_writer가 세 컬럼 값으로 자동 계산하므로
직접 채우지 않는다.

**SCIE 저널 논문만 유발논문으로 센다.** SCIE 여부·IF·JIF 기준 첫 번째 카테고리 상위%는
사람이 JCR에서 정리한 `config/journals_<연도>.tsv`를 Python이 ISSN으로 매칭해
`candidate.scie`/`jif`/`jif_top_pct`에 채워 둔다. `scie`가 "N"이면 유발논문 N,
"미확인"(표에 없는 저널)이면 유발논문 보류이며 csv_writer가 `유발근거`를 비우고 `비고`에
사유를 붙인다. 표에 없는 저널은 `python main.py journals --year <연도>`로 목록을 뽑아
표에 추가하도록 최종 요약에서 알려준다.

**ISSN이 없는 논문은 SCIE 저널 논문이 아니므로 유발논문이 아니다(N).** csv_writer가
`유발근거`를 비우고 `비고`에 "ISSN 없음 — SCIE 아님(유발논문 N)"을 자동으로 붙인다.
ISSN은 전자판으로 출판됐어도 인쇄판을 우선 기입한다(인쇄판이 없는 전자판 전용 저널만
전자판). 이 값은 Python이 `candidate.issn`에 이미 정해 두었다(`eissn`은 전자판).

`출처` 컬럼(`candidate.source_label`)은 논문을 어디서 확인했는지 판단 근거로 남긴다:
`커뮤니티:<출판|게재확정 표기|투고 표기|없음|공식목록 없음> / INSPIRE:<O|O(제목 대조)|X> /
OpenAlex:<O|O(출판 확인)|X|->`. "O(출판 확인)"은 INSPIRE에서 출판이 확인되지 않아
OpenAlex 제목 검색으로 출판본을 찾은 경우다. 커뮤니티 페이지에 없던 논문은 끝에 "(INSPIRE 목록에서 보충)" 또는
"(OpenAlex에서만 발견)"이 붙는다. OpenAlex는 KISTI funder·소속·담당자 ORCID로
검색한 결과라 "X"는 OpenAlex에 없다는 뜻이 아니라 KISTI 관련으로 잡히지 않았다는 뜻이다.

1. **Auth (저자)** — `auth_status`를 그대로 쓴다. Python이 지정 담당자 명단
   (`config/responsible_authors.yaml`)과 ORCID/이름 대조를 이미 마쳤다.
   "확인필요"(이름만 일치)/"확인불가"는 다른 실마리가 없으면 그대로 둔다.

2. **Ack (사사 원문)** — 사사(감사의 글) 원문에 KISTI 또는 GSDC가 언급됐는지.
   Python이 arXiv 원문의 사사 영역 전체에서 정규식으로 이미 판정했다
   (`ack_text_status`, 근거 문맥은 `ack_text_evidence`). 인정하는 표기:
   "KISTI", "Korea Institute of Science and Technology Information",
   "GSDC", "Global Science (experimental) Data hub Center/Centre".
   KIST("Korea Institute of Science and Technology", 뒤에 Information 없음)는
   별개 기관이라 인정하지 않는다.
   - "Y": 그대로 Y.
   - "N": `ack_excerpt`를 훑어 정규식이 놓쳤을 만한 변형 표기(하이픈·약어
     변형 등)가 있는지만 확인하고, 없으면 N.
   - "미확인": 원문을 구하지 못한 경우(`ack_note`에 사유). 그대로 "미확인"으로
     두고 최종 요약에 사유를 적는다.
   - **CrossRef funder 정보로 Ack를 Y로 바꾸지 않는다** — funder는 별도 컬럼이다.

3. **Funder (funder ID)** — CrossRef FundRef 메타데이터에 KISTI funder ID
   (`10.13039/501100003708`)가 있는지. `ack_funder_status`를 다음과 같이 옮긴다:
   - "Y": KISTI ID가 정확히 등록됨 → "Y"
   - "KIST오태깅": KISTI를 KIST ID(`10.13039/501100003693`)로 잘못 등록함
     (funder 이름은 KISTI이거나, 이름도 KIST인데 원문 사사엔 KISTI만 있음)
     → "KIST오태깅" (유발근거로 세되 수정 요청 대상)
   - "KIST확인": KIST ID가 있는데 오태깅인지 판단 근거가 없음 → "KIST확인"
   - "이름만": KISTI 이름만 있고 funder ID가 비어 있음 → "이름만"
   - "N" 또는 null(출판사가 funder 정보를 등록 안 함) → "N"
   KIST/ID 누락 건은 출판사·컬래버레이션에 수정 요청할 수 있도록 csv_writer가
   `Funder 수정요청` 컬럼에 자동으로 사유를 적는다. 근거는 `ack_funder_note` 참고.

정보 교차검증도 이 커맨드가 맡는다:

- **정보 교차검증** — 커뮤니티 원문(`raw_title`, `raw_journal_citation`,
   `raw_pub_date`)과 INSPIRE-HEP/CrossRef 기반 값(`inspire_title`, `journal`+
   `volume`+`page`, `date`)을 비교해서, 단순 표기 차이(LaTeX vs plain text,
   저널 약어 차이 등)가 아니라 실제로 값이 다른 것처럼 보이면(예: 권/페이지/연도가
   서로 다른 논문을 가리키는 것 같은 경우) 최종 요약에 경고로 남긴다. CSV 자체에는
   경고 컬럼이 없으므로 채팅 응답으로만 알려준다.

**출판 여부 판단** — `data/candidates.json`에는 아직 투고/심사중/게재확정
단계인 논문도 포함돼 있다(연도 리스트에는 일단 넣어두고 사후 확인하는
정책). `publication_status` 필드로 최종 CSV 포함 여부를 정한다:

- `"published"`: DOI가 확보됨(원본 페이지에 있었거나, 원본엔 없어도 arXiv ID로
  INSPIRE를 다시 조회해서 실제로는 이미 출판된 걸 확인한 경우 — `source_doi`가
  비어 있는데 `doi`는 채워져 있으면 후자다). **CSV에 포함한다.**
- 그 외(`"submitted"`, `"accepted"`, `"preprint"`, `"unknown"`): 아직 출판
  확인이 안 된 것 — **CSV에서 제외**하고, 최종 요약에 "이번 리포트 제외(미출판)"
  목록으로 커뮤니티/제목/상태를 알려준다. 다음 fetch 때 다시 확인하면 된다.

# 참고용 커뮤니티 분리

`candidate.reference_only`가 true인 논문(현재 DUNE, JSNS2, HK/SK, RENE, FCC, EIC,
ENIGMA — `config/communities.yaml`의 `reference_only: true`)은 논문이 얼마나 나오는지
보는 참고 자료다. 판정 대상(현재 CMS, ALICE, Belle II, LIGO)과 같은 방식으로 행을
만들되, **최종 보고서와 유발논문 집계에서 빼고** 별도 CSV로 낸다.

# 절차

1. `data/candidates.json`을 읽는다. 없으면 사용자에게 먼저
   `python main.py fetch` 를 실행하라고 안내하고 중단한다.
2. `publication_status != "published"`인 candidate는 3~4단계(Ack 판정, CSV 행
   생성)를 건너뛰고 "미출판 제외" 목록에만 담는다.
3. 나머지(published) candidate **전부**에 대해 위 기준으로 Auth/Ack/Funder를
   정한다. 세 근거가 모두 없는 논문도 빼지 않는다.
4. `match_status`가 "inspire_not_found"인 항목은 INSPIRE/CrossRef 정보가
   비어 있을 수 있음 — journal/issn/volume/page/date가 빈 값이면 note로 남기고
   그대로 진행한다(억지로 채우지 않는다).
5. 아래 스키마의 dict 리스트를 만든다. 판정 대상 논문(`reference_only`가 false)은
   `data/final_rows.json`에, 참고용 커뮤니티 논문(`reference_only`가 true)은
   `data/final_rows_reference.json`에 저장한다
   (No.는 나중에 csv_writer가 다시 매기므로 생략 가능):

   ```json
   {
     "title": "<inspire_title 있으면 그것, 없으면 raw_title>",
     "journal": "<candidate.journal>",
     "ISSN": "<candidate.issn>",
     "Vol": "<candidate.volume>",
     "Page": "<candidate.page>",
     "coll.": "<candidate.community>",
     "date": "<candidate.date>",
     "IF": "<candidate.jif>",
     "JIF 상위%": "<candidate.jif_top_pct>",
     "SCIE": "<candidate.scie: Y|N|미확인>",
     "DOI": "<candidate.doi>",
     "Auth": "<candidate.auth_status>",
     "Ack": "<Y|N|미확인>  (사사 원문 기준)",
     "Funder": "<Y|KIST오태깅|KIST확인|이름만|N>",
     "출처": "<candidate.source_label>",
     "비고": "<candidate.pub_date_future가 true면 '게재 예정(<date>)', 아니면 빈 값>"
   }
   ```

   정렬은 date 내림차순(최신 논문 먼저)으로 하되, 사용자가 다른 정렬을
   요청하면 그에 따른다.

6. 다음 명령으로 최종 CSV를 만든다(경로/인코딩은 config/settings.yaml의
   output 설정을 따른다):

   ```
   python -m src.csv_writer data/final_rows.json output/journal_report.csv
   python -m src.csv_writer data/final_rows_reference.json output/journal_report_reference.csv
   ```

7. 사용자에게 다음을 요약해서 보고한다. 아래 항목(유발논문 집계, 근거 엇갈림,
   Funder 수정요청 등)은 **판정 대상 커뮤니티만** 대상으로 하고, 참고용 커뮤니티는
   맨 끝에 커뮤니티별 출판 논문 수(와 SCIE 논문 수)만 따로 보여준다:
   - 총 몇 건 처리했는지, 커뮤니티별 건수
   - 미출판이라 이번 CSV에서 제외한 논문 수와 목록(커뮤니티/제목/상태)
   - 근거별 건수: 저자 Y / 사사 Y / Funder Y, 그리고 하나라도 Y인 유발 논문 수와
     근거가 하나도 없는 논문 수
   - 근거가 엇갈리는 경우(예: Funder Y인데 사사 N, 사사 Y인데 Funder N) 목록 —
     출판사 funder 메타데이터 누락/과잉 여부를 사람이 볼 수 있게
   - Ack "미확인" 건수와 사유
   - 게재 예정(`pub_date_future`, 게재일이 오늘 이후) 논문 목록 — 원래 조사 대상이
     아니지만 CSV에는 넣고 비고에 표시한다
   - ISSN이 없어 유발논문에서 제외된 논문 목록(근거 컬럼에 Y가 있던 것은 특히 표시)
   - `Funder 수정요청`이 있는 논문 목록(KIST 오태깅 / KIST 확인필요 / ID 누락별로)
   - Auth가 "확인필요"/"확인불가"인 건수와 목록 (사람이 다시 봐야 함)
   - 4번 단계에서 발견한 정보 불일치 경고
   - (참고용) 커뮤니티별 출판 논문 수·SCIE 논문 수 — `journal_report_reference.csv`
