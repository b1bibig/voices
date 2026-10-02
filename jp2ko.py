#!/usr/bin/env python3
"""일본어 음성(동인음성/ASMR) → 한국어 자막 생성기.

1) 일본어 받아쓰기 (타임스탬프 포함)
   - --asr vercel : Vercel AI Gateway API (MAI-Transcribe-2 / Grok STT), GPU 불필요
   - --asr local  : 내 PC 에서 faster-whisper
2) Claude 로 문맥을 유지하며 한국어 번역
3) 오디오 파일 옆에 .ko.srt / .ko.vtt / .ko.lrc 저장

사용 예:
    python jp2ko.py "RJ01234567/"                 # 폴더 안 트랙 전부
    python jp2ko.py track01.mp3 --bilingual        # 한국어 + 일본어 원문 같이
    python jp2ko.py track01.mp3 --no-translate     # 일본어 받아쓰기만
    python jp2ko.py track01.mp3 --asr local        # 받아쓰기를 내 PC(GPU)에서
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".mp4", ".mkv", ".webm"}

# Whisper 가 무음/숨소리 구간에서 자주 지어내는 문장들
HALLUCINATIONS = [
    "ご視聴ありがとうございました",
    "ご清聴ありがとうございました",
    "チャンネル登録",
    "最後までご視聴",
    "おやすみなさい。ご視聴",
]

CLAUDE_MODEL = "claude-opus-5-5"
CHUNK_SIZE = 60      # 한 번에 번역할 줄 수
CONTEXT_LINES = 8    # 다음 묶음에 넘겨줄 직전 번역 줄 수


@dataclass
class Segment:
    id: int
    start: float
    end: float
    ja: str
    ko: str = ""


# ---------------------------------------------------------------- 받아쓰기

def is_hallucination(text: str) -> bool:
    if any(h in text for h in HALLUCINATIONS):
        return True
    # 같은 글자/구절이 비정상적으로 반복되는 경우 ("あああああ…" 는 살리고 긴 루프만 제거)
    if len(text) > 30 and len(set(text)) <= 3:
        return True
    return False


def transcribe(path: Path, model, args) -> list[Segment]:
    segments, info = model.transcribe(
        str(path),
        language="ja",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,  # 반복 루프 방지
        initial_prompt=args.prompt,
        no_speech_threshold=0.6,
        word_timestamps=True,
    )
    out: list[Segment] = []
    for seg in segments:
        for start, end, text in split_segment(seg):
            if not text or is_hallucination(text):
                continue
            out.append(Segment(id=len(out), start=start, end=end, ja=text))
            print(f"  [{fmt_lrc(start)}] {text}", flush=True)
    return out


def transcribe_vercel(path: Path, args) -> list[Segment]:
    import asr_vercel

    out: list[Segment] = []

    def on_line(start: float, end: float, text: str) -> None:
        if not text or is_hallucination(text):
            return
        out.append(Segment(id=len(out), start=start, end=end, ja=text))
        print(f"  [{fmt_lrc(start)}] {text}", flush=True)

    asr_vercel.transcribe(path, args.asr_model, on_line)
    return out


SENTENCE_END = ("。", "！", "？", "!", "?", "…", "♪")
MAX_LINE_CHARS = 40   # 이보다 길면 쉼표에서도 끊기
MAX_GAP = 0.8         # 단어 사이 공백이 이보다 길면 끊기


def split_segment(seg) -> list[tuple[float, float, str]]:
    """Whisper 세그먼트가 길게 뭉쳐 나오면 문장 단위로 잘라 자막 한 줄을 짧게 유지."""
    words = seg.words or []
    if not words:
        return [(seg.start, seg.end, seg.text.strip())]
    pieces: list[tuple[float, float, str]] = []
    cur, cur_start, cur_end = "", words[0].start, words[0].end
    for i, w in enumerate(words):
        if cur and w.start - cur_end > MAX_GAP:
            pieces.append((cur_start, cur_end, cur.strip()))
            cur, cur_start = "", w.start
        cur += w.word
        cur_end = w.end
        last = i == len(words) - 1
        token = w.word.strip()
        if not last and (token.endswith(SENTENCE_END) or (len(cur) >= MAX_LINE_CHARS and token.endswith(("、", ",")))):
            pieces.append((cur_start, cur_end, cur.strip()))
            cur, cur_start = "", words[i + 1].start
    if cur.strip():
        pieces.append((cur_start, cur_end, cur.strip()))
    return pieces


# ---------------------------------------------------------------- 번역

SYSTEM_PROMPT = """\
당신은 일본 동인음성(ASMR, 음성 드라마) 전문 일→한 번역가입니다.
성인 청취자가 자기가 구매한 작품을 이해하려고 쓰는 자막이며, 작품은 허구입니다.

