"""Vercel AI Gateway 받아쓰기 (MAI-Transcribe-2 / Grok STT 등).

GPU 없이 받아쓰기를 API로 처리한다. 긴 트랙은 무음 지점에서 2분 안팎으로 잘라
조각마다 보내고, 돌아온 타임스탬프에 조각 시작 시간을 더해 이어 붙인다.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://ai-gateway.vercel.sh/v4/ai/transcription-model"
MODEL_ALIASES = {
    "mai": "microsoft/mai-transcribe-2",
    "mai-transcribe-2": "microsoft/mai-transcribe-2",
    "grok": "spacexai/grok-stt",
    "grok-stt": "spacexai/grok-stt",
}

TARGET_CHUNK = 120.0   # 이 길이쯤에서 무음 지점을 찾아 자른다
MAX_CHUNK = 180.0      # 무음이 없어도 이 길이면 강제로 자른다
SENTENCE_RE = re.compile(r".+?(?:[。！？!?…♪]+|$)", re.S)


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


# ---------------------------------------------------------------- 자르기

def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def find_silences(path: Path) -> list[float]:
    """무음 구간의 가운데 시점 목록."""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "silencedetect=noise=-40dB:d=0.4", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return [(s + e) / 2 for s, e in zip(starts, ends)]


def plan_chunks(duration: float, silences: list[float]) -> list[tuple[float, float]]:
    chunks, start = [], 0.0
    while duration - start > MAX_CHUNK:
        window = [s for s in silences if start + TARGET_CHUNK * 0.5 <= s <= start + MAX_CHUNK]
        # 목표 길이에 가장 가까운 무음에서 자르기
        cut = min(window, key=lambda s: abs(s - (start + TARGET_CHUNK))) if window else start + TARGET_CHUNK
        chunks.append((start, cut))
        start = cut
    chunks.append((start, duration))
    return chunks


def export_chunk(src: Path, start: float, end: float, dst: Path) -> None:
    # 모노 16kHz mp3 — 음성인식에 충분하고 업로드가 가볍다
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
         "-ac", "1", "-ar", "16000", "-b:a", "48k", str(dst)],
        check=True,
    )


# ---------------------------------------------------------------- API

def call_api(audio: bytes, model: str, api_key: str, provider_options: dict | None) -> dict:
    body = {"audio": base64.b64encode(audio).decode(), "mediaType": "audio/mpeg"}
    if provider_options:
        body["providerOptions"] = provider_options
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "ai-gateway-protocol-version": "0.0.1",
            "ai-transcription-model-specification-version": "4",
            "ai-model-id": model,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def provider_options_for(model: str) -> dict | None:
    if model.startswith("microsoft/"):
        return {"azure": {"locales": ["ja-JP"]}}
    return None


def transcribe_chunk(audio: bytes, model: str, api_key: str) -> dict:
    options = provider_options_for(model)
    for attempt in range(4):
        try:
            return call_api(audio, model, api_key, options)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (401, 403):
                raise SystemExit(f"AI Gateway 인증 실패 ({e.code}). AI_GATEWAY_API_KEY 를 확인하세요.\n{detail}")
            if e.code == 400 and options:
                options = None  # 모델이 옵션을 거부하면 옵션 없이 다시
                continue
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SystemExit(f"AI Gateway 오류 ({e.code}): {detail}")
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    raise SystemExit("AI Gateway 요청이 계속 실패합니다.")


# ---------------------------------------------------------------- 결과 정리

def split_sentences(start: float, end: float, text: str) -> list[tuple[float, float, str]]:
    """긴 구간을 문장 단위로 쪼개고 글자 수 비율로 시간을 나눈다."""
    parts = [p.strip() for p in SENTENCE_RE.findall(text) if p.strip()]
    if len(parts) <= 1:
        return [(start, end, text.strip())]
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        dur = (end - start) * len(p) / total
        out.append((t, t + dur, p))
        t += dur
    return out


def to_lines(result: dict, offset: float, chunk_len: float) -> list[tuple[float, float, str]]:
    segments = result.get("segments") or []
    lines: list[tuple[float, float, str]] = []
    if segments:
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            s = float(seg.get("startSecond", seg.get("start", 0.0)))
            e = float(seg.get("endSecond", seg.get("end", s)))
            lines += split_sentences(offset + s, offset + e, text)
    elif (result.get("text") or "").strip():
        # 타임스탬프가 없으면 조각 전체에 문장을 고르게 배치 (조각이 짧아서 크게 어긋나진 않음)
        lines += split_sentences(offset, offset + chunk_len, result["text"])
    return lines


def transcribe(path: Path, model_name: str, on_line=None) -> list[tuple[float, float, str]]:
    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        raise SystemExit("AI_GATEWAY_API_KEY 환경변수가 없습니다.")
    model = resolve_model(model_name)
    duration = probe_duration(path)
    chunks = plan_chunks(duration, find_silences(path))
    print(f"  {model} 로 받아쓰기 — {duration / 60:.1f}분, {len(chunks)}조각", flush=True)

    lines: list[tuple[float, float, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (start, end) in enumerate(chunks, 1):
            piece = Path(tmp) / f"{i:04}.mp3"
            export_chunk(path, start, end, piece)
            result = transcribe_chunk(piece.read_bytes(), model, api_key)
            new = to_lines(result, start, end - start)
            for line in new:
                if on_line:
                    on_line(*line)
            lines += new
    return lines
