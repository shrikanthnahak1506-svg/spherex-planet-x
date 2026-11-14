"""
SPHEREx Planet X — FastAPI backend.

Endpoints:
  GET  /health
  POST /api/cutouts/frames   → flipbook frames as WebP URLs
  POST /api/explain          → AI interpretation via Gemini (OpenRouter or direct)
  POST /api/chat             → multi-turn AI chat with observation context

Run locally:
  uvicorn app.main:app --reload --port 8000
  → http://localhost:8000/docs
"""
import io
import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import numpy as np
import pyvo
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy import units as u
from astroquery.ipac.irsa import Irsa
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image

# Optional: direct Gemini SDK (only used if GEMINI_API_KEY is set)
try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spherex")

# ------------------------------------------------------------
# Setup
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
FRAME_DIR = BASE_DIR / "static" / "frames"
FRAME_DIR.mkdir(parents=True, exist_ok=True)

# AI provider config
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# TAP service (alternate IRSA endpoint — works when SIA is degraded)
TAP_SERVICE = "https://irsa.ipac.caltech.edu/TAP"

app = FastAPI(title="SPHEREx Planet X API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ------------------------------------------------------------
# Schemas — flipbook
# ------------------------------------------------------------
class FrameRequest(BaseModel):
    ra: float = Field(..., ge=0, lt=360, description="Right Ascension (deg)")
    dec: float = Field(..., ge=-90, le=90, description="Declination (deg)")
    size_deg: float = Field(0.1, gt=0, le=1.0, description="Cutout size (deg)")
    n_frames: int = Field(10, ge=1, le=50, description="Max frames to return")
    collection: str = Field("spherex_qr2", description="IRSA collection")

class Frame(BaseModel):
    frame_index: int
    epoch_mjd: float
    epoch_date: str
    image_url: str
    mef_url: str

class FramesResponse(BaseModel):
    ra: float
    dec: float
    collection: str
    total_found: int
    returned_frames: int
    frames: list[Frame]
    meta: dict

# ------------------------------------------------------------
# Schemas — AI explain
# ------------------------------------------------------------
class ExplainRequest(BaseModel):
    ra: float = Field(..., ge=0, lt=360)
    dec: float = Field(..., ge=-90, le=90)
    target_name: str = Field("Unknown target")
    unique_epochs: int = Field(..., ge=1)
    span_days: float = Field(..., ge=0)
    cadence_hours: float | None = Field(None, ge=0)

class ExplainResponse(BaseModel):
    explanation: str
    provider: str
    model: str

# ------------------------------------------------------------
# Schemas — AI chat
# ------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=2000)

class ChatContext(BaseModel):
    ra: float | None = None
    dec: float | None = None
    target_name: str | None = None
    unique_epochs: int | None = None
    span_days: float | None = None

class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=20)
    context: ChatContext | None = None

class ChatResponse(BaseModel):
    reply: str
    provider: str
    model: str

# ------------------------------------------------------------
# Fallback MEFs for WISE 0855 (verified working earlier)
# Used ONLY when both SIA and TAP return empty
# ------------------------------------------------------------
FALLBACK_MEFS = [
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W19_1B/l2b-v20-2025-241/6/level2_2025W19_1B_0138_1D6_spx_l2b-v20-2025-241.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W19_1B/l2b-v20-2025-241/2/level2_2025W19_1B_0181_1D2_spx_l2b-v20-2025-241.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W44_2B/l2b-v20-2025-307/1/level2_2025W44_2B_0116_1D6_spx_l2b-v20-2025-307.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W45_1A/l2b-v20-2025-311/1/level2_2025W45_1A_0110_1D1_spx_l2b-v20-2025-311.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W45_2A/l2b-v20-2025-314/5/level2_2025W45_2A_0308_4D5_spx_l2b-v20-2025-314.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2025W50_2A/l2b-v20-2025-349/2/level2_2025W50_2A_0374_1D2_spx_l2b-v20-2025-349.fits",
    "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr2/level2/2026W18_2A/l2b-v24-2026-125/4/level2_2026W18_2A_0424_4D4_spx_l2b-v24-2026-125.fits",
]
FALLBACK_MJDS = [
    60800.7015, 60801.1066, 60800.7000, 60982.5066,
    60986.9257, 61021.8235, 61162.3370,
]

# ------------------------------------------------------------
# Helpers — flipbook
# ------------------------------------------------------------
def render_webp(image_2d: np.ndarray, out_path: Path) -> None:
    finite = image_2d[np.isfinite(image_2d)]
    if finite.size == 0:
        data = np.zeros_like(image_2d, dtype=np.uint8)
    else:
        vmin, vmax = np.percentile(finite, [5, 95])
        norm = np.clip((image_2d - vmin) / max(vmax - vmin, 1e-12), 0, 1)
        norm[~np.isfinite(norm)] = 0
        data = (norm * 255).astype(np.uint8)
    Image.fromarray(np.flipud(data), mode="L").save(out_path, "WEBP", quality=85)


