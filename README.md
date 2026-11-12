# 🌌 SPHEREx Planet X

**Interactive web tool that turns any RA/Dec coordinate into a time-series flipbook of real NASA SPHEREx observations — with AI analysis and contextual chat.**

Built for NASA Space Apps Challenge 2026 · Team submission

**Live deployment**:
- Frontend: https://spherex-planet-x-1.onrender.com
- Backend API: https://spherex-planet-x.onrender.com
- API docs: https://spherex-planet-x.onrender.com/docs

---

## The Real Data

Our flipbook is not a simulation. For WISE 0855 (nearest Y-dwarf brown dwarf):

| Metric | Value |
|---|---|
| Total MEFs covering the position | **278** |
| Unique observation epochs | **142** |
| Time span of returned flipbook | **362–364 days** (May 2025 → May 2026) |
| Default cutout size | **0.3°** (configurable 0.1–1.0°) |
| Frames in default demo | 5 (selectable 1–10) |
| Data source | IRSA · SPHEREx QR2 release |
| Strict-bad pixels | **0.00%** |

Every frame is a real SPHEREx spectral image — parsed from FITS, normalized, and served as WebP.

---

## What It Does

1. User enters RA/Dec (or shares a link with `?ra=X&dec=Y&n_frames=Z`)
2. Backend queries NASA IRSA via SIA for SPHEREx Spectral Image MEFs
3. Downloads small 0.3° cutouts — not the full 70 MB MEF
4. Parses FITS, extracts IMAGE HDU, normalizes each epoch to WebP
5. Frontend renders frames as an interactive time-series flipbook
6. **AI Insight** — Gemini (via OpenRouter) generates ~80-word interpretation
7. **AI Chat** — multi-turn conversation about the current observation

---

## ✅ Working End-to-End (Verified, Deployed)

### Data pipeline
- IRSA SIA query via `astroquery.ipac.irsa.Irsa`
- QR2 collection confirmed working (QR3 not yet covering WISE 0855)
- Cutout service integration (`?center=RA,DEC&size=0.3`)
- FITS parsing: `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI`, `PSF`, `WCS-WAVE`
- WCS validated (celestial coordinates present)
- FLAGS bitmask-aware quality checks (bit 21 = benign, strict-bad rate 0%)
- Multi-epoch fetcher with **exponential backoff retry** on HTTP 5xx
- **Spread selection** — picks N frames evenly across full time range
- Flipbook assembly (animated WebP + GIF)

### Backend API
- `GET  /health` — service status + AI provider availability
- `POST /api/cutouts/frames` — fetch N epochs, return WebP URLs + real quality metrics
- `POST /api/explain` — AI-generated astrophysical interpretation (~80 words)
- `POST /api/chat` — multi-turn conversation with observation context
- Retry-on-5xx with 3 attempts, exponential backoff (3s → 6s → 12s)
- Disk caching — repeat queries in ~2–3 sec (measured through Render)
- OpenRouter → direct Gemini fallback chain

### Frontend
- ✅ **LiveDataPanel** — real SPHEREx flipbook + AI insight + AI chat
- ✅ **URL sync** — RA/Dec/frames written to URL after fetch (shareable)
- ✅ **Auto-fetch on load** — opening a shared URL loads the same observation
- ✅ **Dynamic target name** — resolves preset name or `"RA X° / Dec Y°"`
- ✅ **Client-side validation** — friendly error messages (not raw Pydantic JSON)
- ✅ **Real quality stats** — frame success %, cadence, source from actual API response
- ✅ **Interactive design vision** — canvas modes, overlays, playback all work
- ✅ **CSV export** — Discovery log downloads as file
- ✅ **Accessibility** — aria-labels on all inputs, buttons, sliders

### AI Integration
- **2 providers**: OpenRouter (primary) + Gemini direct (fallback)
- **2 endpoints**: `/api/explain` (one-shot) + `/api/chat` (multi-turn)
- Robust error handling: reasoning models, None content, rate limits, timeouts
- Context-aware: injects RA/Dec, epochs, span into prompt
- Demo-verified with `nvidia/nemotron-3.5-super-vl:free` (also works with `meta-llama/llama-3.3-70b-instruct:free`)

### CI Pipeline
- GitHub Actions on every push/PR
- Real IRSA query in CI (not mocked)
- Uploads artifacts (FITS, plots, logs)
- `main` branch protected, requires green CI + PR approval

