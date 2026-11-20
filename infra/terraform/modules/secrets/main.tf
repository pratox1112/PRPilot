# Secret Manager: Terraform creates the secrets and who may read them, never their values.
# The owner adds each value from their own machine (gcloud secrets versions add ...), so no
# key or token is in code or in Terraform state.

variable "accessors" {
  description = "secret id => service accounts that may read it"
  type        = map(list(string))
}

resource "google_secret_manager_secret" "s" {
  for_each  = var.accessors
  secret_id = "prpilot-${each.key}"
  replication {
    auto {}
  }
}

locals {
  grants = merge([
    for id, emails in var.accessors : { for e in emails : "${id}/${e}" => { id = id, email = e } }
  ]...)
}

resource "google_secret_manager_secret_iam_member" "read" {
  for_each  = local.grants
  secret_id = google_secret_manager_secret.s[each.value.id].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.email}"
}

output "ids" {
  value = { for k, s in google_secret_manager_secret.s : k => s.secret_id }
}
