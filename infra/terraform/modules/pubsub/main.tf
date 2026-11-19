# Pub/Sub between the webhook and the worker (roadmap §9.2): the api publishes and acks GitHub
# at once; a push subscription delivers each event to the worker with its own identity, retries
# with backoff, and after `max_attempts` moves it to the dead-letter topic. A pull subscription
# on the dead-letter topic keeps those events for inspection; the alert watches it.

variable "project_id" {
  type = string
}

variable "publisher" {
  type = string
}

variable "push_account" {
  type = string
}

variable "push_endpoint" {
  description = "The worker service's URL"
  type        = string
}

variable "max_attempts" {
  type = number
}

variable "ack_deadline_s" {
  type = number
}

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic" "events" {
  name = "prpilot-pr-events"
}

resource "google_pubsub_topic" "dead_letter" {
  name = "prpilot-pr-events-dead-letter"
}

resource "google_pubsub_subscription" "worker" {
  name                 = "prpilot-pr-events-worker"
  topic                = google_pubsub_topic.events.id
  ack_deadline_seconds = var.ack_deadline_s

  push_config {
    push_endpoint = "${var.push_endpoint}/pubsub"
    oidc_token {
      service_account_email = var.push_account
      audience              = var.push_endpoint
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.max_attempts
  }

  expiration_policy {
    ttl = "" # never expires
  }
}

resource "google_pubsub_subscription" "dead_letter" {
  name                       = "prpilot-pr-events-dead-letter-hold"
  topic                      = google_pubsub_topic.dead_letter.id
  message_retention_duration = "604800s" # 7 days

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "api_publishes" {
  topic  = google_pubsub_topic.events.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${var.publisher}"
}

# Pub/Sub's own service agent forwards to the dead-letter topic and acks the original.
resource "google_pubsub_topic_iam_member" "agent_publishes_dead_letters" {
  topic  = google_pubsub_topic.dead_letter.id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "agent_acks_dead_letters" {
  subscription = google_pubsub_subscription.worker.id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

# ... and mints the push identity's OIDC tokens.
resource "google_service_account_iam_member" "agent_signs_push_tokens" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.push_account}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}

output "topic_name" {
  value = google_pubsub_topic.events.name
}

output "push_subscription_name" {
  value = google_pubsub_subscription.worker.name
}

output "dlq_subscription_name" {
  value = google_pubsub_subscription.dead_letter.name
}
