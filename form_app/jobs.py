from datetime import datetime, timedelta, date
from .models import (
    get_published_template, clients_db, timesheet_records_db, compliance_requests_db, email_logs_db,
    ComplianceRequest, ComplianceRequestStatus, EmailLog, EmailStatus,
    FormTemplateStatus, Client, TimesheetRecord, User, find_user_by_id # Added EmailLog, EmailStatus, email_logs_db, find_user_by_id
)

def run_compliance_form_trigger_job():
    """
    Core logic for the Compliance Form Trigger Job.
    Identifies timesheet records requiring compliance forms and creates ComplianceRequest records.
    """
    report = {
        "job_name": "Compliance Form Trigger",
        "run_time": datetime.utcnow().isoformat(),
        "status": "started",
        "published_template_found": False,
        "published_template_id": None,
        "issuer_audit_clients_count": 0,
        "eligible_timesheets_count": 0,
        "new_requests_created_count": 0,
        "simulated_emails_sent_count": 0, # New report field
        "requests_created_details": [],
        "warnings": [],
        "errors": []
    }

    # 1. Fetch Published Template
    published_template = get_published_template()
    if not published_template:
        report["status"] = "failed"
        report["errors"].append("No published form template found. Job cannot run.")
        return report

    report["published_template_found"] = True
    report["published_template_id"] = published_template.id

    # 2. Identify Issuer Audit Clients
    issuer_audit_clients = [client for client in clients_db if client.is_issuer_audit_client]
    report["issuer_audit_clients_count"] = len(issuer_audit_clients)
    if not issuer_audit_clients:
        report["status"] = "completed_no_action"
        report["warnings"].append("No issuer audit clients found. No compliance requests will be generated.")
        return report

    issuer_audit_client_ids = {client.id for client in issuer_audit_clients}

    # 3. Filter Timesheet Records
    ninety_days_ago = date.today() - timedelta(days=90)
    eligible_timesheets = []
    for record in timesheet_records_db:
        if record.status == 30 and \
           record.date_posted >= ninety_days_ago and \
           record.client_id in issuer_audit_client_ids:
            eligible_timesheets.append(record)

    report["eligible_timesheets_count"] = len(eligible_timesheets)
    if not eligible_timesheets:
        report["status"] = "completed_no_action"
        report["warnings"].append("No eligible timesheet records found matching the criteria.")
        return report

    # 4. Determine Employees and Create Requests
    # Group eligible timesheets by (employee_id, client_id, project_id)
    # This helps in creating one compliance request per unique combination,
    # and linking all relevant timesheet IDs to it.

    potential_requests_data = {} # Key: (emp_id, client_id, project_id), Value: list of timesheet_record_ids

    for ts_record in eligible_timesheets:
        key = (ts_record.employee_id, ts_record.client_id, ts_record.project_id)
        if key not in potential_requests_data:
            potential_requests_data[key] = []
        potential_requests_data[key].append(ts_record.id)

    for (emp_id, client_id, project_id), ts_ids in potential_requests_data.items():
        # Prevent Duplicates: Check for existing PENDING or COMPLETED requests for this combination
        # for the current published template.
        # A request is considered a duplicate if it's for the same employee, client, project,
        # and importantly, the same form_template_id, and its status is PENDING or COMPLETED.
        # "CLOSED" requests do not prevent new ones. "CANCELLED" requests allow new ones.

        existing_relevant_request = False
        for req in compliance_requests_db:
            if req.employee_id == emp_id and \
               req.client_id == client_id and \
               req.project_id == project_id and \
               req.form_template_id == published_template.id and \
               req.status in [ComplianceRequestStatus.PENDING, ComplianceRequestStatus.COMPLETED]:
                existing_relevant_request = True
                report["warnings"].append(
                    f"Skipping request for EmpID {emp_id}, ClientID {client_id}, ProjID {project_id} "
                    f"as a PENDING/COMPLETED request (ID: {req.id}) already exists for template {published_template.id}."
                )
                break

        if not existing_relevant_request:
            new_request = ComplianceRequest(
                employee_id=emp_id,
                form_template_id=published_template.id,
                client_id=client_id,
                project_id=project_id,
                timesheet_record_ids=ts_ids, # Link all relevant timesheet entries
                status=ComplianceRequestStatus.PENDING
            )
            compliance_requests_db.append(new_request)
            report["new_requests_created_count"] += 1

            # --- Email Logging ---
            employee = find_user_by_id(emp_id)
            if employee and employee.email:
                email_subject = f"Action Required: New Compliance Form - {published_template.name}"
                email_body = (
                    f"Dear {employee.username},\n\n"
                    f"A new compliance form, '{published_template.name}', has been assigned to you "
                    f"due to your recent timesheet entries for client ID {client_id} on project ID {project_id}.\n"
                    f"Please complete this form by the due date (to be specified).\n"
                    f"Compliance Request ID: {new_request.id}\n"
                    f"You can access the form at [link_to_form_placeholder].\n\n"
                    f"Thank you."
                )

                email_log_entry = EmailLog(
                    recipient_email=employee.email,
                    employee_id=emp_id,
                    subject=email_subject,
                    body=email_body,
                    status=EmailStatus.SENT_SIMULATED,
                    compliance_request_id=new_request.id
                )
                email_logs_db.append(email_log_entry)
                report["simulated_emails_sent_count"] += 1
            else:
                report["warnings"].append(f"Could not log email for EmpID {emp_id}: User or email not found.")
            # --- End Email Logging ---

            report["requests_created_details"].append({
                "request_id": new_request.id,
                "employee_id": emp_id,
                "client_id": client_id,
                "project_id": project_id,
                "form_template_id": new_request.form_template_id,
                "linked_timesheet_ids": ts_ids
            })

    if report["new_requests_created_count"] > 0:
        report["status"] = "completed_with_action"
    else:
        report["status"] = "completed_no_new_action" # No new requests, but job ran.
        if not report["warnings"] and not report["errors"]: # if no warnings, it means everything was fine but no action needed
             report["warnings"].append("No new compliance requests were needed based on current data and existing requests.")


    return report
