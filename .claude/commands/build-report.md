---
description: Python이 만든 최종 결과(CSV·엑셀)를 재확인(사사 원문 N 건)·교차검증하고 요약 보고
---

# 목적

최종 CSV와 엑셀은 Python이 만든다(`python main.py report --year <연도>`, 또는
`python main.py fetch --year <연도>` 끝에 자동 실행):

- `output/journal_report.csv` — 판정 대상 커뮤니티(CMS, ALICE, Belle II, LIGO)
- `output/journal_report_reference.csv` — 참고용 커뮤니티(`reference_only: true`)
- `output/journal_report_<연도>.xlsx` — 같은 행 규칙의 엑셀(요약·커뮤니티별·출판 미확인 탭)

이 커맨드는 **CSV를 직접 만들지 않는다.** 정규식·메타데이터 대조로는 놓칠 수 있는
부분만 사람처럼 다시 보고, 고칠 판정이 있으면 `config/review_overrides.yaml`에 남긴 뒤
`report`를 다시 실행한다. 그리고 결과를 요약해 보고한다.

1. **사사 원문 재확인** — Ack가 `N`인 출판 논문의 사사 발췌를 훑어 정규식이 놓친
   KISTI/GSDC 표기가 있는지 본다.
2. **정보 교차검증** — 커뮤니티 공식 출판목록 값과 INSPIRE-HEP/CrossRef 값이
   실제로 다른 논문을 가리키는지 본다.
3. **최종 요약** — 집계와 사람이 봐야 할 목록을 채팅으로 보고한다.

# 데이터

`data/candidates.json`의 각 논문에 Python이 채운 값:

- 커뮤니티 공식 출판목록 원문: `raw_title`, `raw_journal_citation`, `raw_pub_date`,
  `raw_status`, `source_doi`(목록에 실제로 있던 DOI)
- INSPIRE-HEP/CrossRef 조회 결과: `inspire_title`, `journal`, `issn`, `volume`,
  `page`, `date`, `doi`, `match_status`, `publication_status`
- Auth: `auth_status`, `auth_note`
- Ack(사사 원문): `ack_text_status`, `ack_text_evidence`, `ack_excerpt`, `ack_note`
- Funder(CrossRef funder ID): `ack_funder_status`, `ack_funder_note`
- SCIE: `scie`, `jif`, `jif_top_pct` (`config/journals_<연도>.tsv`에서)
- `source_label`(출처), `reference_only`, `pub_date_future`

CSV 컬럼 중 `유발논문`·`유발근거`·`Funder 수정요청`·`비고`의 자동 문구는
`src/csv_writer.py`가 계산한다. 유발논문은 SCIE 저널 논문이면서 Auth `Y`, Ack `Y`,
Funder `Y`/`KIST오태깅`/`이름만` 중 하나 이상일 때 `Y`다. ISSN이 없거나 SCIE가
아니면 `N`, JCR 표에 없는 저널(`미확인`)이면 보류다.

`출처` 형식: `커뮤니티:<출판|게재확정 표기|투고 표기|없음|공식목록 없음> /
INSPIRE:<O|O(제목 대조)|X> / OpenAlex:<O|O(KISTI: …)|O(출판 확인)|X|->`.
미출판 논문은 "INSPIRE:프리프린트 / OpenAlex:출판본 미확인", 공식 목록에 없던 논문은
끝에 "(INSPIRE 목록에서 보충)" 또는 "(OpenAlex에서만 발견)"이 붙는다.

Funder 값: `Y`(KISTI ID 정확), `KIST오태깅`(KIST ID로 잘못 등록, 근거 인정·수정 요청),
`이름만`(ID 누락, 근거 인정·ID 추가 요청), `KIST확인`(오태깅인지 판단 불가, 사람 확인),
`N`(KISTI 없음 또는 출판사가 funder 미등록).

# 절차

