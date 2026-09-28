# AutoCut — 영상 넣으면 자동으로 컷 편집 · 자막 · 효과

캡컷(CapCut) 같은 편집기입니다. 영상을 올리면 아래 작업을 자동으로 하고, 결과를 웹 편집기에서 바로 고친 뒤 내보낼 수 있어요.

| 자동 처리 | 내용 |
|---|---|
| ✂ **점프컷** | 말 없는 무음 구간, "음/어" 같은 군말을 잘라냅니다 |
| 💬 **자동 자막** | 음성 인식(Whisper)으로 자막을 만들고, 지금 말하는 단어를 강조색으로 표시하는 숏폼 스타일로 입힙니다 |
| 🔍 **줌 효과** | 컷이 바뀔 때마다 번갈아 화면을 확대하는 펀치인 줌 (유튜브 쇼츠/브이로그 느낌) |
| ⚡ **플래시 전환** | 컷 전환 순간 흰색 플래시 (다이나믹 프리셋) |
| 🎨 **필터** | 선명하게 / 따뜻하게 / 시원하게 / 시네마틱 / 흑백 |
| 📱 **세로 변환** | 9:16 블러 배경 / 9:16 꽉 채우기 / 1:1 / 원본 |
| 🏷 **상단 제목** | 쇼츠처럼 위쪽에 계속 떠 있는 제목 박스 |
| 🎵 **배경음악** | 말할 때 음악 볼륨이 자동으로 작아지는 덕킹, 목소리 음량 자동 맞춤(-14 LUFS) |

## 설치

1. **Python 3.10+** 와 **ffmpeg** 가 필요합니다.
   - Windows: `winget install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Ubuntu: `sudo apt install ffmpeg fonts-noto-cjk`
2. 패키지 설치
   ```bash
   pip install -r requirements.txt
   ```
3. (권장) 자막용 한글 폰트: [Pretendard](https://github.com/orioncactus/pretendard) 나 Noto Sans KR 을 설치하면 자동으로 사용합니다.
   설치가 안 돼 있으면 맑은 고딕 / Apple SD Gothic Neo 등 시스템 폰트를 씁니다.

> 처음 실행할 때 Whisper 음성 인식 모델(약 500MB)을 자동으로 내려받습니다.

## 사용법

### 웹 편집기 (추천)
```bash
python -m autocut serve
```
브라우저에서 http://127.0.0.1:8000 을 열고:

1. 영상을 끌어다 놓고, 편집 스타일(숏폼 / 다이나믹 / 브이로그 / 인터뷰)을 고른 뒤 **자동 편집 시작**
2. 분석이 끝나면 편집 화면이 열립니다. 이미 자동으로 컷·자막·효과가 들어가 있어요.
   - **타임라인**: 파란 블록은 남긴 구간, 보라색은 줌인 구간, 빗금은 잘린 구간입니다.
     클릭해서 선택 → `Del` 로 삭제/복원, `S` 로 재생 위치에서 자르기, 🔍 로 줌인 켜기/끄기
   - **자막 탭**: 오타 수정, 시간 조정, 추가/삭제, 스타일(팝/박스/미니멀)·크기·색상
   - **효과 탭**: 화면 비율, 필터, 줌 배율, 플래시, 상단 제목
   - **오디오 탭**: 배경음악 넣기, 볼륨
   - **자동 컷 탭**: 무음 기준·길이를 바꿔서 다시 자동 편집
   - 모든 수정은 **편집 미리보기**에 바로 보입니다 (잘린 구간은 건너뛰며 재생).
3. **영상 내보내기** → 완성본 MP4 다운로드 (자막은 SRT 로도 받을 수 있어요)

### 명령줄 한 방 편집
```bash
python -m autocut edit 내영상.mp4 --preset shorts --title "오늘의 브이로그"
# → 내영상_autocut.mp4
```
옵션: `--preset shorts|dynamic|vlog|interview`, `--aspect original|9:16-crop|9:16-blur|1:1`,
`--bgm 음악.mp3`, `--language ko|en|ja|auto`, `--no-captions`

## 설정 (환경 변수)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AUTOCUT_WHISPER_MODEL` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` — 클수록 자막이 정확하지만 느림 |
| `AUTOCUT_WHISPER_DEVICE` | `auto` | NVIDIA GPU 가 있으면 `cuda` |
| `AUTOCUT_WHISPER_COMPUTE` | `int8` | GPU 사용 시 `float16` 추천 |
| `AUTOCUT_DATA` | `./data` | 업로드·결과물 저장 위치 |

## 구조

```
autocut/
  media.py       ffprobe/ffmpeg 호출, 무음 구간 탐지, 미리보기용 영상
  transcribe.py  faster-whisper 음성 인식 (단어 단위 타임스탬프)
  planner.py     무음/군말 → 컷 구간, 단어 → 자막 묶음, 원본↔편집본 시간 변환
  subtitles.py   ASS 자막 (단어 강조, 팝 애니메이션, 제목 박스)
  render.py      ffmpeg 필터 그래프 (컷, 비율, 줌, 필터, 자막, 플래시, 오디오 믹스)
  project.py     프로젝트 저장, 분석→렌더 작업 큐
  server.py      FastAPI API + 웹 편집기 제공
  web/           편집기 UI (HTML/CSS/JS, 빌드 불필요)
tests/           pytest (planner 단위 테스트 + 실제 ffmpeg 렌더 테스트)
```

테스트: `pip install pytest && python -m pytest`
