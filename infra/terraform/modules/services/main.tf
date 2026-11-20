# The four Cloud Run services and the migrations job.
#
#   api       public (GitHub must reach the webhook); the approval API checks the admin token
#   executor  only the owner's Google account may call it (Cloud Run IAM), plus the admin token
#   worker    internal ingress; only the Pub/Sub push identity may call it
#   model     only the worker's identity may call it
#
# Cloud Run returns a Cloud SQL volume (and its mount) after the others, so it is declared
# last; otherwise every plan shows a reordering that changes nothing.
#
# Worker and model serve one PR at a time per instance (concurrency 1), so a PR never waits
# behind another inside an instance and the model gets its whole context.

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "images" {
  type = map(string)
}

variable "service_accounts" {
  type = map(string)
}

variable "db_connection" {
  type = string
}

variable "database_url_secret" {
  type = string
}

variable "secret_ids" {
  type = map(string)
}

variable "pubsub_topic" {
  type = string
}

variable "sandbox_repos" {
  type = list(string)
}

variable "app_id" {
  type = string
}

variable "reader_app_id" {
  type = string
}

variable "executor_dry_run" {
  type = bool
}

variable "owner_email" {
  type = string
}

variable "max_instances" {
  type = number
}

variable "model_min_instances" {
  type = number
}

variable "tracing" {
  type = bool
}

variable "langfuse_host" {
  type = string
}

variable "replay_bucket" {
  type = string
}

variable "replay_bundle" {
  type = string
}

locals {
  sandbox_repos = join(",", var.sandbox_repos)
  # Database connections per instance. All instances together (worker 10 x 2, api 2 x 4,
  # executor 2, plus the migrate job) stay under the database's max_connections (50).
  db_pool = {
    worker   = { DB_POOL_SIZE = "1", DB_MAX_OVERFLOW = "1" } # one PR at a time
    api      = { DB_POOL_SIZE = "4", DB_MAX_OVERFLOW = "0" }
    executor = { DB_POOL_SIZE = "2", DB_MAX_OVERFLOW = "0" }
  }
}

# ---------------------------------------------------------------------------------- model

resource "google_cloud_run_v2_service" "model" {
  name                = "prpilot-model"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = var.service_accounts["model"]
    max_instance_request_concurrency = 1
    timeout                          = "600s"

    scaling {
      min_instance_count = var.model_min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.images["model"]
      ports {
        container_port = 8080
      }
      resources {
        limits            = { cpu = "4", memory = "16Gi" } # 8Gi ran out on long prompts
        cpu_idle          = true
        startup_cpu_boost = true
      }
      env {
        name  = "LLAMA_ARG_THREADS"
        value = "4"
      }
      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 10
        failure_threshold = 24
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "worker_calls_model" {
  name     = google_cloud_run_v2_service.model.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_accounts["worker"]}"
}

# --------------------------------------------------------------------------------- worker

resource "google_cloud_run_v2_service" "worker" {
  name                = "prpilot-worker"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY" # Pub/Sub push counts as internal

  template {
    service_account                  = var.service_accounts["worker"]
    max_instance_request_concurrency = 1
    timeout                          = "600s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    volumes {
      name = "reader-key"
      secret {
        secret = var.secret_ids["github-reader-app-key"]
        items {
          version = "latest"
          path    = "key.pem"
        }
      }
    }
    dynamic "volumes" {
      for_each = var.replay_bundle != "" ? [var.replay_bucket] : []
      content {
        name = "replay"
        gcs {
          bucket    = volumes.value
          read_only = true
        }
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.db_connection]
      }
    }

    containers {
      image = var.images["worker"]
      ports {
        container_port = 8080
      }
      resources {
        limits            = { cpu = "2", memory = "8Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      volume_mounts {
        name       = "reader-key"
        mount_path = "/secrets/reader"
      }
      dynamic "volume_mounts" {
        for_each = var.replay_bundle != "" ? ["/replay"] : []
        content {
          name       = "replay"
          mount_path = volume_mounts.value
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.database_url_secret
            version = "latest"
          }
        }
      }
      env {
        name  = "SANDBOX_REPOS"
        value = local.sandbox_repos
      }
      dynamic "env" {
        for_each = local.db_pool["worker"]
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "GITHUB_READER_APP_ID"
        value = var.reader_app_id
      }
      env {
        name  = "GITHUB_READER_APP_PRIVATE_KEY_PATH"
        value = "/secrets/reader/key.pem"
      }
      env {
        name  = "MODEL_BASE_URL"
        value = "${google_cloud_run_v2_service.model.uri}/v1"
      }
      env {
        name  = "MODEL_AUDIENCE"
        value = google_cloud_run_v2_service.model.uri
      }
      env {
        name  = "TRACING"
        value = tostring(var.tracing)
      }
      # The Langfuse keys are referenced only with tracing on, so a deploy without them works.
      dynamic "env" {
        for_each = var.tracing ? { LANGFUSE_PUBLIC_KEY = "langfuse-public-key", LANGFUSE_SECRET_KEY = "langfuse-secret-key" } : {}
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = var.secret_ids[env.value]
              version = "latest"
            }
          }
        }
      }
      dynamic "env" {
        for_each = var.tracing ? [var.langfuse_host] : []
        content {
          name  = "LANGFUSE_HOST"
          value = env.value
        }
      }
      # Pilot / shadow replay: the PRs in this bundle are served from it, not from GitHub.
      dynamic "env" {
        for_each = var.replay_bundle != "" ? ["/replay/${var.replay_bundle}"] : []
        content {
          name  = "REPLAY_BUNDLE"
          value = env.value
        }
      }
      # The screening models load before the port opens (~70 s on CPU).
      startup_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds    = 10
        failure_threshold = 24
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_pushes_to_worker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_accounts["pubsub-push"]}"
}