1. `data/candidates.json`과 `output/journal_report.csv`가 있는지 확인한다.
   candidates.json이 없으면 `python main.py fetch --year <연도>`를 먼저 실행하라고
   안내하고 중단한다. CSV가 없거나 candidates.json보다 오래됐으면
   `python main.py report --year <연도>`를 실행한다. 연도는 사용자가 주지 않으면
   candidates의 `date`에서 가장 많은 연도로 정하고 요약에 밝힌다.

2. **사사 원문 재확인** — `publication_status == "published"`이고
   `ack_text_status == "N"`인 논문의 `ack_excerpt`를 훑는다. 이미
   `config/review_overrides.yaml`에 있는 DOI는 건너뛴다.
   - 인정하는 표기: "KISTI", "Korea Institute of Science and Technology Information",
     "GSDC", "Global Science (experimental) Data hub Center/Centre"와 그 변형
     (하이픈·줄바꿈·약어 표기 등 정규식이 놓칠 만한 것).
   - KIST("Korea Institute of Science and Technology", 뒤에 Information 없음)는
     별개 기관이라 인정하지 않는다.
   - CrossRef funder 정보로 Ack를 바꾸지 않는다(Funder는 별도 근거).
   - 발췌에 실제로 KISTI/GSDC가 있으면 `config/review_overrides.yaml`의
     `ack_overrides`에 DOI를 키로 `ack: "Y"`와 `reason`(발췌에서 찾은 표기를 그대로
     인용)을 추가한다. 확신이 없으면 고치지 말고 요약에 "확인 필요"로 올린다.
   - `ack_text_status == "미확인"`(원문을 못 구함)은 고치지 않고 사유(`ack_note`)만
     요약에 적는다.

3. **정보 교차검증** — 출판 논문마다 커뮤니티 원문(`raw_title`,
   `raw_journal_citation`, `raw_pub_date`)과 조회 값(`inspire_title`,
   `journal`+`volume`+`page`, `date`)을 비교한다. LaTeX와 일반 텍스트 차이, 저널
   약어 차이 같은 표기 차이는 무시하고, 권·페이지·연도가 서로 다른 논문을 가리키는
   것처럼 보이면 경고로 남긴다. `match_status == "inspire_not_found"`인 논문은
   서지정보가 비어 있을 수 있으니 빈 값 목록을 함께 적는다. 교차검증 결과로 CSV를
   고치지 않는다(채팅 보고만).

4. 2번에서 `review_overrides.yaml`에 추가한 항목이 있으면
   `python main.py report --year <연도>`를 다시 실행해 CSV·엑셀에 반영한다
   (고친 행의 `비고`에 "Ack 재확인 N→Y: <사유>"가 붙는다).

5. 사용자에게 요약해서 보고한다. 집계 항목은 **판정 대상 커뮤니티만** 대상으로 하고,
   참고용 커뮤니티는 맨 끝에 커뮤니티별 출판 논문 수(와 SCIE 논문 수)만 따로 보여준다:
   - 커뮤니티별 출판 논문 수, 미출판이라 제외된 논문 수와 목록(커뮤니티/제목/상태)
   - 근거별 건수(저자 Y / 사사 Y / Funder 근거 인정), 유발 논문 수, 근거 없는 논문 수
   - 이번 재확인에서 Ack를 고친 논문과 사유, "확인 필요"로 남긴 논문
   - 근거가 엇갈리는 경우(예: Funder Y인데 사사 N, 사사 Y인데 Funder N) — 출판사
     funder 메타데이터 누락/과잉을 사람이 볼 수 있게
   - Ack "미확인" 건수와 사유
   - 게재 예정(`pub_date_future`) 논문 목록
   - ISSN 없음·SCIE 아님·JCR 표에 없는 저널로 유발논문에서 빠진 논문(근거가 있던 것은
     특히 표시). 표에 없는 저널은 `python main.py journals --year <연도>`로 뽑아
     `config/journals_<연도>.tsv`에 추가하라고 안내한다.
   - `Funder 수정요청`이 있는 논문(KIST 오태깅 / KIST 확인필요 / ID 누락별로)
   - Auth가 "확인필요"/"확인불가"인 논문
   - 3번의 정보 불일치 경고
   - (참고용) 커뮤니티별 출판 논문 수·SCIE 논문 수
