# Deploy to GCP, pause, tear down

Everything runs in PowerShell from `C:\PRPilot`, as the project owner. Nothing here is run by
CI: CI only plans. Costs start at step 4 (Cloud SQL, about $10 a month while it runs); see
"Pause" and "Teardown" at the end.

## 0. Session variables (every new terminal)

```powershell
$proj = "prpilot-513002"; $region = "us-east1"
$reg = "$region-docker.pkg.dev/$proj/prpilot"
$tag = git rev-parse --short=12 HEAD
$env:TF_VAR_owner_email = "<your Google account email>"
$env:TF_VAR_alert_email = "<where the dead-letter alert goes>"
$tf = "-var-file=dev.tfvars", "-var", "image_tag=$tag"
function EnvValue($k) { ((Get-Content .env | Select-String "^$k=") -replace "^$k=", "").Trim() }
function Add-Secret($name, $value) { $f = New-TemporaryFile; [IO.File]::WriteAllText($f, $value); gcloud secrets versions add "prpilot-$name" --project=$proj --data-file=$f; Remove-Item $f }
```

Build and deploy only from a clean, committed tree (`git status` empty), so the image tag is
the code that runs. `$tag` must name images that exist in the registry: after a commit that
changes only Terraform or docs, keep the tag of the last images built (for example
`$tag = "32de816f0080"`) instead of the new commit.

Cloud Run's front end reserves `/healthz` (it answers 404 without reaching the app). The startup
probes call the container directly and are not affected; check a deployed service another way,
for example `Invoke-RestMethod "$($api.api)/runs" -Headers $h`.

## 1. State bucket (once)

```powershell
gcloud storage buckets create gs://$proj-tfstate --project=$proj --location=$region --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://$proj-tfstate --versioning
terraform -chdir=infra/terraform init -backend-config="bucket=$proj-tfstate"
```

## 2. Foundations: APIs, image registry, service accounts, secrets (no values yet)

```powershell
terraform -chdir=infra/terraform apply @tf -target="google_project_service.apis" -target="module.registry" -target="module.iam" -target="module.secrets"
```

Then add the secret values from this machine. Text values go through a temporary file without
a trailing newline, deleted at once.

```powershell
gcloud secrets versions add prpilot-github-app-key --project=$proj --data-file="$HOME\.prpilot\github-app.pem"
gcloud secrets versions add prpilot-github-reader-app-key --project=$proj --data-file="$HOME\.prpilot\github-reader-app.pem"
Add-Secret "webhook-secret" (EnvValue "GITHUB_WEBHOOK_SECRET")
Add-Secret "admin-token" (EnvValue "ADMIN_TOKEN")
```

## 3. Images

```powershell
gcloud auth configure-docker "$region-docker.pkg.dev" --quiet
docker build -f docker/app.Dockerfile -t "$reg/app:$tag" .
$hf = New-TemporaryFile; [IO.File]::WriteAllText($hf, (EnvValue "HF_TOKEN"))
docker build -f docker/worker.Dockerfile --secret "id=hf_token,src=$hf" -t "$reg/worker:$tag" .; Remove-Item $hf
docker build -f docker/model.Dockerfile -t "$reg/model:$tag" models
docker push "$reg/app:$tag"; docker push "$reg/worker:$tag"; docker push "$reg/model:$tag"
```

## 4. Everything else

```powershell
terraform -chdir=infra/terraform apply @tf
terraform -chdir=infra/terraform output
```

Cloud SQL takes 5–15 minutes to create. Then run the migrations:

```powershell
gcloud run jobs execute prpilot-migrate --project=$proj --region=$region --wait
```

## 5. Webhook

Set the writing App's webhook URL (`prpilot-dev-pratox1112` → General → Webhook URL) to the
`webhook_url` output. Save.

## 6. CI plan identity (once)

```powershell
gh variable set GCP_WIF_PROVIDER --body (terraform -chdir=infra/terraform output -raw ci_workload_identity_provider)
gh variable set GCP_PLAN_SERVICE_ACCOUNT --body (terraform -chdir=infra/terraform output -raw ci_plan_service_account)
gh variable set IMAGE_TAG --body $tag
gh secret set OWNER_EMAIL --body $env:TF_VAR_owner_email
gh secret set ALERT_EMAIL --body $env:TF_VAR_alert_email
```

## Calling the deployed API

```powershell
$api = terraform -chdir=infra/terraform output -json urls | ConvertFrom-Json
$h = @{ Authorization = "Bearer $(EnvValue 'ADMIN_TOKEN')" }
Invoke-RestMethod "$($api.api)/runs" -Headers $h | Format-List
# The executor also needs your Google identity (Cloud Run IAM), in the header Cloud Run reads first:
$hx = @{ Authorization = $h.Authorization; "X-Serverless-Authorization" = "Bearer $(gcloud auth print-identity-token)" }
Invoke-RestMethod "$($api.executor)/proposals/<id>/actions/labels/execute" -Method Post -Headers $hx
```

Writes stay off until `-var executor_dry_run=false` is applied.

## Tracing to Langfuse (optional)

One trace per PR (`pr_run`, with spans `context`, `screen`, `triage` and the model call inside
it, `proposal`). Add the two key values once, then apply with tracing on:

