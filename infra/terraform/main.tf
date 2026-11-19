# PRPilot on GCP (roadmap §9.1): webhook → api → Pub/Sub → worker (→ model) → Postgres;
# approved actions → executor → GitHub. One module per concern.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudbilling.googleapis.com", # the public price catalog (replay.measure), free
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

module "registry" {
  source     = "./modules/registry"
  region     = var.region
  depends_on = [google_project_service.apis]
}

module "iam" {
  source     = "./modules/iam"
  project_id = var.project_id
  depends_on = [google_project_service.apis]
}

module "secrets" {
  source = "./modules/secrets"
  accessors = {
    "github-app-key"        = [module.iam.emails["executor"]]
    "github-reader-app-key" = [module.iam.emails["worker"]]
    "webhook-secret"        = [module.iam.emails["api"]]
    "admin-token"           = [module.iam.emails["api"], module.iam.emails["executor"]]
    "langfuse-public-key"   = [module.iam.emails["worker"]]
    "langfuse-secret-key"   = [module.iam.emails["worker"]]
  }
  depends_on = [google_project_service.apis]
}

module "database" {
  source            = "./modules/database"
  region            = var.region
  activation_policy = var.db_activation_policy
  url_readers = [
    module.iam.emails["api"], module.iam.emails["worker"],
    module.iam.emails["executor"], module.iam.emails["migrate"],
  ]
  depends_on = [google_project_service.apis]
}

# Replay bundles (historical PRs as of their open time, replay.bundle) for the pilot and the
# shadow replay. The worker mounts the bucket read-only only while `replay_bundle` is set.
resource "google_storage_bucket" "replay" {
  name                        = "${var.project_id}-replay"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true # teardown to $0 removes the bundles with the bucket
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "worker_reads_replay" {
  bucket = google_storage_bucket.replay.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${module.iam.emails["worker"]}"
}

module "services" {
  source              = "./modules/services"
  project_id          = var.project_id
  region              = var.region
  images              = { for k in ["app", "worker", "model"] : k => "${module.registry.url}/${k}:${var.image_tag}" }
  service_accounts    = module.iam.emails
  db_connection       = module.database.connection_name
  database_url_secret = module.database.url_secret_id
  secret_ids          = module.secrets.ids
  pubsub_topic        = module.pubsub.topic_name
  sandbox_repos       = var.sandbox_repos
  app_id              = var.app_id
  reader_app_id       = var.reader_app_id
  executor_dry_run    = var.executor_dry_run
  owner_email         = var.owner_email
  max_instances       = var.max_instances
  model_min_instances = var.model_min_instances
  tracing             = var.tracing
  langfuse_host       = var.langfuse_host
  replay_bucket       = google_storage_bucket.replay.name
  replay_bundle       = var.replay_bundle
  depends_on          = [module.secrets, module.database, google_storage_bucket_iam_member.worker_reads_replay]
}

module "pubsub" {
  source         = "./modules/pubsub"
  project_id     = var.project_id
  publisher      = module.iam.emails["api"]
  push_account   = module.iam.emails["pubsub-push"]
  push_endpoint  = module.services.worker_url
  max_attempts   = 5
  ack_deadline_s = 600
  depends_on     = [google_project_service.apis]
}

module "monitoring" {
  source            = "./modules/monitoring"
  alert_email       = var.alert_email
  dlq_subscription  = module.pubsub.dlq_subscription_name
  push_subscription = module.pubsub.push_subscription_name
  depends_on        = [google_project_service.apis]
}

module "ci" {
  source           = "./modules/ci"
  project_id       = var.project_id
  github_repo      = var.github_repo
  state_bucket     = "${var.project_id}-tfstate"
  readable_secrets = [module.database.url_secret_id]
  depends_on       = [google_project_service.apis]
}