# ------------------------------------------------------------------------------------ api

resource "google_cloud_run_v2_service" "api" {
  name                = "prpilot-api"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.service_accounts["api"]
    timeout         = "30s"

    # Fixed, not max_instances: the api answers in milliseconds, and its connections count
    # against the database's limit (see db_pool).
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.db_connection]
      }
    }

    containers {
      image   = var.images["app"]
      command = ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
      ports {
        container_port = 8080
      }
      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.database_url_secret
            version = "latest"
          }
        }
      }
      env {
        name = "GITHUB_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["webhook-secret"]
            version = "latest"
          }
        }
      }
      env {
        name = "ADMIN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["admin-token"]
            version = "latest"
          }
        }
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "PUBSUB_TOPIC"
        value = var.pubsub_topic
      }
      env {
        name  = "SANDBOX_REPOS"
        value = local.sandbox_repos
      }
      dynamic "env" {
        for_each = local.db_pool["api"]
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "github_calls_api" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers" # GitHub's webhook; every route checks a signature or the admin token
}

# ------------------------------------------------------------------------------- executor

resource "google_cloud_run_v2_service" "executor" {
  name                = "prpilot-executor"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = var.service_accounts["executor"]
    max_instance_request_concurrency = 10
    timeout                          = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    volumes {
      name = "app-key"
      secret {
        secret = var.secret_ids["github-app-key"]
        items {
          version = "latest"
          path    = "key.pem"
        }
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.db_connection]
      }
    }

    containers {
      image   = var.images["app"]
      command = ["uvicorn", "services.executor.main:app", "--host", "0.0.0.0", "--port", "8080"]
      ports {
        container_port = 8080
      }
      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }
      volume_mounts {
        name       = "app-key"
        mount_path = "/secrets/app"
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.database_url_secret
            version = "latest"
          }
        }
      }
      env {
        name = "ADMIN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["admin-token"]
            version = "latest"
          }
        }
      }
      env {
        name  = "SANDBOX_REPOS"
        value = local.sandbox_repos
      }
      dynamic "env" {
        for_each = local.db_pool["executor"]
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name  = "EXECUTOR_DRY_RUN"
        value = tostring(var.executor_dry_run)
      }
      env {
        name  = "GITHUB_APP_ID"
        value = var.app_id
      }
      env {
        name  = "GITHUB_APP_PRIVATE_KEY_PATH"
        value = "/secrets/app/key.pem"
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "owner_calls_executor" {
  name     = google_cloud_run_v2_service.executor.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "user:${var.owner_email}"
}

# ------------------------------------------------------------------------- migrations job

resource "google_cloud_run_v2_job" "migrate" {
  name                = "prpilot-migrate"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = var.service_accounts["migrate"]
      max_retries     = 0

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.db_connection]
        }
      }

      containers {
        image   = var.images["app"]
        command = ["alembic", "upgrade", "head"]
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.database_url_secret
              version = "latest"
            }
          }
        }
      }
    }
  }
}

output "worker_url" {
  value = google_cloud_run_v2_service.worker.uri
}

output "urls" {
  value = {
    api      = google_cloud_run_v2_service.api.uri
    executor = google_cloud_run_v2_service.executor.uri
    worker   = google_cloud_run_v2_service.worker.uri
    model    = google_cloud_run_v2_service.model.uri
  }
}