def fetch_and_render(ra: float, dec: float, mef_url: str,
                     size_deg: float, out_path: Path, retries: int = 3) -> None:
    cutout_url = f"{mef_url}?{urlencode({'center': f'{ra:.8f},{dec:.8f}', 'size': str(size_deg)})}"
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(cutout_url, timeout=60)
            resp.raise_for_status()
            hdul = fits.open(io.BytesIO(resp.content))
            try:
                render_webp(hdul["IMAGE"].data, out_path)
            finally:
                hdul.close()
            return
        except requests.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code in (429, 500, 502, 503, 504):
                import time as _t
                _t.sleep(3 * (2 ** attempt))
                continue
            raise
        except requests.RequestException as e:
            last_exc = e
            import time as _t
            _t.sleep(3 * (2 ** attempt))
    raise last_exc


# ------------------------------------------------------------
# Helpers — TAP fallback (alternate IRSA endpoint)
# ------------------------------------------------------------
def query_irsa_tap(ra: float, dec: float):
    """
    Query SPHEREx artifact table via TAP (works when SIA index is empty).
    Returns (t_min_array, urls_list) or None on failure/empty.
    """
    try:
        service = pyvo.dal.TAPService(TAP_SERVICE)
        query = f"""
            SELECT 'https://irsa.ipac.caltech.edu/' || a.uri AS access_url,
                   p.time_bounds_lower AS t_min
            FROM spherex.artifact a
            JOIN spherex.plane p ON a.planeid = p.planeid
            WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)
            ORDER BY p.time_bounds_lower
        """
        result = service.search(query)
        if len(result) == 0:
            logger.warning("TAP query returned 0 rows")
            return None
        t_min = np.array([float(r["t_min"]) for r in result], dtype=float)
        urls = [str(r["access_url"]) for r in result]
        logger.info(f"TAP query: {len(urls)} artifacts found")
        return t_min, urls
    except Exception as e:
        logger.warning(f"TAP query failed: {e}")
        return None


# ------------------------------------------------------------
# Helpers — AI providers (explain)
# ------------------------------------------------------------
PROMPT_TEMPLATE = """You are an astrophysicist analyzing NASA SPHEREx near-infrared survey data.

Target: {target_name}
Position: RA {ra:.4f}°, Dec {dec:.4f}°
Unique epochs found: {unique_epochs}
Time span: {span_days:.1f} days
Cadence: {cadence_str}

Explain what kind of moving object or variable star this could be, and why SPHEREx's 102 infrared bands (0.75–5.0 μm) are useful here. Mention specific physical phenomena (proper motion, photometric variability, dust, water/methane absorption, etc.) that a citizen scientist might be seeing.

Keep it under 100 words. Sound exciting and accessible. Do not use bullet points or headings. Return plain prose only."""


