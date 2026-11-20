# Cloud SQL Postgres 17, smallest tier. No authorized networks: the services connect through
# Cloud Run's built-in Cloud SQL connection (a Unix socket under /cloudsql). The app user's
# password is generated here and stored only as the DATABASE_URL secret; it is also in the
# Terraform state, which lives in the private, versioned state bucket.

variable "region" {
  type = string
}

variable "activation_policy" {
  type = string
}

variable "url_readers" {
  description = "Service accounts that may read DATABASE_URL"
  type        = list(string)
}

resource "google_sql_database_instance" "pg" {
  name                = "prpilot-pg"
  database_version    = "POSTGRES_17"
  region              = var.region
  deletion_protection = false # `terraform destroy` must be able to remove everything

  settings {
    tier              = "db-f1-micro"
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    activation_policy = var.activation_policy

    ip_configuration {
      ipv4_enabled = true
    }

    backup_configuration {
      enabled = false
    }

    # db-f1-micro allows 25 by default, a few of them reserved; the services' pools add up to
    # more under load (modules/services, db_pool).
    database_flags {
      name  = "max_connections"
      value = "50"
    }
  }
}

resource "google_sql_database" "app" {
  name     = "prpilot"
  instance = google_sql_database_instance.pg.name
}

resource "random_password" "app" {
  length  = 32
  special = false # goes into a URL unescaped
}

resource "google_sql_user" "app" {
  name     = "prpilot"
  instance = google_sql_database_instance.pg.name
  password = random_password.app.result
}

resource "google_secret_manager_secret" "url" {
  secret_id = "prpilot-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "url" {
  secret = google_secret_manager_secret.url.id
  secret_data = format(
    "postgresql+asyncpg://%s:%s@/%s?host=/cloudsql/%s",
    google_sql_user.app.name, random_password.app.result, google_sql_database.app.name,
    google_sql_database_instance.pg.connection_name,
  )
}

resource "google_secret_manager_secret_iam_member" "read" {
  for_each  = toset(var.url_readers)
  secret_id = google_secret_manager_secret.url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

output "connection_name" {
  value = google_sql_database_instance.pg.connection_name
}

output "url_secret_id" {
  value = google_secret_manager_secret.url.secret_id
}
