# Step 1-1: Data Collection Workflow

## 개요
ST 포럼의 질문/답변 데이터(에러 사례)와 ST 공식 하드웨어 가이드라인 PDF를 텍스트 형태로 수집하는 첫 번째 워크플로우입니다.

## 진행 절차
1. **수동 다운로드 자료 배치 (`dataset/official_docs/`)**
   - 아래 URL을 통해 핵심 참조 문서(PDF)들을 수동으로 다운로드하여 지정된 폴더에 적재합니다.
   
   **[기초 하드웨어 데이터시트 및 가이드]**
   - RM0440 (Data Reference Manual): [URL 접속](https://www.st.com/resource/en/reference_manual/rm0440-stm32g4-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf)
   - STM32G474 Datasheet: [URL 접속](https://www.st.com/resource/en/datasheet/stm32g474re.pdf)
   - AN5031 (HW 하드웨어 기초 설계): [URL 접속](https://www.st.com/resource/en/application_note/dm00445657-getting-started-with-stm32g4-series-hardware-development-stmicroelectronics.pdf)
   
   **[아날로그 핀 & 통신 연결 특화 가이드]**
   - AN5306 (OPAMP 내부 증폭기 PGA 세팅): [URL 접속](https://www.st.com/resource/en/application_note/dm00609340-operational-amplifier-opamp-usage-in-stm32g4-series-stmicroelectronics.pdf)
   - AN5346 (ADC 최적화 및 충돌 방지): [URL 접속](https://www.st.com/resource/en/application_note/dm00612662-stm32g4-adc-use-tips-and-recommendations-stmicroelectronics.pdf)
   - AN5348 (FDCAN 핀/클럭 주의사항): [URL 접속](https://www.st.com/resource/en/application_note/dm00627714-introduction-to-fdcan-peripherals-for-stm32-mcus-stmicroelectronics.pdf)
   - AN3070 (RS-485 DE핀 하드웨어 제어): [URL 접속](https://www.st.com/resource/en/application_note/cd00259695-managing-the-driver-enable-signal-for-rs485-and-iolink-communications-with-the-stm32-usart-stmicroelectronics.pdf)
   
   **[모터 구동/FOC 제어 레퍼런스 및 튜닝 공식 매뉴얼]**
   - UM2392 (STM32 Motor Control FOC SDK 파라미터 구조): [URL 접속](https://www.st.com/resource/en/user_manual/um2392-stm32-motor-control-sdk-v5x-tools-stmicroelectronics.pdf)
   - AN5166 (MTPA 및 모터 토크 파라미터 맞춤화): [URL 접속](https://www.st.com/resource/en/application_note/dm00481232-guidelines-for-control-and-customization-of-power-boards-with-stm32-mc-sdk-stmicroelectronics.pdf)
   - EVSPIN32G4-DUAL 회로도 (다축 레퍼런스): [URL 접속](https://www.st.com/resource/en/schematic_pack/evspin32g4-dual-schematics.pdf)

2. **오픈소스 하드웨어/펌웨어 데이터 수집 (`dataset/opensource/`)**
   - 글로벌 오픈소스 모터 제어 프로젝트의 소스코드와 회로도(KiCad/Altium)에서 완벽한 정답 데이터(핀 라우팅, 클럭 트리 설정 등)를 얻습니다.
   - **SimpleFOC**: `https://github.com/simplefoc/Arduino-FOC` (STM32G4 하드웨어 포팅 리포지토리 스크래핑)
   - **flatmcu**: `https://github.com/GyrocopterLLC/flatmcu` (STM32G473CB 기반 완전 공개 오픈소스 FOC 컨트롤러 KiCad 회로도)
   - **stm32-esc**: `https://github.com/mohammad-askari/stm32-esc` (B-G431B-ESC1 보드 최적화 코드)
   - 이 데이터들은 `git clone` 명령어를 통해 `dataset/opensource/` 하위에 클론하여 텍스트(.c, .h, .net)를 파싱합니다.

3. **포럼 데이터 스크래핑 (자동화/수동 대체)**
   - `scripts/scrape_st_forum.py` (또는 스킬) 을 확장하여 ST 커뮤니티의 Motor Control Hardware 관련 질의응답을 스크래핑합니다. (방화벽 차단 시 주요 에러 사례 수동 텍스트 저장으로 대체 가능)

4. **텍스트 정제**
   - 수집된 PDF, C헤더 스니펫, 오픈소스 회로도 네트리스트 등을 순수 Text 포맷(Markdown)으로 정제하여 `dataset/cleaned_text/` 에 저장합니다.

## 사용 스킬
- `skills/skill_st_forum_scraper.py` (웹 크롤링 전문 스킬)
