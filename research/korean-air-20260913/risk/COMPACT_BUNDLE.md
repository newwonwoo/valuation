# 작은 증거 번들

원본을 삭제하거나 수정하지 않았다. `compact_bundle_manifest.json`의 `git_stage_exclude`에 나열한 10개 원본/탐색자료만 원격 기록에서 제외하면 위험팩 폴더의 파일 합계가 약25.6MB에서 **1.62MB**로 줄어든다. 모든 Yahoo 일별가격·주간가격·금리·국가위험 원자료와 메타데이터는 유지한다. 제외 FedEx의 주가 시계열도 유지하며 최종 베타 집합에는 사용하지 않는다.

- `sec_facts_subset.json`: UPS/United/Delta 원본 companyfacts에서 필요한 최신 태그·레코드 14개만 선택했다. 각 SEC 레코드의 모든 키와 값, tag label/description을 그대로 보존했다. 원본 파일명·전체 SHA256·SEC URL·발행자 정보가 함께 있다.
- `capital_source_snippets.json`: UPS A/B 주식수, Delta sale-leaseback 금융부채, Lufthansa 부채/발행주식/자기주식의 원문 HTML·추출텍스트를 정확한 문자열 구간으로 보존했다. 공백·마크업을 다시 작성하지 않았다. 원문 전체 해시, 문자 offset, 추출구간 자체 UTF-8 해시와 URL을 포함한다. Lufthansa PDF→text 변환을 명시하고 전체 PDF 해시도 보존한다.
- `build_risk_draft.py`는 실제로 위 작은 원문에서 금융부채와 주식수를 읽고 계산한다. 예전 숫자를 고정 상수로 복제한 대체파일이 아니다. SEC 부채는 원 단위 정수 합계 후 million으로 환산하고, HTML/PDF 텍스트 숫자는 명시적 패턴으로 파싱한다. 출처 링크·원래 출력 형식은 유지했다.
- `compact_bundle_output_hashes.json`: 변경 전 `risk_pack.yaml`, `weekly_regressions.json`, `weekly_benchmark.json`, `peer_capital_audit.json`의 SHA256이다. 변경 후 네 파일 모두 **바이트 단위로 동일**함을 검증했다.

추가로 큰 원본이 없는 별도 임시 폴더에서 스크립트·두 subset·여섯 가격 JSON 및 기존 `scripts/compute_peer_betas.py`만 복사해 실행했다. 같은 네 출력의 바이트 해시가 전부 일치했다. 임시 검증 폴더는 자동 정리했다. 이는 생성물 재현 검증이며 제외한 전체 공시 원문을 subset에서 복원할 수 있다는 뜻은 아니다.

원격 기록 제외 목록: `DAL_facts.json`, `UAL_facts.json`, `UPS_facts.json`, `FDX_facts.json`, `UPS_10q.html`, `lufthansa_q2.pdf`, `fdx_q4.pdf`, `fdx_q4.txt`, `lufthansa_reports.html`, `delta_q2_release.html`. 각 파일의 용도와 해시·원문 링크는 manifest에서 확인할 수 있다. 이 문서는 제외 권고만 기록하며 실제 git staging은 수행하지 않았다.
