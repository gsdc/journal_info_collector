#!/usr/bin/env python3
"""
사용법:
  python main.py fetch [--community CMS ALICE ...] [--year 2026] [--limit N]
      -> 커뮤니티별 논문 목록 다운로드 + INSPIRE-HEP/CrossRef 조회 + Auth 판정
         + Ack 발췌까지 마친 뒤 data/candidates.json 에 저장.
         --year를 주면 해당 연도(pub_date_raw 기준)에 출판된 논문만 대상으로 함.
         (Ack Y/N 최종 판정, 커뮤니티 정보와 INSPIRE 정보 불일치 검토는
          이후 Claude Code의 /build-report 슬래시 커맨드에서 수행)

  python main.py scan-ack-gap [--community CMS ...] [--year 2026 | --since-days 730] [--limit N]
      -> "computing centres를 감사한다"는 문구는 있는데 KISTI/GSDC는 이름으로도
         funder DOI로도 전혀 안 나오는 논문을 찾아 data/ack_gap_flagged.json 에 저장.
         --year와 --since-days는 동시에 쓸 수 없음(둘 다 생략하면 --since-days 730 기본값).
"""
import argparse
import json

from src.ack_gap_scan import scan_computing_ack_gap
from src.pipeline import build_candidates, save_candidates


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch_p = sub.add_parser("fetch", help="커뮤니티 논문 목록 다운로드 및 조회")
    fetch_p.add_argument("--community", nargs="*", default=None, help="특정 커뮤니티만 처리")
    fetch_p.add_argument("--year", type=int, default=None, help="이 연도(pub_date_raw 기준)에 출판된 논문만")
    fetch_p.add_argument("--limit", type=int, default=None, help="커뮤니티별 처리 건수 제한(테스트용)")

    scan_p = sub.add_parser(
        "scan-ack-gap", help="'computing centres' 감사는 있는데 KISTI/GSDC 언급이 없는 논문 찾기"
    )
    scan_p.add_argument("--community", nargs="*", default=None, help="특정 커뮤니티만 처리")
    scan_date_group = scan_p.add_mutually_exclusive_group()
    scan_date_group.add_argument("--year", type=int, default=None, help="이 연도에 출판된 논문만")
    scan_date_group.add_argument(
        "--since-days", type=int, default=None, help="최근 며칠치만 볼지 (--year 없으면 기본 730일=2년, 0이면 전체)"
    )
    scan_p.add_argument("--limit", type=int, default=None, help="커뮤니티별 처리 건수 제한(테스트용)")
    scan_p.add_argument("--output", default="data/ack_gap_flagged.json")

    args = parser.parse_args()

    if args.cmd == "fetch":
        candidates, settings = build_candidates(
            community_names=args.community, year=args.year, limit_per_community=args.limit
        )
        save_candidates(candidates, settings)

    elif args.cmd == "scan-ack-gap":
        since_days = args.since_days
        if args.year is None and since_days is None:
            since_days = 730  # 둘 다 안 주면 기존 기본값(2년) 유지
        flagged, skipped_no_text, checked = scan_computing_ack_gap(
            community_names=args.community,
            year=args.year,
            since_days=since_days,
            limit_per_community=args.limit,
        )
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(
                {"checked": checked, "flagged": flagged, "skipped_no_text": skipped_no_text},
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"checked {checked}, flagged {len(flagged)} -> {args.output}")


if __name__ == "__main__":
    main()
