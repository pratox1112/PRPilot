variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east1"
}

variable "image_tag" {
  description = "Tag of the app, worker and model images in Artifact Registry (the git commit)."
  type        = string
}

variable "github_repo" {
  description = "owner/name of this repository; CI's plan identity is limited to it."
  type        = string
}

variable "sandbox_repos" {
  description = "Repos the bot may triage and write to (everything else is refused)."
  type        = list(string)
}

variable "app_id" {
  description = "The writing GitHub App (its key is mounted in the executor only)."
  type        = string
}

variable "reader_app_id" {
  description = "The read-only GitHub App the worker reads PRs as."
  type        = string
}

variable "executor_dry_run" {
  description = "true: the executor records approved actions and writes nothing to GitHub."
  type        = bool
  default     = true
}

variable "owner_email" {
  description = "Google account allowed to call the executor (Cloud Run IAM)."
  type        = string
}

variable "alert_email" {
  description = "Where the dead-letter alert is sent."
  type        = string
}

variable "db_activation_policy" {
  description = "ALWAYS while the system is in use, NEVER to stop the database (and its cost)."
  type        = string
  default     = "ALWAYS"
}

variable "max_instances" {
  description = "Upper bound on worker and model instances (each handles one PR at a time)."
  type        = number
  default     = 3
}

variable "max_delivery_attempts" {
  description = "Pub/Sub pushes per event before the dead-letter topic: 5 normally; 20 for load runs, so an event can wait about 2.5 hours (backoff 10 s to 600 s) for a free worker instead of being dead-lettered."
  type        = number
  default     = 5
}

variable "model_min_instances" {
  description = "0 in dev; 1 during load tests, so the model has no cold start."
  type        = number
  default     = 0
}

variable "tracing" {
  description = "true: the worker sends a trace per PR to Langfuse (needs the two Langfuse key secrets)."
  type        = bool
  default     = false
}

variable "replay_bundle" {
  description = "Object in the replay bucket the worker serves historical PRs from (\"\" = off)."
  type        = string
  default     = ""
}

variable "langfuse_host" {
  type    = string
  default = "https://us.cloud.langfuse.com"
}
