# jp2ko — 일본어 동인음성 → 한국어 자막

일본어 음성 파일(mp3/wav/flac 등)을 넣으면 **한국어 자막(.srt / .vtt / .lrc)** 을 오디오 옆에 만들어 줍니다.

```
RJ01234567/
├── 01_おかえりなさい.mp3
├── 01_おかえりなさい.ko.srt   ← 동영상 플레이어용 (팟플레이어, MPC, VLC)
├── 01_おかえりなさい.ko.lrc   ← 음악 플레이어용 (가사처럼 표시)
└── 01_おかえりなさい.ko.vtt   ← 웹/브라우저 플레이어용
```

## 폰에서 쓰기: 귀자막 웹앱 (`docs/index.html`)

설치 없이 폰 브라우저에서 파일을 열면 자막을 만들고, 자막을 띄운 채 재생합니다.

- **키는 그 기기 브라우저에만 저장**됩니다.
  - 받아쓰기: Vercel AI Gateway 키 (MAI-Transcribe-2 / Grok STT)
  - 번역: 기본은 **Toyrisu 릴레이** (`https://codex.zcxv.xyz/claude/v1`, 모델 `claude-opus-5-5`, OpenAI 호환 Chat Completions). 설정에 릴레이 키(`sk-tr-…`)만 넣으면 됩니다. Vercel 키로 번역하도록 바꿀 수도 있습니다.
- 만든 자막은 브라우저에 `파일이름@크기` 로 저장됩니다. 다음에 같은 파일을 열면 자동으로 붙습니다. 음성 파일 자체는 저장하지 않습니다.
- WAV 는 통째로 메모리에 올리지 않고 필요한 부분만 읽습니다. 1분 안팎으로 잘라 보내고, 조각마다 중간 저장을 하므로 탭이 꺼져도 **이어서 만들기**로 계속할 수 있습니다.
- BGM·효과음 있는 버전과 없는 버전처럼 타이밍이 같은 파일은 **다른 자막 연결**로 자막을 공유합니다. 받아쓰기는 효과음 없는 버전으로 하는 편이 정확합니다.
- SRT / LRC / VTT 로 내보내기 가능합니다.

### 처음 한 번: GitHub Pages 켜기
저장소 **Settings → Pages → Build and deployment** 에서 Source 를 *Deploy from a branch*, 브랜치를 `claude/keen-brahmagupta-d5bvz3`, 폴더를 `/docs` 로 저장합니다.
1~2분 뒤 `https://b1bibig.github.io/voices/` 에서 열립니다.

## PC 에서 쓰기: 명령줄 도구 (`jp2ko.py`)