---

## 🏗 Architecture

```
User (browser)
    ↓ RA/Dec input OR ?ra=X&dec=Y URL
Frontend (React + Vite)
    ↓ POST /api/cutouts/frames (proxied)
Backend (FastAPI)
    ↓ Irsa.query_sia
NASA IRSA (SIA service)
    ↓ MEF access_url
Backend
    ↓ access_url?center=RA,DEC&size=0.3
NASA IRSA (cutout service)
    ↓ ~5 MB FITS cutout
Backend
    ↓ astropy parses IMAGE HDU
    ↓ percentile normalize (5-95%)
    ↓ save as 20 KB WebP
    ↓ {"frames": [...], "meta": {...}}
Frontend renders flipbook

    ↓ POST /api/explain (metadata only)
Backend → OpenRouter → Gemini/Llama
    ↓ ~80-word interpretation
Frontend typewriter effect

    ↓ POST /api/chat (message history)
Backend → OpenRouter → Gemini/Llama
    ↓ contextual reply
Frontend chat panel
```

Detailed version: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite 5 |
| Styling | Tailwind CSS (CDN in index.html) |
| State | Zustand (with localStorage persistence) |
| Charts | Chart.js 4 |
| Icons | Lucide React |
| Backend | Python 3.11 + FastAPI + Uvicorn |
| Astronomy | astropy 8.0, astroquery 0.4, pyvo |
| AI | OpenRouter (Gemini / Nemotron / Llama), direct Gemini fallback |
| Data | FITS MEF → WebP via Pillow |
| CI/CD | GitHub Actions |
| Deployment | Render (backend + frontend) |

---

## Quick Start (Local)

### 1. Install dependencies

```
pip install -r requirements.txt
npm.cmd install       # Windows
npm install           # macOS/Linux
```

### 2. Set AI env vars (terminal)

```
# PowerShell
$env:OPENROUTER_API_KEY = "sk-or-v1-YOUR-KEY"
$env:OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl:free"
```

Get key: https://openrouter.ai/keys (free tier available)

### 3. Start backend (Terminal 1)

```
uvicorn app.main:app --reload --port 8000
```

Verify: http://127.0.0.1:8000/health → `{"status":"ok", "ai":{"openrouter":true,...}}`

### 4. Start frontend (Terminal 2)

```
npm.cmd run dev       # Windows
npm run dev           # macOS/Linux
```

Open: http://localhost:5173

### 5. Test

- RA: `133.795129`, Dec: `-7.245147` (WISE 0855)
- Frames: `5`
- Click **FETCH** → flipbook loads
- Click **GENERATE AI INSIGHT** → AI interpretation streams
- Click **ASK ABOUT THIS DATA** → chat opens, ask "Why does proper motion matter?"

**First fetch**: 90–180 sec (real NASA download of 5 × 5 MB cutouts)
**Subsequent fetches**: ~2–3 sec (disk-cached WebP frames)

---

## API Reference

### `POST /api/cutouts/frames`

```json
{
  "ra": 133.795129,
  "dec": -7.245147,
  "size_deg": 0.3,
  "n_frames": 5,
  "collection": "spherex_qr2"
}
```

```json
{
  "ra": 133.795129,
  "dec": -7.245147,
  "collection": "spherex_qr2",
  "total_found": 278,
  "returned_frames": 5,
  "frames": [
    {
      "frame_index": 210,
      "epoch_mjd": 60800.700028,
      "epoch_date": "2025-05-05T16:48:02",
      "image_url": "/static/frames/133p7951_m7p2451_60800p7000.webp",
      "mef_url": "https://irsa.ipac.caltech.edu/..."
    }
  ],
  "meta": {
    "size_deg": 0.3,
    "cadence_hours": 2104.93,
    "span_days": 364.0,
    "unique_epochs": 142
  }
}
```

### `POST /api/explain`

```json
{
  "ra": 133.795129,
  "dec": -7.245147,
  "target_name": "WISE 0855",
  "unique_epochs": 142,
  "span_days": 362,
  "cadence_hours": 2104.93
}
```

```json
{
  "explanation": "WISE 0855 is a Y-dwarf brown dwarf...",
  "provider": "openrouter",
  "model": "nvidia/nemotron-3.5-super-vl:free"
}
```

### `POST /api/chat`

