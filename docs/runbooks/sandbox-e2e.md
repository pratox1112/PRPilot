# Sandbox end-to-end run (local)

The whole bot on one laptop against the sandbox repo `pratox1112/prpilot-sandbox`:
a PR opens → GitHub sends a signed webhook → the bot screens and triages it with the
fine-tuned student → a proposal waits for approval → after approval the executor writes
the labels and the comment to the PR, as the GitHub App.

Everything runs in PowerShell from `C:\PRPilot`. Five terminals: model, database (one
command), app, tunnel, and one for your own commands.

## What you need

- The GitHub App `prpilot-dev-pratox1112`, installed on `prpilot-sandbox` (Issues and Pull
  requests read/write). Its App ID, private key path and webhook secret are already in
  `.env` (Phase 0, task 0.3).
- Docker Desktop running (Postgres).
- The student model `models/student-fill-1016-Q4_K_M.gguf`
  (`sha256:b6e18672b4043587baa67c907d120c7ab02f719ec20c99b50e48ce7fa8ff53bb`), copied from
  Explorer.
- `cloudflared` (installed in Phase 0).

## 1. Model server (llama.cpp, CPU)

llama.cpp build **b11960**, which is release v0.8.0's commit (`8642541b`), the same code the
GGUF was re-tested with on Explorer. Download it outside the repo and check its hash:

```powershell
New-Item -ItemType Directory -Force C:\tools | Out-Null
Invoke-WebRequest https://github.com/ggml-org/llama.cpp/releases/download/b11960/llama-b11960-bin-win-cpu-x64.zip -OutFile C:\tools\llama-b11960.zip
(Get-FileHash C:\tools\llama-b11960.zip -Algorithm SHA256).Hash
```

The hash must be `73F962F82850D244DB47EAACB9D913637027B82399124324298576D58C5F4287`.
Then unzip and start the server (terminal 1, leave it running):

```powershell
Expand-Archive C:\tools\llama-b11960.zip -DestinationPath C:\tools\llama-b11960
C:\tools\llama-b11960\llama-server.exe -m C:\PRPilot\models\student-fill-1016-Q4_K_M.gguf -a prpilot-model --jinja --host 127.0.0.1 --port 8080 -c 16384
```

It is ready when it prints `server is listening on http://127.0.0.1:8080`.

## 2. Database

```powershell
docker compose up -d db
$env:DATABASE_URL = "postgresql+asyncpg://prpilot:prpilot@localhost:5433/prpilot"; uv run alembic upgrade head
```

## 3. Settings in `.env`

Add (or set) these lines. `ADMIN_TOKEN` is any long random string; generate one with
`uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.

```
DATABASE_URL=postgresql+asyncpg://prpilot:prpilot@localhost:5433/prpilot
ADMIN_TOKEN=<the random string>
SANDBOX_REPOS=pratox1112/prpilot-sandbox
EXECUTOR_DRY_RUN=true
MODEL_BASE_URL=http://127.0.0.1:8080/v1
MODEL_NAME=prpilot-model
```

`HF_TOKEN` (for PromptGuard) is already there from Phase 4.

## 4. The app (terminal 3)

```powershell
uv run --group screening uvicorn services.local:app --port 8000
```

## 5. Tunnel (terminal 4)

```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8000
```

It prints a `https://<random>.trycloudflare.com` address (new on every start). In GitHub:
**Settings → Developer settings → GitHub Apps → prpilot-dev-pratox1112 → General → Webhook
URL** = that address + `/webhook`. Save.

## 6. Labels in the sandbox repo

The bot only proposes labels the repo has. In `prpilot-sandbox` → Issues → Labels, make
sure some type labels exist, for example `bug`, `enhancement`, `documentation`,
`refactor`, `test`, `ci`.

## 7. Dry run: open a PR, approve, nothing is written

Open a small PR on `prpilot-sandbox` (a real change: a bug fix or a docs edit). Then, in your
own terminal (terminal 5):

```powershell
$h = @{ Authorization = "Bearer <ADMIN_TOKEN>" }
Invoke-RestMethod http://localhost:8000/runs -Headers $h | Format-List
Invoke-RestMethod http://localhost:8000/proposals -Headers $h | ConvertTo-Json -Depth 6
```

The run's `state` goes `screened` → `triaged` → `proposed` (the student on CPU takes about a
minute). The proposal shows its `content_hash` and its actions (`labels`, maybe `reviewers`,
`comment`). Approve and execute one action:

```powershell
$p = (Invoke-RestMethod http://localhost:8000/proposals -Headers $h)[0]
$body = @{ decision = "approve"; content_hash = $p.content_hash } | ConvertTo-Json
Invoke-RestMethod "http://localhost:8000/proposals/$($p.id)/actions/labels/decision" -Method Post -Headers $h -Body $body -ContentType "application/json"
Invoke-RestMethod "http://localhost:8000/proposals/$($p.id)/actions/labels/execute" -Method Post -Headers $h
```

With `EXECUTOR_DRY_RUN=true` the answer says `"dry_run": true` and the PR on GitHub does not
change.

## 8. Real run

Stop the app (Ctrl+C), set `EXECUTOR_DRY_RUN=false` in `.env`, start it again (step 4). Push a
new commit to the PR (a new head SHA is a new run) or open a new PR, and wait for its
proposal. Fetch it again (`$p = (Invoke-RestMethod http://localhost:8000/proposals -Headers
$h)[0]`; the dry-run proposal has used its approval), then approve and execute `labels` and
`comment` as above. The labels and the triage comment appear on the PR, posted by
`prpilot-dev-pratox1112[bot]`.

Then check that the lock holds against the live system:

```powershell
# the same action again: refused as "replayed", nothing written
Invoke-RestMethod "http://localhost:8000/proposals/$($p.id)/actions/labels/execute" -Method Post -Headers $h
```

## 9. Afterwards

Set `EXECUTOR_DRY_RUN=true` again. Stop the tunnel (the trycloudflare address dies with it).