동작 방식:
1. **일본어 받아쓰기** — 둘 중 선택
   - `--asr vercel` (기본): [Vercel AI Gateway](https://vercel.com/ai-gateway) 의 **MAI-Transcribe-2** / **Grok STT**. GPU 필요 없음.
     30일마다 $5 무료 크레딧 → 한 달 약 50시간 분량 (크레딧을 직접 구매하면 무료 지급이 끊기니 주의)
   - `--asr local`: 내 PC에서 faster-whisper (large-v3). 무료지만 NVIDIA GPU 권장
2. 앞뒤 문맥·캐릭터 말투를 살려 한국어 번역 — 둘 중 선택
   - `--translator relay` (기본): Toyrisu 릴레이 (`https://codex.zcxv.xyz/claude/v1`, `claude-opus-5-5`, OpenAI 호환). 키는 `TOYRISU_API_KEY`
   - `--translator claude`: Anthropic API 직접. 키는 `ANTHROPIC_API_KEY`

## 설치 (윈도우 기준)

1. [Python 3.10+](https://www.python.org/downloads/) 설치 (설치할 때 "Add python.exe to PATH" 체크)
2. [ffmpeg](https://ffmpeg.org) 설치: `winget install ffmpeg`
3. 이 폴더에서 명령 프롬프트 열고:
   ```
   pip install -r requirements.txt
   ```
4. **API 키** (명령 프롬프트를 새로 열어야 적용됨)
   ```
   setx AI_GATEWAY_API_KEY "..."      ← 받아쓰기 (Vercel 대시보드 → AI Gateway → API Keys)
   setx TOYRISU_API_KEY "sk-tr-..."    ← 번역 (Toyrisu 릴레이)
   setx ANTHROPIC_API_KEY "sk-ant-..." ← --translator claude 를 쓸 때만
   ```

`--asr local` 을 쓸 때만 `pip install faster-whisper` 가 추가로 필요합니다.
NVIDIA 그래픽카드가 있으면 자동으로 GPU를 씁니다(훨씬 빠름). GPU에서 cuBLAS/cuDNN 오류가 나면
[faster-whisper 안내](https://github.com/SYSTRAN/faster-whisper#gpu)대로 CUDA 라이브러리를 깔거나, 일단 `--device cpu` 로 돌리세요.

## 사용법

```bash
# 작품 폴더 통째로
python jp2ko.py "D:\voices\RJ01234567"

# 한국어 + 일본어 원문 같이 보기 (공부용으로도 좋음)
python jp2ko.py track01.mp3 --bilingual

# 작품 정보를 주면 번역이 훨씬 자연스러워짐 (호칭, 말투, 관계)
python jp2ko.py "RJ01234567" --context "츤데레 소꿉친구 '아카리'가 청자(동갑, 반말)를 귀청소해주는 작품"

# 캐릭터 이름 같은 고유명사를 Whisper가 자꾸 틀리면 힌트로
python jp2ko.py "RJ01234567" --prompt "あかり、耳かき、お兄ちゃん"

# 받아쓰기 모델을 Grok STT 로 (MAI 와 비교해보고 잘 듣는 쪽 쓰기)
python jp2ko.py track01.mp3 --asr-model grok --no-translate

# 받아쓰기를 내 PC(GPU)에서
python jp2ko.py track01.mp3 --asr local
```

| 옵션 | 설명 |
|---|---|
| `--bilingual` | 한국어 아래 일본어 원문 같이 표시 |
| `--context "..."` | 작품 설명/캐릭터 정보 → 번역 품질 ↑ |
| `--asr` | 받아쓰기 방식: `vercel`(기본) / `local` |
| `--asr-model` | `--asr vercel` 모델: `mai`(기본, MAI-Transcribe-2) / `grok` (Grok STT) |
| `--prompt "..."` | `--asr local` Whisper 받아쓰기 힌트 (일본어로) |
| `--formats srt,lrc` | 원하는 자막 형식만 |
| `--no-translate` | 번역 없이 일본어 받아쓰기만 (API 키 불필요) |
| `--retranslate` | 받아쓰기는 그대로 두고 번역만 다시 (`--context` 바꿨을 때) |
| `--force` | 캐시 무시하고 처음부터 |
| `--model` | `--asr local` Whisper 모델: `large-v3`(기본) / `medium` / `small` |
| `--translator` | 번역 경로: `relay`(기본, Toyrisu) / `claude`(Anthropic API) |
| `--relay-url` / `--relay-model` | 릴레이 주소·모델 (기본 `https://codex.zcxv.xyz/claude/v1` / `claude-opus-5-5`) |
| `--effort` | `--translator claude` 번역 공들이는 정도: `low` / `medium`(기본) / `high` |

받아쓰기 결과는 `*.jp2ko.json` 으로 캐시되므로, 번역만 다시 돌릴 때는 받아쓰기를 반복하지 않습니다.

## 자막 보는 법

- **PC**: 팟플레이어에 mp3 를 열면 같은 이름의 `.ko.srt` 를 자동으로 불러옵니다.
  (안 뜨면 자막 파일을 플레이어 창에 드래그)
- **폰**: `.lrc` 를 지원하는 음악 플레이어(예: 안드로이드 Poweramp, Musicolet 등)에 mp3 와 lrc 를 같은 폴더에 두기.
  또는 mp3+srt 를 MX Player / VLC 로 재생.

## 비용 / 시간 감각

- 받아쓰기 (vercel): 1시간 $0.10 — 무료 크레딧 $5/30일 안에서 해결. 긴 트랙은 무음 지점에서 2분 안팎으로 잘라 보냅니다.
- 받아쓰기 (local): GPU(RTX 3060급)에서 1시간 음성 ≈ 3~6분, CPU 만 있으면 그 몇 배.
- 번역: 대사량에 따라 다르지만 1시간 작품에 대략 1달러 안팎 (`--effort low` 로 낮추면 더 저렴).

## 한계

- 속삭임·바이노럴·효과음이 많으면 받아쓰기 모델이 대사를 놓치거나 잘못 듣기도 합니다. 번역 단계에서 문맥으로 어느 정도 보정하지만 완벽하진 않습니다.
- 무음 구간에서 Whisper 가 지어내는 대표적 문장(`ご視聴ありがとうございました` 등)은 자동으로 걸러냅니다.
- 번역이 거절되거나 오류가 난 묶음은 일본어 원문 그대로 남기고 다음으로 진행합니다.

## 실시간으로 그냥 듣고 싶다면

파일 처리 없이 바로 듣고 싶으면 **팟플레이어의 "실시간 자막 생성/번역"** 기능(Whisper 기반)을 써도 됩니다.
다만 실시간은 문맥을 못 보고 한 줄씩 기계번역이라 품질이 이 도구보다 확실히 떨어집니다.
자주 듣는 작품은 이 도구로 한 번 자막을 만들어 두는 걸 추천합니다.