번역 규칙:
- 원문의 의미, 감정, 뉘앙스를 충실하게 옮긴다. 수위 높은 표현도 순화하거나 빼지 않는다.
- 캐릭터 말투를 살린다: 반말/존댓말, 귀여운 말투, 장난스러운 말투, 메이드/누나/소꿉친구 등 캐릭터성.
- 청자를 부르는 호칭(お兄ちゃん, 先輩, ご主人様 등)은 한국어로 자연스러운 호칭(오빠, 선배, 주인님 등)으로.
- 의성어·의태어·숨소리(はぁ, んっ, ちゅっ 등)는 한국어 자막에서 자연스러운 표기로.
- 받아쓰기(음성인식) 결과라 오탈자·잘못 들은 단어가 있을 수 있다. 앞뒤 문맥으로 원래 의도를 추정해서 번역한다.
- 각 줄은 자막 한 줄이다. 줄을 합치거나 나누지 말고, 입력된 모든 id 에 대해 정확히 하나씩 번역을 낸다.
- 번역문만 쓴다. 설명이나 역주는 붙이지 않는다.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "ko": {"type": "string"},
                },
                "required": ["id", "ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def build_prompt(chunk: list[Segment], previous: list[Segment], work_context: str | None) -> str:
    parts = []
    if work_context:
        parts.append(f"<작품정보>\n{work_context}\n</작품정보>")
    if previous:
        prev = "\n".join(f"{s.ja}  =>  {s.ko}" for s in previous)
        parts.append(f"<직전_대사_참고용>\n{prev}\n</직전_대사_참고용>")
    lines = "\n".join(json.dumps({"id": s.id, "ja": s.ja}, ensure_ascii=False) for s in chunk)
    parts.append(f"<번역할_대사>\n{lines}\n</번역할_대사>")
    parts.append("위 <번역할_대사> 의 각 줄을 한국어로 번역하세요.")
    return "\n\n".join(parts)


def translate_chunk(client, chunk: list[Segment], previous: list[Segment], args) -> dict[int, str]:
    import anthropic

    try:
        response = client.beta.messages.create(
            model=args.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(chunk, previous, args.context)}],
            output_config={
                "effort": args.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # 안전 분류기가 거절하면 서버가 권장 모델로 자동 재시도
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        sys.exit("ANTHROPIC_API_KEY 가 없거나 잘못됐습니다. README 의 'API 키' 항목을 보세요.")
    except anthropic.APIStatusError as e:
        print(f"  ! 번역 API 오류 ({e.status_code}): {e.message} — 이 묶음은 일본어로 남깁니다.")
        return {}
    except anthropic.APIConnectionError:
        print("  ! 네트워크 오류 — 이 묶음은 일본어로 남깁니다.")
        return {}

    if response.stop_reason == "refusal":
        print("  ! 번역이 거절됨 — 이 묶음은 일본어로 남깁니다.")
        return {}
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"  ! 번역 결과를 읽지 못함 (stop_reason={response.stop_reason}) — 일본어로 남깁니다.")
        return {}
    return {line["id"]: line["ko"].strip() for line in data["lines"]}


def translate(segments: list[Segment], args) -> None:
    import anthropic

    client = anthropic.Anthropic(max_retries=4)
    previous: list[Segment] = []
    for i in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[i:i + CHUNK_SIZE]
        print(f"  번역 중… {i + 1}-{i + len(chunk)} / {len(segments)}", flush=True)
        result = translate_chunk(client, chunk, previous, args)
        missing = 0
        for seg in chunk:
            seg.ko = result.get(seg.id, "")
            if not seg.ko:
                missing += 1
                seg.ko = seg.ja  # 빠진 줄은 원문 유지
        if missing and result:
            print(f"  ! {missing}줄 번역 누락 — 원문으로 채움")
        previous = chunk[-CONTEXT_LINES:]


# ---------------------------------------------------------------- 자막 파일

def fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def fmt_vtt(t: float) -> str:
    return fmt_srt(t).replace(",", ".")


def fmt_lrc(t: float) -> str:
    cs = int(round(t * 100))
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{m:02}:{s:02}.{cs:02}"


def line_text(seg: Segment, bilingual: bool, translated: bool) -> list[str]:
    if not translated:
        return [seg.ja]
    if bilingual and seg.ko != seg.ja:
        return [seg.ko, seg.ja]
    return [seg.ko]


