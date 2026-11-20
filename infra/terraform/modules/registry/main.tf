# Artifact Registry: the app, worker and model images (private).

variable "region" {
  type = string
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "prpilot"
  location      = var.region
  format        = "DOCKER"
  description   = "PRPilot service images, tagged with the git commit they were built from"
}

output "url" {
  value = "${var.region}-docker.pkg.dev/${google_artifact_registry_repository.images.project}/${google_artifact_registry_repository.images.repository_id}"
}
