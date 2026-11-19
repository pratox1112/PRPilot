# One service account per service, each with only what it needs. Narrow grants (a secret, a
# topic, a service) are made next to the resource they apply to; only Cloud SQL access is
# project-wide, because the Cloud SQL client role has no per-instance form.

variable "project_id" {
  type = string
}

locals {
  accounts = {
    api           = "PRPilot api: webhook and approval API"
    worker        = "PRPilot worker: screens and triages PRs (read-only GitHub App)"
    executor      = "PRPilot executor: writes approved actions (writing GitHub App)"
    model         = "PRPilot model: llama.cpp server, no access to anything"
    migrate       = "PRPilot migrations job"
    "pubsub-push" = "Identity Pub/Sub uses to push events to the worker"
  }
  sql_clients = ["api", "worker", "executor", "migrate"]
}

resource "google_service_account" "sa" {
  for_each     = local.accounts
  account_id   = "prpilot-${each.key}"
  display_name = each.value
}

resource "google_project_iam_member" "sql_client" {
  for_each = toset(local.sql_clients)
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.sa[each.key].email}"
}

output "emails" {
  value = { for k, sa in google_service_account.sa : k => sa.email }
}
