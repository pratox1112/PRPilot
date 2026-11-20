# Non-secret settings of the dev deployment. The owner's email addresses are not committed:
# pass them as TF_VAR_owner_email and TF_VAR_alert_email (CI: the OWNER_EMAIL and ALERT_EMAIL
# repository secrets, masked in logs), and the image tag as -var image_tag=<built images' tag>.
project_id    = "prpilot-513002"
region        = "us-east1"
github_repo   = "pratox1112/PRPilot"
sandbox_repos = ["pratox1112/prpilot-sandbox"]
app_id        = "5488427" # prpilot-dev-pratox1112 (writes; executor only)
reader_app_id = "5824817" # prpilot-reader-pratox1112 (reads; worker only)
