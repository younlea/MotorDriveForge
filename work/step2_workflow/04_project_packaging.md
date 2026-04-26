# Step 2-4: Project Zip & Deploy Workflow

## 개요
완성된 프로젝트 리소스를 압축하여 프론트엔드로 다운로드할 수 있도록 API를 내립니다.

## 진행 절차
1. 폴더 내부 쓰레기 파일 정리 (로그 파일 등).
2. ZIP 아카이브 압축 (`shutil.make_archive` 또는 bash 스킬 활용).
3. 웹서버 스토리지 이동 및 Download Link 발급.

## 사용 스킬
- Python 기본 라이브러리 조합 예정.
