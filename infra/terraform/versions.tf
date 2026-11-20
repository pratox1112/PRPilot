terraform {
  required_version = "= 1.16.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 8.10.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.9.1"
    }
  }

  # State in a versioned bucket created once outside Terraform (docs/runbooks/gcp-deploy.md):
  #   terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "prpilot"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
