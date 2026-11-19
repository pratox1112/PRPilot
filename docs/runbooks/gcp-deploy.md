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
the code that runs.

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