```json
{
  "messages": [
    {"role": "user", "content": "Why does proper motion matter?"}
  ],
  "context": {
    "ra": 133.795129,
    "dec": -7.245147,
    "target_name": "WISE 0855",
    "unique_epochs": 142,
    "span_days": 362
  }
}
```

```json
{
  "reply": "Proper motion shows how fast the object moves across the sky...",
  "provider": "openrouter",
  "model": "nvidia/nemotron-3.5-super-vl:free"
}
```

Full API docs: [docs/API.md](docs/API.md)

---

## Repository Structure

```
spherex-planet-x/
├── .github/workflows/
│   └── spherex-test.yml            # CI pipeline
├── app/
│   └── main.py                     # FastAPI backend (3 endpoints)
├── scripts/
│   ├── spherex_pipeline_test.py    # End-to-end CI test
│   ├── analyze_epochs.py           # Epoch feasibility report
│   ├── decode_flags.py             # FLAGS bitmask analysis
│   ├── fetch_epoch_cutouts.py      # Multi-epoch CLI downloader
│   └── assemble_flipbook.py        # Frames → animated WebP
├── src/
│   ├── main.tsx                    # Entry point
│   ├── App.tsx                     # Header + LiveDataPanel + Design Vision + Footer
│   ├── components/
│   │   ├── LiveDataPanel.tsx       # Real data + AI insight + AI chat
│   │   ├── Header.tsx              # Share button → modal
│   │   ├── ShareModal.tsx          # Copy URL from window.location
│   │   ├── TargetNavigator.tsx     # Design mockup inputs
│   │   ├── SkyViewerCanvas.tsx     # Interactive canvas (4 modes)
│   │   ├── SEDChart.tsx            # Chart.js SED plot
│   │   ├── BandSelector.tsx        # 6 arrays + fine tuning
│   │   ├── CatalogOverlays.tsx     # Gaia/WISE/Simbad toggles
│   │   ├── MetadataPanel.tsx       # Mockup quality flags
│   │   ├── DiscoveryLog.tsx        # CSV export
│   │   └── Footer.tsx
│   ├── store/index.ts              # Zustand store
│   ├── constants/presets.ts        # 6 preset targets
│   └── types/index.ts              # TypeScript types
├── static/frames/                  # Served WebP frames (runtime cache)
├── outputs/                        # Gitignored FITS + plots
├── docs/
│   ├── API.md
│   └── ARCHITECTURE.md
├── requirements.txt                # Python deps
├── package.json                    # Node deps
├── vite.config.ts                  # Proxy /api and /static
├── tsconfig.json
├── tailwind.config.js
├── CONTRIBUTING.md
└── README.md
```

---

## Configuration

### Environment variables

```bash
# Required for AI features
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free

# Optional: direct Gemini fallback
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash

# Frontend (if not using Vite proxy)
VITE_BACKEND_URL=http://localhost:8000
```

See `.env.example` for the full template. `.env` is gitignored.

### Data configuration (`app/main.py`)

| Parameter | Default | Notes |
|---|---|---|
| `size_deg` | 0.3 | Cutout size (frontend sends 0.3; API allows 0.1–1.0) |
| `n_frames` | 5 | Frames per request (frontend clamps 1–10) |
| `collection` | `spherex_qr2` | IRSA collection ID |
| `workers` | 3 | Parallel download threads |
| `retries` | 3 | HTTP 5xx retry attempts |
| Search radius | 0.05° | SIA query radius |

---

## Key Data Insights

### SPHEREx QR2 vs QR3

- **QR2**: 278 MEFs covering WISE 0855 — **works**
- **QR3**: 0 results at this position (rolling release not yet covering)

Backend tries QR3 first, falls back to QR2 automatically.

### FLAGS bitmask (from `decode_flags.py`)

| Bit | Fraction | Interpretation |
|---|---|---|
| 21 | 28.8% | Benign coverage / detection flag |
| 19 | 2.3% | Minor coverage |
| 17 | 0.1% | Edge case |
| 0, 2, 11 | <1% total | Minor flags |
| **Strict-bad (bits 3+)** | **0.00%** | Zero bad pixels in sample |

### PSF dominates cutout size

For a 0.3° cutout (~5 MB FITS):