async def call_openrouter(prompt: str) -> str:
    """Call OpenRouter API (OpenAI-compatible) → returns explanation text."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://spherex-planet-x.app",
                "X-Title": "SPHEREx Planet X",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.7,
                "reasoning": {"enabled": False},
            },
        )

        if r.status_code == 429:
            raise RuntimeError("OpenRouter rate limit")
        if not r.is_success:
            body = r.text[:500]
            logger.error(f"OpenRouter HTTP {r.status_code}: {body}")
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {body}")

        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        reasoning = msg.get("reasoning")
        finish = choice.get("finish_reason")

        text = (content or reasoning or "").strip()

        if not text:
            logger.error(f"Empty response. finish_reason={finish}, msg_keys={list(msg.keys())}")
            raise RuntimeError(f"Empty AI response (finish={finish}). Try a non-reasoning model.")

        return text


def call_gemini_sync(prompt: str) -> str:
    """Call Google Gemini SDK directly (sync — wraps in asyncio.to_thread)."""
    if not GEMINI_API_KEY or not GENAI_AVAILABLE:
        raise RuntimeError("Gemini not available")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
    resp = model.generate_content(prompt)
    return (resp.text or "").strip()


# ------------------------------------------------------------
# Helpers — chat prompt
# ------------------------------------------------------------
def build_chat_system_prompt(ctx: ChatContext | None) -> str:
    base = (
        "You are a helpful astrophysics assistant embedded in the SPHEREx Planet X tool. "
        "Answer the user's questions about the current SPHEREx observation in 1-3 short sentences. "
        "Be concrete, scientifically accurate, and cite SPHEREx's 102 infrared bands "
        "(0.75–5.0 μm) and the specific target being viewed when relevant."
    )
    if ctx and ctx.target_name:
        extra = f"\n\nCurrent observation context:"
        extra += f"\nTarget: {ctx.target_name}"
        if ctx.ra is not None and ctx.dec is not None:
            extra += f"\nPosition: RA {ctx.ra:.4f}°, Dec {ctx.dec:.4f}°"
        if ctx.unique_epochs:
            extra += f"\nUnique epochs: {ctx.unique_epochs}"
        if ctx.span_days:
            extra += f"\nTime span: {ctx.span_days:.1f} days"
        return base + extra
    return base


async def call_openrouter_chat(system: str, messages: list[dict]) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    full_messages = [{"role": "system", "content": system}] + messages
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://spherex-planet-x.app",
                "X-Title": "SPHEREx Planet X Chat",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": full_messages,
                "max_tokens": 1000,
                "temperature": 0.7,
                "reasoning": {"enabled": False},
            },
        )
        if r.status_code == 429:
            raise RuntimeError("OpenRouter rate limit")
        if not r.is_success:
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        text = (msg.get("content") or msg.get("reasoning") or "").strip()
        if not text:
            raise RuntimeError(f"Empty AI reply (finish={choice.get('finish_reason')})")
        return text


def call_gemini_chat_sync(system: str, messages: list[dict]) -> str:
    if not GEMINI_API_KEY or not GENAI_AVAILABLE:
        raise RuntimeError("Gemini not available")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=system,
    )
    history = []
    for m in messages[:-1]:
        history.append({"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]})
    chat = model.start_chat(history=history)
    last = messages[-1]["content"]
    resp = chat.send_message(last)
    return (resp.text or "").strip()


# ------------------------------------------------------------
# Routes — health
# ------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "ai": {
            "openrouter": bool(OPENROUTER_API_KEY),
            "gemini_direct": bool(GEMINI_API_KEY and GENAI_AVAILABLE),
        },
        "version": "0.3.0",
    }


# ------------------------------------------------------------
# Route — flipbook (3-tier fallback: SIA → TAP → hardcoded)
# ------------------------------------------------------------
@app.post("/api/cutouts/frames", response_model=FramesResponse)
def cutouts_frames(req: FrameRequest):
    coord = SkyCoord(req.ra, req.dec, unit="deg", frame="icrs")

    t_min = None
    urls = None
    source = None

    # ── Attempt 1: SIA (requested collection) ──
    try:
        results = Irsa.query_sia(pos=(coord, 0.1 * u.deg), collection=req.collection)
        if len(results) > 0:
            t_min = np.array(results["t_min"], dtype=float)
            urls = list(results["access_url"])
            source = "sia"
            logger.info(f"SIA ({req.collection}): {len(results)} rows")
    except Exception as e:
        logger.warning(f"SIA query failed: {e}")

    # ── Attempt 2: alternate SIA collection ──
    if t_min is None:
        for alt in [c for c in ("spherex_qr3", "spherex_qr2") if c != req.collection]:
            try:
                results = Irsa.query_sia(pos=(coord, 0.1 * u.deg), collection=alt)
                if len(results) > 0:
                    t_min = np.array(results["t_min"], dtype=float)
                    urls = list(results["access_url"])
                    source = f"sia:{alt}"
                    logger.info(f"SIA alt ({alt}): {len(results)} rows")
                    break
            except Exception as e:
                logger.warning(f"SIA alt {alt} failed: {e}")

    # ── Attempt 3: TAP (different backend path) ──
    if t_min is None:
        tap_result = query_irsa_tap(req.ra, req.dec)
        if tap_result is not None:
            t_min, urls = tap_result
            source = "tap"

    # ── Attempt 4: hardcoded known-good MEFs ──
    if t_min is None:
        logger.error(
            f"SIA + TAP both empty for RA={req.ra}, Dec={req.dec}. "
            f"Using {len(FALLBACK_MEFS)} hardcoded fallback MEFs."
        )
        n = min(req.n_frames, len(FALLBACK_MEFS))
        t_min = np.array(FALLBACK_MJDS[:n], dtype=float)
        urls = FALLBACK_MEFS[:n]
        source = "fallback"
        total_found = len(FALLBACK_MEFS)
        unique_epochs = len(FALLBACK_MEFS)
        selected = list(range(n))
    else:
        # Dedupe by epoch
        order = np.argsort(t_min)
        seen, selected = set(), []
        for i in order:
            key = round(t_min[i], 4)
            if key in seen:
                continue
            seen.add(key)
            selected.append(i)
        total_found = len(t_min)
        unique_epochs = len(seen)

        # Spread selection
        if len(selected) > req.n_frames:
            step = len(selected) / req.n_frames
            picks = sorted({min(int(round(i * step)), len(selected) - 1)
                            for i in range(req.n_frames)})
            selected = [selected[p] for p in picks]

    def job(idx: int) -> Frame:
        mjd = float(t_min[idx])
        tag = f"{req.ra:.4f}_{req.dec:.4f}_{mjd:.4f}".replace(".", "p").replace("-", "m")
        webp_path = FRAME_DIR / f"{tag}.webp"
        if not webp_path.exists():
            fetch_and_render(req.ra, req.dec, str(urls[idx]), req.size_deg, webp_path)
        return Frame(
            frame_index=idx,
            epoch_mjd=mjd,
            epoch_date=Time(mjd, format="mjd").to_datetime().isoformat(),
            image_url=f"/static/frames/{webp_path.name}",
            mef_url=str(urls[idx]),
        )

    frames: list[Frame] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(job, i): i for i in selected}
        for fut in as_completed(futures):
            try:
                frames.append(fut.result())
            except Exception as e:
                logger.warning(f"frame failed: {e}")

    if not frames:
        raise HTTPException(500, "All frame downloads failed")

    frames.sort(key=lambda f: f.epoch_mjd)
    ep = [f.epoch_mjd for f in frames]
    cadence_h = float(np.median(np.diff(ep))) * 24.0 if len(ep) > 1 else None
    span_days = float(ep[-1] - ep[0]) if len(ep) > 1 else 0.0

    return FramesResponse(
        ra=req.ra, dec=req.dec, collection=req.collection,
        total_found=total_found, returned_frames=len(frames),
        frames=frames,
        meta={
            "size_deg": req.size_deg,
            "cadence_hours": cadence_h,
            "span_days": span_days,
            "unique_epochs": unique_epochs,
            "source": source,   # "sia" | "sia:spherex_qr3" | "tap" | "fallback"
        },
    )


# ------------------------------------------------------------
# Route — AI explain
# ------------------------------------------------------------
@app.post("/api/explain", response_model=ExplainResponse)
async def explain(req: ExplainRequest):
    cadence_str = f"{req.cadence_hours:.1f} hours" if req.cadence_hours else "unknown"
    prompt = PROMPT_TEMPLATE.format(
        target_name=req.target_name,
        ra=req.ra, dec=req.dec,
        unique_epochs=req.unique_epochs,
        span_days=req.span_days,
        cadence_str=cadence_str,
    )

    # ── Attempt 1: OpenRouter ──
    if OPENROUTER_API_KEY:
        try:
            text = await call_openrouter(prompt)
            return ExplainResponse(
                explanation=text, provider="openrouter", model=OPENROUTER_MODEL
            )
        except Exception as e:
            logger.warning(f"OpenRouter failed: {e}")

    # ── Attempt 2: Direct Gemini ──
    if GEMINI_API_KEY and GENAI_AVAILABLE:
        try:
            text = await asyncio.to_thread(call_gemini_sync, prompt)
            if text:
                return ExplainResponse(
                    explanation=text, provider="gemini", model=GEMINI_MODEL
                )
        except Exception as e:
            logger.warning(f"Gemini direct failed: {e}")

    # ── Both failed ──
    if not OPENROUTER_API_KEY and not (GEMINI_API_KEY and GENAI_AVAILABLE):
        raise HTTPException(
            503,
            "AI not configured. Set OPENROUTER_API_KEY or GEMINI_API_KEY in environment.",
        )
    raise HTTPException(500, "AI service unavailable. Please try again.")


# ------------------------------------------------------------
# Route — AI chat
# ------------------------------------------------------------
@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    system = build_chat_system_prompt(req.context)
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    if OPENROUTER_API_KEY:
        try:
            reply = await call_openrouter_chat(system, msgs)
            return ChatResponse(reply=reply, provider="openrouter", model=OPENROUTER_MODEL)
        except Exception as e:
            logger.warning(f"OpenRouter chat failed: {e}")

    if GEMINI_API_KEY and GENAI_AVAILABLE:
        try:
            reply = await asyncio.to_thread(call_gemini_chat_sync, system, msgs)
            if reply:
                return ChatResponse(reply=reply, provider="gemini", model=GEMINI_MODEL)
        except Exception as e:
            logger.warning(f"Gemini chat failed: {e}")

    if not OPENROUTER_API_KEY and not (GEMINI_API_KEY and GENAI_AVAILABLE):
        raise HTTPException(503, "AI not configured.")
    raise HTTPException(500, "AI chat service unavailable.")