def write_subtitles(base: Path, segments: list[Segment], formats: list[str], bilingual: bool, translated: bool) -> list[Path]:
    suffix = ".ko" if translated else ".ja"
    written = []
    if "srt" in formats:
        blocks = [
            f"{n}\n{fmt_srt(s.start)} --> {fmt_srt(s.end)}\n" + "\n".join(line_text(s, bilingual, translated))
            for n, s in enumerate(segments, 1)
        ]
        p = base.with_name(base.name + suffix + ".srt")
        p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8-sig")  # BOM: 윈도우 플레이어 한글 깨짐 방지
        written.append(p)
    if "vtt" in formats:
        blocks = [
            f"{fmt_vtt(s.start)} --> {fmt_vtt(s.end)}\n" + "\n".join(line_text(s, bilingual, translated))
            for s in segments
        ]
        p = base.with_name(base.name + suffix + ".vtt")
        p.write_text("WEBVTT\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")
        written.append(p)
    if "lrc" in formats:
        lines = []
        for s in segments:
            lines.append(f"[{fmt_lrc(s.start)}]" + " / ".join(line_text(s, bilingual, translated)))
            lines.append(f"[{fmt_lrc(s.end)}]")  # 대사 끝나면 화면 비우기
        p = base.with_name(base.name + suffix + ".lrc")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(p)
    return written


# ---------------------------------------------------------------- main

def collect_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"! 없는 경로: {p}")
    return files


def load_whisper(args):
    from faster_whisper import WhisperModel

    device = args.device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    print(f"Whisper 모델 로딩: {args.model} ({device}, {compute_type}) — 첫 실행은 다운로드 때문에 오래 걸립니다")
    return WhisperModel(args.model, device=device, compute_type=compute_type)


def main() -> None:
    ap = argparse.ArgumentParser(description="일본어 음성 → 한국어 자막")
    ap.add_argument("inputs", nargs="+", help="오디오 파일 또는 폴더")
    ap.add_argument("--asr", default="vercel", choices=["vercel", "local"],
                    help="받아쓰기 방식: vercel(API, 기본) / local(내 PC faster-whisper)")
    ap.add_argument("--asr-model", default="mai", help="--asr vercel 일 때 모델: mai(기본) / grok / 전체 모델 ID")
    ap.add_argument("--model", default="large-v3", help="--asr local 일 때 Whisper 모델 (기본 large-v3)")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--formats", default="srt,vtt,lrc", help="출력 형식, 쉼표 구분 (srt,vtt,lrc)")
    ap.add_argument("--bilingual", action="store_true", help="한국어 아래에 일본어 원문도 표시")
    ap.add_argument("--no-translate", action="store_true", help="번역 없이 일본어 받아쓰기만")
    ap.add_argument("--context", help="작품 정보 (캐릭터 이름/관계/상황). 번역 품질이 올라감")
    ap.add_argument("--prompt", help="Whisper 힌트 (캐릭터 이름 등 고유명사를 일본어로)")
    ap.add_argument("--retranslate", action="store_true", help="받아쓰기 캐시는 두고 번역만 다시")
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 처음부터 다시")
    ap.add_argument("--claude-model", default=CLAUDE_MODEL)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    args = ap.parse_args()

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    files = collect_inputs(args.inputs)
    if not files:
        sys.exit("처리할 오디오 파일이 없습니다.")

    whisper = None
    for n, path in enumerate(files, 1):
        print(f"\n[{n}/{len(files)}] {path.name}")
        base = path.with_suffix("")
        cache = base.with_name(base.name + ".jp2ko.json")

        segments: list[Segment] | None = None
        if cache.exists() and not args.force:
            segments = [Segment(**d) for d in json.loads(cache.read_text(encoding="utf-8"))]
            print(f"  받아쓰기 캐시 사용 ({len(segments)}줄)")

        if segments is None:
            if args.asr == "vercel":
                segments = transcribe_vercel(path, args)
            else:
                if whisper is None:
                    whisper = load_whisper(args)
                segments = transcribe(path, whisper, args)
            if not segments:
                print("  대사를 찾지 못했습니다 (무음/효과음 트랙?) — 건너뜀")
                continue

        translated = not args.no_translate
        if translated and (args.force or args.retranslate or any(not s.ko for s in segments)):
            translate(segments, args)

        cache.write_text(json.dumps([asdict(s) for s in segments], ensure_ascii=False, indent=1), encoding="utf-8")
        for p in write_subtitles(base, segments, formats, args.bilingual, translated):
            print(f"  → {p.name}")


if __name__ == "__main__":
    main()
