output "registry" {
  value = module.registry.url
}

output "urls" {
  value = module.services.urls
}

output "webhook_url" {
  description = "Set this as the writing GitHub App's webhook URL."
  value       = "${module.services.urls["api"]}/webhook"
}

output "replay_bucket" {
  value = google_storage_bucket.replay.name
}

output "ci_workload_identity_provider" {
  description = "GitHub repository variable GCP_WIF_PROVIDER"
  value       = module.ci.workload_identity_provider
}

output "ci_plan_service_account" {
  description = "GitHub repository variable GCP_PLAN_SERVICE_ACCOUNT"
  value       = module.ci.plan_service_account
}
