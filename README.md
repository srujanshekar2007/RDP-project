# CivicFix

CivicFix is a community road-damage reporting and repair-tracking application. Citizens can submit reports with photos or videos; authorities can verify and update cases; verified NGOs can propose separate community interventions; administrators manage access and NGO verification.

## What is included

- React, TypeScript, and Vite frontend with role-aware citizen, authority, NGO, and admin workspaces.
- FastAPI backend with JWT authentication, password hashing, role checks, audit history, notifications, and status-transition validation.
- SQLite for local development and PostgreSQL/PostGIS for the Docker deployment. Nearby matching uses PostGIS when available and a Haversine fallback otherwise.
- Image and video analysis with the existing YOLOv8 pothole segmentation checkpoint. Results include detections, confidence, a severity estimate, and annotated media.
- Local media storage by default, with optional S3-compatible storage configuration.
- Docker Compose deployment with a PostGIS database, API, and Nginx-served frontend.

AI severity and duplicate matching are decision-support tools, not guarantees. A person should verify reports and repairs. Video processing runs on the API process using FastAPI background tasks; it is not a crash-durable queue. For a high-volume deployment, move analysis to a separately supervised worker/queue and add monitoring and backups.

## Run locally on Windows

Use two PowerShell terminals from the project folder.

### 1. Start the API

The backend needs Python 3.11 or newer and the dependencies in `backend/requirements.txt`. Activate a virtual environment that has those dependencies and the YOLO model packages installed, then run:

```powershell
Set-Location .\backend
Copy-Item .env.example .env
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The development configuration uses a local SQLite database. `app.main` creates its development tables automatically. The included `backend/models/best.pt` lets local inference run offline; keep `MODEL_OFFLINE=true` in `backend/.env` to prevent model downloads.

### 2. Start the frontend

```powershell
Set-Location .\frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open **http://localhost:5173**. Vite proxies `/api` requests to the local API at port 8000. API documentation and health checks are available at **http://localhost:8000/docs** and **http://localhost:8000/api/health**.

### 3. Create an administrator

With the API environment active:

```powershell
Set-Location .\backend
python -m app.create_admin
```

The script prompts for an email, name, and password. Citizens and NGOs can register in the app; authority accounts must be created or promoted by an administrator.

## Docker deployment

Docker Desktop with Docker Compose is required. From the project root:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace both placeholder values with unique secrets. Then start the services:

```powershell
docker compose up --build -d
docker compose ps
```

Open **http://localhost:8080**. The first API start applies the Alembic migration. Create the first administrator with:

```powershell
docker compose exec backend python -m app.create_admin
```

View service logs with `docker compose logs -f backend frontend db`, and stop the services with `docker compose down`. The named PostgreSQL volume and `backend/data` directory persist database and uploaded-media data. Back them up regularly. Do not expose the database port or API directly to the public internet; put the application behind HTTPS and a production reverse proxy, set restrictive CORS origins, and configure managed secrets and backups.

## Model, offline use, and limits

- Model weights are not committed. When the configured checkpoint is missing and `MODEL_OFFLINE=false`, the API downloads `best.pt` from [keremberke/yolov8n-pothole-segmentation](https://huggingface.co/keremberke/yolov8n-pothole-segmentation) on the first analysis request. The model repository metadata does not declare a license; verify usage rights before deployment. Ultralytics itself is distributed under AGPL-3.0; review its terms before redistributing a deployment.
- To run offline, place an authorized `best.pt` at `MODEL_WEIGHTS_PATH` and set `MODEL_OFFLINE=true`. The web map uses public OpenStreetMap tiles, so map tiles still need internet access.
- The upstream model card lists 0.995 mAP@0.5 for boxes and masks, marked unverified in its metadata. This is not a measured accuracy guarantee for local roads or user-uploaded videos. No “perfect accuracy” claim is made; review AI results before taking action.
- Video inference samples every frame by default (`VIDEO_FRAME_STRIDE=1`). A larger stride processes fewer frames and may reduce processing time, but can miss damage between sampled frames. Tune `VIDEO_INFERENCE_SIZE`, `VIDEO_FRAME_STRIDE`, `VIDEO_MAX_SECONDS`, and `UPLOAD_MAX_MB` for the available CPU, workload, and review requirements.
- CPU inference is supported. A CUDA-enabled environment can accelerate inference if compatible PyTorch/CUDA packages and hardware are installed.
- MapLibre uses public OpenStreetMap raster tiles by default; production use should select a tile provider and follow its usage policy.

## Configuration

`backend/.env.example` documents API settings for local development, including the database URL, JWT secret, CORS origins, storage backend, model path/offline mode, upload limits, and video settings. The root `.env.example` is for Docker Compose secrets and origins. Never commit real environment files, credentials, or user media.

For S3-compatible storage, set `STORAGE_BACKEND=s3`, provide `S3_BUCKET`, and configure the endpoint, region, and credentials. Use the deployment platform's secret manager instead of writing credentials into source control.
