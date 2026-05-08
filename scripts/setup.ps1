# NexusLeague — Windows / PowerShell setup helper.
# Run from the project root:
#   .\scripts\setup.ps1            # full first-time setup
#   .\scripts\setup.ps1 -Run       # also start uvicorn after setup
#   .\scripts\setup.ps1 -ResetDb   # drop and recreate tables

param(
    [switch]$Run,
    [switch]$ResetDb
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host ""; Write-Host ">> $msg" -ForegroundColor Cyan }

# 1. Python venv
if (-not (Test-Path ".\.venv")) {
    Step "Creating virtualenv at .venv"
    python -m venv .venv
}

Step "Activating virtualenv"
. .\.venv\Scripts\Activate.ps1

# 2. Dependencies
Step "Installing requirements"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. .env scaffold
if (-not (Test-Path ".\.env")) {
    Step "Creating .env from .env.example (edit it now!)"
    Copy-Item .env.example .env
    Write-Host "Edit .env in your text editor before continuing." -ForegroundColor Yellow
    Write-Host "Required keys: DATABASE_URL, SECRET_KEY, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI" -ForegroundColor Yellow
    exit 0
}

# 4. DB bootstrap
if ($ResetDb) {
    Step "Resetting database (destructive)"
    python -m scripts.init_db --reset
} else {
    Step "Initialising database"
    python -m scripts.init_db
}

# 5. Optional run
if ($Run) {
    Step "Starting uvicorn at http://localhost:8000"
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
} else {
    Write-Host ""
    Write-Host "Setup complete. Start the server with:" -ForegroundColor Green
    Write-Host "  uvicorn main:app --reload" -ForegroundColor Green
}