```powershell
Add-Secret "langfuse-public-key" (EnvValue "LANGFUSE_PUBLIC_KEY")
Add-Secret "langfuse-secret-key" (EnvValue "LANGFUSE_SECRET_KEY")
terraform -chdir=infra/terraform apply @tf -var tracing=true
```

Every later apply needs `-var tracing=true` too, or tracing turns off again.

## Replaying historical PRs (pilot, burst test, shadow replay)

The worker serves the PRs of a replay bundle (open-time data only) from the replay bucket
instead of GitHub; every other PR is read live as before, and nothing is written anywhere.
Build the bundle locally, upload it, and point the worker at it:

```powershell
uv run --group data python -m replay.bundle --split val --keys data/processed/prompts_val_s500.jsonl --every 10 --out data/processed/replay/pilot_val50.jsonl
gcloud storage cp data/processed/replay/pilot_val50.jsonl "gs://$proj-replay/"
terraform -chdir=infra/terraform apply @tf -var replay_bundle=pilot_val50.jsonl
```

Send it from a clean tree (signed webhooks, at most `--in-flight` PRs open at once), then,
10 minutes after the last run finished, measure it. The token reads Cloud Monitoring, the
service sizes and the public price catalog:

```powershell
uv run python -m replay.send --bundle data/processed/replay/pilot_val50.jsonl --api $api.api --label pilot --in-flight 3
$env:GCP_ACCESS_TOKEN = gcloud auth print-access-token
uv run python -m replay.measure report --label pilot --server-times $api.api
```

For a burst test (all events at once, 10% redelivered), raise capacity for the run with
`-var max_instances=10 -var model_min_instances=1 -var max_delivery_attempts=20`, so events
wait in Pub/Sub for a free worker instead of being dead-lettered, then:

```powershell
uv run python -m loadtest.burst --bundle data/processed/replay/burst_val500.jsonl --api $api.api --label burst
uv run python -m replay.measure report --label burst --server-times $api.api
```

Apply again without those three variables after the run (back to 3 instances, model at zero,
5 attempts). If events were still being retried or dead-lettered after the last run finished
(an overloaded burst), end the usage window when the burst stopped polling, so the report
counts every dead-lettered event and the capacity that stayed up:
`--until (Get-Item data\processed\replay\burst.sends.jsonl).LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")`.

For the shadow replay (the test split's 500-PR sample, scored once against maintainer truth),
first check, free and locally, that the worker will build byte-identical prompts to the
offline pack; then send it like the pilot, turn the runs into replies, and score them with
the eval harness next to the offline run of the same GGUF file on the same PRs:

```powershell
uv run --group data python -m replay.bundle --split test --keys data/processed/prompts_test_s500.jsonl --out data/processed/replay/shadow_test500.jsonl
uv run python -m replay.shadow prompts --bundle data/processed/replay/shadow_test500.jsonl --pack data/processed/prompts_test.jsonl
gcloud storage cp data/processed/replay/shadow_test500.jsonl "gs://$proj-replay/"
terraform -chdir=infra/terraform apply @tf -var replay_bundle=shadow_test500.jsonl
uv run python -m replay.send --bundle data/processed/replay/shadow_test500.jsonl --api $api.api --label shadow --in-flight 3 --timeout 3600
uv run python -m replay.shadow replies --label shadow --api $api.api --pack data/processed/prompts_test.jsonl
uv run python -m replay.measure report --label shadow --server-times $api.api
$score = "--project", "eval", "python", "-m", "prpilot_eval", "score", "--pack", "data/processed/prompts_test.jsonl", "--targets", "data/processed/targets_test.jsonl", "--only", "data/processed/prompts_test_s500.jsonl"
uv run @score --replies data/processed/replay/shadow-model.jsonl --out results/shadow_replay_model.json
uv run @score --replies data/processed/replay/shadow-proposal.jsonl --out results/shadow_replay_proposal.json
uv run @score --replies data/processed/replies/student-gguf-system_test_11914958.jsonl --out results/student-gguf-system_test_s500.json
uv run --project eval python -m prpilot_eval compare --a results/shadow_replay_model.records.jsonl --b results/student-gguf-system_test_s500.records.jsonl
```

Every later apply needs `-var replay_bundle=...` too while the replay is in use. Without it the
worker stops mounting the bucket (the bundles stay there until teardown).

## Pause (between sessions, about $2 a month)

```powershell
terraform -chdir=infra/terraform apply @tf -var db_activation_policy=NEVER
```

Resume with `-var db_activation_policy=ALWAYS`. The services scale to zero on their own.

## Teardown to $0 (roadmap Phase 9)

```powershell
terraform -chdir=infra/terraform destroy @tf
gcloud storage rm -r gs://$proj-tfstate
gcloud run services list --project=$proj; gcloud run jobs list --project=$proj --region=$region
gcloud sql instances list --project=$proj; gcloud artifacts repositories list --project=$proj
gcloud secrets list --project=$proj; gcloud pubsub topics list --project=$proj; gcloud storage buckets list --project=$proj
```

Every listing must be empty. Then, only when no further GCP work is needed:
`gcloud billing projects unlink $proj`, and turn the writing App's webhook off (General →
Active).
