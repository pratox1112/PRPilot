# GitHub Actions signs in to GCP without a stored key (Workload Identity Federation) to run
# `terraform plan` on pull requests. Only workflows of this one repository can use the pool,
# and the identity can read, never change: project viewer and IAM policy reader, the state
# bucket's objects (plan runs with -lock=false, so it writes nothing), and the one secret
# Terraform itself manages (plan refreshes its version).

variable "project_id" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "state_bucket" {
  type = string
}

variable "readable_secrets" {
  type = list(string)
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "prpilot-github"
  display_name              = "PRPilot GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${var.github_repo}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "plan" {
  account_id   = "prpilot-ci-plan"
  display_name = "PRPilot CI: terraform plan (read-only)"
}

resource "google_service_account_iam_member" "github_uses_plan" {
  service_account_id = google_service_account.plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "plan_reads" {
  for_each = toset(["roles/viewer", "roles/iam.securityReviewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.plan.email}"
}

resource "google_storage_bucket_iam_member" "plan_reads_state" {
  bucket = var.state_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.plan.email}"
}

resource "google_secret_manager_secret_iam_member" "plan_reads_managed_secret" {
  for_each  = toset(var.readable_secrets)
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.plan.email}"
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "plan_service_account" {
  value = google_service_account.plan.email
}
