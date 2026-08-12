---
description: candidates.json을 읽어 Ack(사사)/Auth 최종 판정과 정보 검증 후 journal_report.csv 작성
---

# 목적

`python main.py fetch` 로 만들어진 `data/candidates.json`에는 각 논문에 대해
Python(결정론적 로직)이 이미 채워 놓은 정보가 들어있다:
서비스 다운로드 원문(`raw_*`), INSPIRE-HEP/CrossRef 조회 결과(`journal`, `issn`,
`volume`, `page`, `date`, `inspire_title`), Auth 1차 판정(`auth_status`,
`auth_note`), Ack 1차 판정(`ack_funder_status`, `ack_funder_note`) — CrossRef의
FundRef(funder) 메타데이터에서 KISTI의 funder DOI(`10.13039/501100003708`)로
확인한 결과 — 그리고 `ack_excerpt`/`ack_note`(arXiv 원문에서 뽑아낸
Acknowledgements 발췌).

Ack는 두 단계로 판정하되, **"Y"만 그대로 신뢰**하고 그 외에는 항상 원문도 본다
(CrossRef의 funder 목록은 출판사가 자동 텍스트마이닝으로 채우는 경우가 많아
있어도 불완전한 사례를 실제로 확인했다 — 예: 어떤 Elsevier 논문은 원문에는
"NRF"가 있는데 funder 목록엔 빠져 있었음. 반대로 "Y"는 CrossRef가 KISTI의
funder DOI를 정확히 매칭한 것이라 신뢰할 수 있다):

1. **`ack_funder_status`가 "Y"면 그대로 Ack = "Y"로 확정한다.** (예: CMS 논문이
   APS 저널(Phys.Rev. 시리즈)에 실리면 사사 원문에는 "MSIT and NRF (Republic of
   Korea)"만 보여도 CrossRef funder 목록에는 KISTI DOI가 들어있는 경우가 실제로
   확인됨.) 이 경우 `ack_excerpt`는 비어 있는 게 정상이니 무시한다.

2. **`ack_funder_status`가 "N"이거나 None이면** (funder 목록에 KISTI가 없거나,
   출판사가 FundRef 메타데이터 자체를 등록 안 한 경우 모두 포함) `ack_excerpt`를
   읽고 KISTI 또는 KISTI GSDC가 사사(감사의 글)에 언급되어 있는지 Y/N으로
   직접 판단한다. `ack_funder_note`에 "N"의 근거(어떤 funder들이 대신 있었는지)가
   있으니 참고는 하되 최종 판단의 근거로 단독으로 쓰지 않는다. 표기가
   컬래버레이션/논문마다 다르다는 점에 주의:
   - 약칭: "KISTI"
   - 전체 기관명: "Korea Institute of Science and Technology Information"
     (실제 ALICE 사사 문구 예시: "Korea Institute of Science and Technology
     Information and National Research Foundation of Korea (NRF), Republic of
     Korea;")
   - GSDC: "GSDC", "Global Science experimental Data hub Center",
     "Global Science Data hub Center" 등 표기가 조금씩 다를 수 있음
   - 이 중 하나라도 언급되어 있으면 Ack = "Y", 없으면 "N".
   - `ack_excerpt`가 비어 있고 `ack_note`에 사유가 적혀 있으면(예: "tex 소스
     없음") Ack = "미확인" 으로 두고, 왜 확인할 수 없었는지 최종 요약에 적는다.

정보 교차검증도 이 커맨드가 맡는다:

- **정보 교차검증** — 커뮤니티 원문(`raw_title`, `raw_journal_citation`,
   `raw_pub_date`)과 INSPIRE-HEP/CrossRef 기반 값(`inspire_title`, `journal`+
   `volume`+`page`, `date`)을 비교해서, 단순 표기 차이(LaTeX vs plain text,
   저널 약어 차이 등)가 아니라 실제로 값이 다른 것처럼 보이면(예: 권/페이지/연도가
   서로 다른 논문을 가리키는 것 같은 경우) 최종 요약에 경고로 남긴다. CSV 자체에는
   경고 컬럼이 없으므로 채팅 응답으로만 알려준다.

Auth는 이미 Python이 ORCID/이름 대조까지 마쳤으므로 그대로 사용한다
(`auth_status`가 "확인필요"/"확인불가"인 항목은 다른 실마리가 없으면 그대로 둔다).

**출판 여부 판단** — `data/candidates.json`에는 아직 투고/심사중/게재확정
단계인 논문도 포함돼 있다(연도 리스트에는 일단 넣어두고 사후 확인하는
정책). `publication_status` 필드로 최종 CSV 포함 여부를 정한다:

- `"published"`: DOI가 확보됨(원본 페이지에 있었거나, 원본엔 없어도 arXiv ID로
  INSPIRE를 다시 조회해서 실제로는 이미 출판된 걸 확인한 경우 — `source_doi`가
  비어 있는데 `doi`는 채워져 있으면 후자다). **CSV에 포함한다.**
- 그 외(`"submitted"`, `"accepted"`, `"preprint"`, `"unknown"`): 아직 출판
  확인이 안 된 것 — **CSV에서 제외**하고, 최종 요약에 "이번 리포트 제외(미출판)"
  목록으로 커뮤니티/제목/상태를 알려준다. 다음 fetch 때 다시 확인하면 된다.

# 절차

1. `data/candidates.json`을 읽는다. 없으면 사용자에게 먼저
   `python main.py fetch` 를 실행하라고 안내하고 중단한다.
2. `publication_status != "published"`인 candidate는 3~4단계(Ack 판정, CSV 행
   생성)를 건너뛰고 "미출판 제외" 목록에만 담는다.
3. 나머지(published) candidate에 대해 `ack_funder_status`가 "Y"면 그대로, 그
   외(N/None)에는 `ack_excerpt`를 읽고 위 기준으로 Ack를 판정한다.
4. `match_status`가 "inspire_not_found"인 항목은 INSPIRE/CrossRef 정보가
   비어 있을 수 있음 — journal/issn/volume/page/date가 빈 값이면 note로 남기고
   그대로 진행한다(억지로 채우지 않는다).
5. 아래 스키마의 dict 리스트를 만들어 `data/final_rows.json`에 저장한다
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
     "IF": "",
     "JCI percentile": "",
     "DOI": "<candidate.doi>",
     "Auth": "<candidate.auth_status>",
     "Ack": "<Y|N|미확인>"
   }
   ```

   정렬은 date 내림차순(최신 논문 먼저)으로 하되, 사용자가 다른 정렬을
   요청하면 그에 따른다.

6. 다음 명령으로 최종 CSV를 만든다(경로/인코딩은 config/settings.yaml의
   output 설정을 따른다):

   ```
   python -m src.csv_writer data/final_rows.json output/journal_report.csv
   ```

7. 사용자에게 다음을 요약해서 보고한다:
   - 총 몇 건 처리했는지, 커뮤니티별 건수
   - 미출판이라 이번 CSV에서 제외한 논문 수와 목록(커뮤니티/제목/상태)
   - Ack "Y"로 판정된 건수와 논문 제목(간단히)
   - Auth가 "확인필요"/"확인불가"인 건수와 목록 (사람이 다시 봐야 함)
   - 4번 단계에서 발견한 정보 불일치 경고
