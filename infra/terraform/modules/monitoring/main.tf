# Cloud Monitoring (roadmap §9.5): one dashboard (latency, errors, Pub/Sub backlog, dead
# letters, instances) and one alert: any event in the dead-letter subscription.

variable "alert_email" {
  type = string
}

variable "dlq_subscription" {
  type = string
}

variable "push_subscription" {
  type = string
}

locals {
  sub_filter = "resource.type=\"pubsub_subscription\" AND metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\""

  chart = {
    latency = {
      title  = "Request latency p95 by service (ms)"
      filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\""
      agg = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_DELTA",
      crossSeriesReducer = "REDUCE_PERCENTILE_95", groupByFields = ["resource.label.service_name"] }
    }
    requests = {
      title  = "Requests per second by service and status class"
      filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\""
      agg = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_RATE", crossSeriesReducer = "REDUCE_SUM",
      groupByFields = ["resource.label.service_name", "metric.label.response_code_class"] }
    }
    backlog = {
      title  = "Pub/Sub backlog (undelivered PR events)"
      filter = "${local.sub_filter} AND resource.label.subscription_id=\"${var.push_subscription}\""
      agg    = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_MAX" }
    }
    dead_letters = {
      title  = "Dead-lettered PR events"
      filter = "${local.sub_filter} AND resource.label.subscription_id=\"${var.dlq_subscription}\""
      agg    = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_MAX" }
    }
    instances = {
      title  = "Instances by service"
      filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/container/instance_count\""
      agg = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_MAX", crossSeriesReducer = "REDUCE_SUM",
      groupByFields = ["resource.label.service_name"] }
    }
  }
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "PRPilot owner"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "dead_letters" {
  display_name = "PRPilot: PR events in the dead-letter queue"
  combiner     = "OR"

  conditions {
    display_name = "dead-letter subscription is not empty"
    condition_threshold {
      filter          = local.chart.dead_letters.filter
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content = "A PR event failed every delivery attempt. Inspect it: gcloud pubsub subscriptions pull ${var.dlq_subscription} --limit=5 (add --auto-ack to remove it once handled)."
  }
}

resource "google_monitoring_dashboard" "main" {
  dashboard_json = jsonencode({
    displayName = "PRPilot"
    gridLayout = {
      columns = "2" # Cloud Monitoring returns int64 fields as strings
      widgets = [
        for c in ["latency", "requests", "backlog", "dead_letters", "instances"] : {
          title = local.chart[c].title
          xyChart = {
            dataSets = [{
              plotType   = "LINE"
              targetAxis = "Y1" # the default Cloud Monitoring fills in
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter      = local.chart[c].filter
                  aggregation = local.chart[c].agg
                }
              }
            }]
          }
        }
      ]
    }
  })
}