| Extension | Size |
|---|---|
| IMAGE | ~130 KB |
| FLAGS | ~130 KB |
| VARIANCE | ~130 KB |
| ZODI | ~130 KB |
| **PSF** | **~4.5 MB (95%)** |
| WCS-WAVE | tiny |

IRSA cutout service does not support HDU filtering. Full ~5 MB download unavoidable.

---

## Known Limitations

| Limitation | Cause | Mitigation |
|---|---|---|
| First fetch is slow (90–180 s) | Real NASA download (5 × 5 MB) | Demo pre-warm (cache fills disk) |
| Cached fetches ~2–3 s | Render cold start + disk read | Accept; pre-warm before demo |
| QR3 not usable | Rolling release not covering target | QR2 fallback automatic |
| Cutout includes PSF (~95% size) | IRSA service doesn't support HDU filter | Accepted; PSF ignored |
| Partial frame results | IRSA rate limit / transient 5xx | Backend retries; UI shows amber |
| Tailwind via CDN | Not bundled for prod | Post-hackathon: proper build |
| Design Vision panels | Mockup (except LiveDataPanel) | Clearly labeled; roadmap documented |

---

## Demo Strategy

### Pre-warm cache (5 min before demo)

```powershell
$body = @{
    ra = 133.795129
    dec = -7.245147
    size_deg = 0.3
    n_frames = 5
    collection = "spherex_qr2"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://spherex-planet-x.onrender.com/api/cutouts/frames" `
    -Method Post -Body $body -ContentType "application/json"
```

This populates `static/frames/*.webp`. Demo fetches return in ~2–3 sec.

### Talking points

- **"Real NASA data"** — 142 epochs of SPHEREx observations
- **"362 days of WISE 0855"** — one year of brown dwarf variability
- **"AI does the science"** — Gemini interprets proper motion, variability, molecular absorption
- **"Chat with the data"** — multi-turn conversation about the observation
- **"Production pipeline"** — retry logic, spread selection, bitmask-aware quality

### Honest framing (critical for judges)

- ✅ **Top "LIVE SPHEREx DATA"** = real NASA data from IRSA
- ⚠️ **Bottom "Design Vision"** = interactive UI mockup for post-hackathon

Say it upfront: *"Top panel is real. Bottom is our UI vision."* Judges respect transparency.

### Judge Q&A prep

| Question | Answer |
|---|---|
| **Is this real data?** | Top panel queries IRSA live. Bottom is mockup. |
| **How does AI work?** | Metadata sent to Gemini via OpenRouter. No images sent. |
| **What if IRSA down?** | Disk cache + retry. Fallback URL documented. |
| **Scalability?** | Single-target MVP. Prod: S3 cache + Redis + background sync. |
| **Why WISE 0855?** | Nearest brown dwarf, well-studied, perfect time-domain test case. |
| **Planet X angle?** | Same tool works for any RA/Dec. Candidate = proper motion + variability check. |

---

## Team

| Member | Role |
|---|---|
| **Lidiya** | Frontend design + UI components + QA |
| **Anshuman** | Backend + NASA data pipeline |
| **Srikanth** | Deployment + Demo/Pitch + GitHub setup |
| **Prateek** | AI/ML + Gemini integration |

---

## Status

**Current state**: Deployed. End-to-end working with real data + AI.

**Live**:
- ✅ Backend on Render with AI endpoints
- ✅ Frontend on Render with full App
- ✅ Real NASA data pipeline
- ✅ AI insight + AI chat
- ✅ URL-based sharing (auto-fetch on load)
- ✅ Client-side validation
- ✅ CSV export
- ✅ CI with real IRSA tests

**Post-hackathon roadmap**:
- Wire Design Vision panels to real FITS data
- Bundle Tailwind properly (drop CDN)
- Spectral flipbook (102 wavelength channels per epoch)
- Multi-target comparison mode
- Redis cache + background sync
- Full frontend test coverage

---

## License

Released under the [MIT License](LICENSE).

**Data attribution**: SPHEREx data is provided by NASA/IPAC Infrared Science Archive (IRSA) and is in the public domain. This project's code is independent of NASA and covered by MIT.

**AI providers**: Uses OpenRouter (Gemini, Nemotron, Llama) and Google Gemini. See each provider's terms of service.

---

**Last updated**: 2026-11-12
**Repository**: [shrikanthnahak1506-svg/spherex-planet-x](https://github.com/shrikanthnahak1506-svg/spherex-planet-x)
