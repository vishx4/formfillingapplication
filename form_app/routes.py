from flask import Blueprint, render_template, request, session, redirect, url_for, abort, flash, Response
from datetime import datetime
import io
import csv
from form_app.models import (
    User, Role, users, FormTemplate, FormTemplateStatus, QuestionType, Client,
    form_templates_db, questions_db, compliance_requests_db, email_logs_db, clients_db,
    form_answers_db, FormAnswer, EmailLog, EmailStatus,
    find_user_by_id, find_form_template_by_id, get_active_draft_template, get_published_template,
    ComplianceRequestStatus, find_client_by_id
)
from form_app.jobs import run_compliance_form_trigger_job
from functools import wraps

main_bp = Blueprint('main', __name__, template_folder='templates', static_folder='static')

# Decorator for role-based access control
def roles_required(*role_names):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_role' not in session:
                return redirect(url_for('main.login', next=request.url))
            # Convert stored role string to Role enum member for comparison
            user_role_enum = Role(session['user_role'])
            # Convert role_names (strings) to Role enum members
            required_roles_enum = [Role[role_name.upper()] for role_name in role_names]

            if user_role_enum not in required_roles_enum:
                abort(403)  # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@main_bp.route('/')
def index():
    user_id = session.get('user_id')
    current_user_obj = None
    if user_id:
        temp_user = find_user_by_id(user_id) # Use the helper
        if temp_user:
            role_value = session.get('user_role')
            user_role_enum = Role(role_value) if role_value else temp_user.role
            current_user_obj = User(id=temp_user.id, username=temp_user.username, role=user_role_enum)
    return render_template('index.html', current_user=current_user_obj)


@main_bp.route('/logout') # Basic logout
def logout():
    session.pop('user_id', None)
    session.pop('user_role', None)
    return redirect(url_for('main.login'))

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        # This is a placeholder for actual authentication
        # For now, let's find a user by username and "log them in"
        user_to_login = next((u for u in users if u.username == username), None)
        if user_to_login:
            session['user_id'] = user_to_login.id
            session['user_role'] = user_to_login.role.value # Store role value (string)
            # Redirect to 'next' URL if it exists, otherwise to index
            next_url = request.args.get('next')
            return redirect(next_url or url_for('main.index'))
        else:
            return "User not found", 404
    # Simple form for testing, ideally use a template
    return """
    <form method="post">
        Username: <input type="text" name="username"><br>
        (Try: admin_user, super_admin_user, employee_user, another_admin)<br>
        <input type="submit" value="Login">
    </form>
    """

# Admin routes for FormTemplate
@main_bp.route('/admin/templates', methods=['GET'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def list_templates():
    # Pass the actual form_templates_db to the template
    return render_template('admin/list_templates.html', templates=form_templates_db, FormTemplateStatus=FormTemplateStatus)

@main_bp.route('/admin/templates/new', methods=['GET', 'POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def create_template():
    existing_draft = get_active_draft_template()
    warning_message = None
    draft_version = None

    if existing_draft:
        warning_message = (f"Warning: Creating a new template will override the existing draft "
                           f"'{existing_draft.name}' (Version: {existing_draft.version}). "
                           f"The existing draft will be archived.")
        draft_version = existing_draft.version

    if request.method == 'POST':
        if existing_draft:
            # Archive the existing draft
            existing_draft.status = FormTemplateStatus.ARCHIVED
            existing_draft.updated_at = datetime.utcnow() # Update timestamp
            flash(f"Existing draft template '{existing_draft.name}' (v{existing_draft.version}) has been archived.", "info")

        # Proceed to create the new template
        name = request.form.get('name')
        static_content = request.form.get('static_content')
        appendix_content = request.form.get('appendix_content')
        user_id = session.get('user_id')

        if not name or not user_id:
            flash("Name is required to create a template.", "error")
            # Re-render form with existing draft info if any
            return render_template('admin/create_template.html',
                                   warning_message=warning_message,
                                   draft_version=draft_version,
                                   form_data=request.form)


        creator = find_user_by_id(user_id)
        if not creator:
            abort(400, "User not found, cannot create template.") # Should not happen

        new_template = FormTemplate(
            name=name,
            static_content=static_content,
            appendix_content=appendix_content,
            created_by_id=creator.id,
            status=FormTemplateStatus.DRAFT, # New template is DRAFT
            version=1 # New draft starts at version 1
        )
        form_templates_db.append(new_template)
        flash(f"New draft template '{new_template.name}' (v{new_template.version}) created successfully.", "success")

        # Redirect to edit page of the new draft
        return redirect(url_for('main.edit_template', template_id=new_template.id))

    # GET request
    return render_template('admin/create_template.html',
                           warning_message=warning_message,
                           draft_version=draft_version)

@main_bp.route('/admin/templates/<int:source_template_id>/clone', methods=['POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def clone_template(source_template_id):
    # 1. Check for and archive existing draft
    existing_draft = get_active_draft_template()
    if existing_draft:
        existing_draft.status = FormTemplateStatus.ARCHIVED
        existing_draft.updated_at = datetime.utcnow()
        flash(f"Existing draft template '{existing_draft.name}' (v{existing_draft.version}) has been archived to make way for the clone.", "info")

    # 2. Fetch Source Template
    source_template = find_form_template_by_id(source_template_id)
    if not source_template:
        flash("Source template not found.", "error")
        return redirect(url_for('main.list_templates'))

    if source_template.status == FormTemplateStatus.DRAFT:
        flash("DRAFT templates cannot be cloned. Please publish or archive first.", "warning")
        return redirect(url_for('main.list_templates'))

    # 3. Create Cloned Template (New Draft)
    cloned_name = f"Clone of {source_template.name}" # Consider a more robust naming/user input later
    user_id = session.get('user_id')
    creator = find_user_by_id(user_id)
    if not creator:
        abort(400, "User not found, cannot clone template.")


    new_draft_template = FormTemplate(
        name=cloned_name,
        static_content=source_template.static_content,
        appendix_content=source_template.appendix_content,
        created_by_id=creator.id,
        status=FormTemplateStatus.DRAFT,
        version=1 # New draft is version 1
    )
    form_templates_db.append(new_draft_template)

    # 4. Clone Questions
    source_questions = source_template.get_questions()
    for src_q in source_questions:
        # The add_question method of FormTemplate handles adding to global questions_db
        # and to the template's internal list (though not strictly necessary if get_questions filters global)
        new_draft_template.add_question(
            question_text=src_q.text,
            question_type=src_q.question_type, # This is already an Enum member
            options=list(src_q.options), # Pass a copy of the list
            is_mandatory=src_q.is_mandatory
            # Order will be recalculated by add_question, or we can explicitly copy src_q.order if preferred
            # For now, add_question recalculates order based on current count.
        )
        # If explicit order is needed:
        # new_q = Question(form_template_id=new_draft_template.id, order=src_q.order, ...)
        # questions_db.append(new_q)
        # new_draft_template.questions.append(new_q)


    flash(f"Template '{source_template.name}' cloned successfully. The new draft '{new_draft_template.name}' is ready for editing.", "success")
    return redirect(url_for('main.edit_template', template_id=new_draft_template.id))

@main_bp.route('/admin/templates/<int:template_id>/publish', methods=['POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def publish_template(template_id):
    draft_to_publish = find_form_template_by_id(template_id)

    if not draft_to_publish:
        flash("Template to publish not found.", "error")
        return redirect(url_for('main.list_templates'))

    if draft_to_publish.status != FormTemplateStatus.DRAFT:
        flash("Only DRAFT templates can be published.", "warning")
        return redirect(url_for('main.edit_template', template_id=template_id)) # Or list_templates

    # Archive existing published template
    existing_published = get_published_template()
    if existing_published:
        existing_published.status = FormTemplateStatus.ARCHIVED
        existing_published.updated_at = datetime.utcnow()
        flash(f"Previously published template '{existing_published.name}' (v{existing_published.version}) has been archived.", "info")

    # Publish the new template
    draft_to_publish.status = FormTemplateStatus.PUBLISHED
    draft_to_publish.updated_at = datetime.utcnow()
    # The version of the draft becomes the version of the published template.
    flash(f"Template '{draft_to_publish.name}' (v{draft_to_publish.version}) has been published successfully.", "success")

    return redirect(url_for('main.list_templates'))

@main_bp.route('/admin/trigger-compliance-job', methods=['POST', 'GET']) # Allow GET to display a button
@roles_required('ADMIN', 'SUPER_ADMIN')
def trigger_compliance_job_route():
    if request.method == 'POST':
        report = run_compliance_form_trigger_job()
        # For complex reports, you might render a dedicated template.
        # For now, using flash for a summary.
        flash(f"Compliance Job Ran. Status: {report.get('status', 'unknown')}", "info")
        flash(f"Published Template Found: {report.get('published_template_found')}, ID: {report.get('published_template_id')}", "info")
        flash(f"Issuer Audit Clients: {report.get('issuer_audit_clients_count')}", "info")
        flash(f"Eligible Timesheets: {report.get('eligible_timesheets_count')}", "info")
        flash(f"New Compliance Requests Created: {report.get('new_requests_created_count')}", "success" if report.get('new_requests_created_count',0) > 0 else "info")

        for err in report.get("errors", []):
            flash(f"Job Error: {err}", "error")
        for warn in report.get("warnings", []):
            flash(f"Job Warning: {warn}", "warning")

        # Optionally, show detailed creation info if any
        # for detail in report.get("requests_created_details", []):
        # flash(f"Created Request ID: {detail['request_id']} for Emp {detail['employee_id']}", "debug")

        return redirect(url_for('main.list_compliance_requests'))

    # GET request: show a button to trigger the job
    return render_template('admin/trigger_job.html')

@main_bp.route('/admin/compliance-requests', methods=['GET'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def list_compliance_requests():
    return render_template('admin/list_compliance_requests.html', requests=compliance_requests_db)

@main_bp.route('/admin/email-logs', methods=['GET'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def list_email_logs():
    return render_template('admin/list_email_logs.html', email_logs=email_logs_db)

# --- Employee Facing Routes ---
@main_bp.route('/my-requests')
@roles_required('ADMIN', 'SUPER_ADMIN', 'EMPLOYEE') # Accessible to all logged-in users
def my_requests():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('main.login'))

    # Fetch all requests for the current user initially
    user_requests = [req for req in compliance_requests_db if req.employee_id == user_id]

    # Get filter parameters from request.args
    filter_status_str = request.args.get('status')
    filter_client_id_str = request.args.get('client_id')
    filter_project_id = request.args.get('project_id')

    # Apply filters
    if filter_status_str:
        try:
            filter_status = ComplianceRequestStatus[filter_status_str.upper()]
            user_requests = [req for req in user_requests if req.status == filter_status]
        except KeyError:
            flash(f"Invalid status filter: {filter_status_str}", "warning")

    if filter_client_id_str and filter_client_id_str.isdigit():
        filter_client_id = int(filter_client_id_str)
        user_requests = [req for req in user_requests if req.client_id == filter_client_id]

    if filter_project_id: # Assuming project_id is a string
        user_requests = [req for req in user_requests if req.project_id == filter_project_id]

    # For populating filter dropdowns:
    # Get unique client IDs and project IDs from the *original* list of user's requests
    all_user_requests_for_filters = [req for req in compliance_requests_db if req.employee_id == user_id]

    # Client names for dropdown
    client_ids_in_requests = {req.client_id for req in all_user_requests_for_filters}
    clients_for_filter = {client.id: client.name for client in clients_db if client.id in client_ids_in_requests}

    project_ids_for_filter = sorted(list({req.project_id for req in all_user_requests_for_filters if req.project_id}))

    # Get form names for display
    template_names = {template.id: template.name for template in form_templates_db}

    return render_template('employee/my_requests.html',
                           requests=user_requests,
                           template_names=template_names,
                           clients_for_filter=clients_for_filter,
                           project_ids_for_filter=project_ids_for_filter,
                           all_statuses=[s.name for s in ComplianceRequestStatus],
                           current_filters={
                               'status': filter_status_str,
                               'client_id': filter_client_id_str,
                               'project_id': filter_project_id
                            })

@main_bp.route('/requests/<int:request_id>/fill', methods=['GET', 'POST'])
@roles_required('ADMIN', 'SUPER_ADMIN', 'EMPLOYEE')
def fill_compliance_form(request_id):
    user_id = session.get('user_id')
    current_user = find_user_by_id(user_id) # For email notifications
    compliance_request = next((cr for cr in compliance_requests_db if cr.id == request_id), None)

    if not compliance_request:
        flash("Compliance request not found.", "error")
        return redirect(url_for('main.my_requests'))

    # Authorization: Must be assigned employee or an admin
    current_user_role = Role(session.get('user_role'))
    if compliance_request.employee_id != user_id and current_user_role not in [Role.ADMIN, Role.SUPER_ADMIN]:
        abort(403, "You are not authorized to access this compliance request.")

    form_template = find_form_template_by_id(compliance_request.form_template_id)
    if not form_template:
        flash("Associated form template not found.", "error")
        return redirect(url_for('main.my_requests'))

    questions = sorted(form_template.get_questions(), key=lambda q: q.order)
    client_obj = find_client_by_id(compliance_request.client_id)
    client_name = client_obj.name if client_obj else "N/A"

    if request.method == 'POST':
        if compliance_request.status != ComplianceRequestStatus.PENDING:
            flash(f"This request (ID: {request_id}) is no longer PENDING. Current status: {compliance_request.status.value}", "warning")
            return redirect(url_for('main.my_requests'))

        # Only assigned employee can submit
        if compliance_request.employee_id != user_id:
             abort(403, "Only the assigned employee can submit this form.")


        errors = {} # To collect validation errors
        # Process Answers
        for q in questions:
            form_field_name = f"q_{q.id}"
            answer_text = None
            selected_options_list = []

            if q.question_type in [QuestionType.TEXT_INPUT, QuestionType.FILE_UPLOAD, QuestionType.DROPDOWN, QuestionType.RADIO]:
                answer_text = request.form.get(form_field_name)
                if q.question_type in [QuestionType.DROPDOWN, QuestionType.RADIO]:
                    selected_options_list = [answer_text] if answer_text else []
            elif q.question_type in [QuestionType.CHECKBOX, QuestionType.MULTI_SELECT_COMBO]:
                selected_options_list = request.form.getlist(form_field_name)
                # For these types, answer_text might be a concatenation or not used if selected_options is primary
                if selected_options_list:
                     answer_text = ", ".join(selected_options_list)


            # Mandatory check (HTML required is client-side, this is server-side)
            if q.is_mandatory:
                is_empty = False
                if q.question_type in [QuestionType.CHECKBOX, QuestionType.MULTI_SELECT_COMBO]:
                    if not selected_options_list:
                        is_empty = True
                elif not answer_text: # Covers TEXT, FILE(name), RADIO, DROPDOWN
                    is_empty = True

                if is_empty:
                    errors[form_field_name] = f"Question {q.order} ('{q.text[:30]}...') is mandatory."

            if not errors.get(form_field_name): # Only save if no error for this question yet
                # For FILE_UPLOAD, request.files[form_field_name] would be used.
                # For now, just storing the filename if provided as text.
                if q.question_type == QuestionType.FILE_UPLOAD and request.files.get(form_field_name):
                    uploaded_file = request.files[form_field_name]
                    if uploaded_file.filename:
                        # In a real app: secure_filename, save file, store path/ID
                        answer_text = f"uploaded_{uploaded_file.filename}" # Placeholder
                    elif q.is_mandatory: # File was mandatory but not provided
                         errors[form_field_name] = f"File for question {q.order} ('{q.text[:30]}...') is mandatory."


                new_answer = FormAnswer(
                    compliance_request_id=request_id,
                    question_id=q.id,
                    answer_text=answer_text,
                    selected_options=selected_options_list
                )
                form_answers_db.append(new_answer)

        if errors:
            flash("Please correct the errors below and resubmit.", "error")
            # Re-render form with errors and previous data (data repopulation not implemented here for brevity)
            return render_template('employee/fill_form.html',
                                   compliance_request=compliance_request,
                                   form_template=form_template,
                                   questions=questions,
                                   client_name=client_name,
                                   project_id=compliance_request.project_id,
                                   QuestionType=QuestionType,
                                   errors=errors,
                                   form_data=request.form) # Pass form data for repopulation

        # Update Compliance Request status
        compliance_request.status = ComplianceRequestStatus.COMPLETED
        compliance_request.completion_date = datetime.utcnow()

        # Simulate Super Admin Notification
        super_admins = [user for user in users if user.role == Role.SUPER_ADMIN]
        email_subject = f"Compliance Form Completed: Request {request_id} by {current_user.username}"
        email_body = (
            f"Employee {current_user.username} (ID: {current_user.id}) has completed the compliance form "
            f"for Request ID: {request_id}.\n"
            f"Form Name: {form_template.name}\n"
            f"Client: {client_name} (ID: {compliance_request.client_id})\n"
            f"Project: {compliance_request.project_id}\n"
            f"Completion Date: {compliance_request.completion_date.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        for sa in super_admins:
            email_log = EmailLog(
                recipient_email=sa.email,
                employee_id=sa.id, # Logged against the super admin receiving it
                subject=email_subject,
                body=email_body,
                status=EmailStatus.SENT_SIMULATED,
                compliance_request_id=request_id
            )
            email_logs_db.append(email_log)

        flash("Form submitted successfully!", "success")
        return redirect(url_for('main.my_requests'))

    # GET request
    return render_template('employee/fill_form.html',
                           compliance_request=compliance_request,
                           form_template=form_template,
                           questions=questions,
                           client_name=client_name,
                           project_id=compliance_request.project_id,
                           QuestionType=QuestionType,
                           errors={}) # Pass empty errors dict for GET

# --- Admin Reports ---
@main_bp.route('/admin/reports/compliant-request-tracker')
@roles_required('ADMIN', 'SUPER_ADMIN')
def compliant_request_tracker_report():
    # Fetch all data needed for display and filtering
    all_requests = list(compliance_requests_db) # Work with a copy

    # Get filter parameters
    filter_status_str = request.args.get('status')
    filter_client_id_str = request.args.get('client_id')
    filter_project_id = request.args.get('project_id')
    filter_employee_id_str = request.args.get('employee_id')

    # Apply filters
    if filter_status_str:
        try:
            filter_status = ComplianceRequestStatus[filter_status_str.upper()]
            all_requests = [req for req in all_requests if req.status == filter_status]
        except KeyError:
            flash(f"Invalid status filter: {filter_status_str}", "warning")

    if filter_client_id_str and filter_client_id_str.isdigit():
        filter_client_id = int(filter_client_id_str)
        all_requests = [req for req in all_requests if req.client_id == filter_client_id]

    if filter_project_id:
        all_requests = [req for req in all_requests if req.project_id == filter_project_id]

    if filter_employee_id_str and filter_employee_id_str.isdigit():
        filter_employee_id = int(filter_employee_id_str)
        all_requests = [req for req in all_requests if req.employee_id == filter_employee_id]

    # Augment data: days_pending and resolve names
    report_data = []
    for req in all_requests:
        days_pending = None
        if req.status == ComplianceRequestStatus.PENDING:
            days_pending = (datetime.utcnow() - req.request_sent_date).days

        employee = find_user_by_id(req.employee_id)
        client = find_client_by_id(req.client_id)
        form_template = find_form_template_by_id(req.form_template_id)

        report_data.append({
            'request': req,
            'employee_name': employee.username if employee else 'N/A',
            'client_name': client.name if client else 'N/A',
            'form_template_name': form_template.name if form_template else 'N/A',
            'form_template_version': form_template.version if form_template else 'N/A', # Added version
            'days_pending': days_pending
        })

    # Data for filter dropdowns - based on *all* requests, not just filtered ones
    # to allow broadening search.
    all_clients_in_requests = {req.client_id: find_client_by_id(req.client_id) for req in compliance_requests_db}
    clients_for_filter = {cid: c.name for cid, c in all_clients_in_requests.items() if c}

    all_projects_in_requests = sorted(list(set(req.project_id for req in compliance_requests_db if req.project_id)))

    all_employees_in_requests = {req.employee_id: find_user_by_id(req.employee_id) for req in compliance_requests_db}
    employees_for_filter = {eid: u.username for eid, u in all_employees_in_requests.items() if u}


    return render_template('admin/report_compliant_request_tracker.html',
                           report_data=report_data,
                           all_statuses=[s.name for s in ComplianceRequestStatus],
                           clients_for_filter=clients_for_filter,
                           project_ids_for_filter=all_projects_in_requests,
                           employees_for_filter=employees_for_filter,
                           current_filters={
                               'status': filter_status_str,
                               'client_id': filter_client_id_str,
                               'project_id': filter_project_id,
                               'employee_id': filter_employee_id_str
                           })

@main_bp.route('/admin/reports/compliant-request-tracker/export/csv')
@roles_required('ADMIN', 'SUPER_ADMIN')
def export_compliant_request_tracker_csv():
    #ほぼ同じデータ取得とフィルタリングロジックをレポートビューから再利用する
    all_requests = list(compliance_requests_db)

    filter_status_str = request.args.get('status')
    filter_client_id_str = request.args.get('client_id')
    filter_project_id = request.args.get('project_id')
    filter_employee_id_str = request.args.get('employee_id')

    if filter_status_str:
        try:
            filter_status = ComplianceRequestStatus[filter_status_str.upper()]
            all_requests = [req for req in all_requests if req.status == filter_status]
        except KeyError:
            pass # 無効なステータスは無視するか、エラーを出す

    if filter_client_id_str and filter_client_id_str.isdigit():
        filter_client_id = int(filter_client_id_str)
        all_requests = [req for req in all_requests if req.client_id == filter_client_id]

    if filter_project_id:
        all_requests = [req for req in all_requests if req.project_id == filter_project_id]

    if filter_employee_id_str and filter_employee_id_str.isdigit():
        filter_employee_id = int(filter_employee_id_str)
        all_requests = [req for req in all_requests if req.employee_id == filter_employee_id]

    # CSV生成のためのデータ拡張
    output = io.StringIO()
    csv_writer = csv.writer(output)

    headers = [
        "Request ID", "Employee Name", "Employee Email", "Form Name", "Form Version",
        "Client Name", "Project ID", "Status", "Request Sent Date",
        "Completion Date", "Days Pending"
    ]
    csv_writer.writerow(headers)

    for req in all_requests:
        days_pending = ""
        if req.status == ComplianceRequestStatus.PENDING:
            days_pending = str((datetime.utcnow() - req.request_sent_date).days)

        employee = find_user_by_id(req.employee_id)
        client = find_client_by_id(req.client_id)
        form_template = find_form_template_by_id(req.form_template_id)

        row = [
            req.id,
            employee.username if employee else 'N/A',
            employee.email if employee else 'N/A',
            form_template.name if form_template else 'N/A',
            form_template.version if form_template else 'N/A',
            client.name if client else 'N/A',
            req.project_id,
            req.status.value if req.status else 'N/A',
            req.request_sent_date.strftime('%Y-%m-%d %H:%M:%S UTC') if req.request_sent_date else '',
            req.completion_date.strftime('%Y-%m-%d %H:%M:%S UTC') if req.completion_date else '',
            days_pending
        ]
        csv_writer.writerow(row)

    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=compliant_requests_export.csv"}
    )

# --- Actions from Compliant Request Tracker ---
@main_bp.route('/admin/requests/<int:request_id>/cancel', methods=['POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def cancel_compliance_request(request_id):
    compliance_request = next((cr for cr in compliance_requests_db if cr.id == request_id), None)

    if not compliance_request:
        flash(f"Compliance request ID {request_id} not found.", "error")
    elif compliance_request.status != ComplianceRequestStatus.PENDING:
        flash(f"Request ID {request_id} is not PENDING (current status: {compliance_request.status.value}). Cannot cancel.", "warning")
    else:
        compliance_request.status = ComplianceRequestStatus.CANCELLED
        compliance_request.updated_at = datetime.utcnow()
        flash(f"Compliance request ID {request_id} has been cancelled.", "success")

    return redirect(url_for('main.compliant_request_tracker_report', **request.args)) # Preserve filters

@main_bp.route('/admin/requests/<int:request_id>/close', methods=['POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def close_compliance_request(request_id):
    compliance_request = next((cr for cr in compliance_requests_db if cr.id == request_id), None)

    if not compliance_request:
        flash(f"Compliance request ID {request_id} not found.", "error")
    elif compliance_request.status != ComplianceRequestStatus.PENDING:
        flash(f"Request ID {request_id} is not PENDING (current status: {compliance_request.status.value}). Cannot mark as closed.", "warning")
    else:
        compliance_request.status = ComplianceRequestStatus.CLOSED
        compliance_request.updated_at = datetime.utcnow()
        flash(f"Compliance request ID {request_id} has been marked as closed.", "success")

    return redirect(url_for('main.compliant_request_tracker_report', **request.args)) # Preserve filters


@main_bp.route('/admin/templates/<int:template_id>/edit', methods=['GET', 'POST'])
@roles_required('ADMIN', 'SUPER_ADMIN')
def edit_template(template_id):
    template = find_form_template_by_id(template_id)
    if not template:
        abort(404)

    if template.status != FormTemplateStatus.DRAFT:
        # As per ID-1003.2, maybe redirect to a read-only view or flash a message
        # For now, just forbid editing if not DRAFT, could be a redirect to list_templates too.
        abort(403, "Only DRAFT templates can be edited.")

    if request.method == 'POST':
        # This part handles adding a new question
        question_text = request.form.get('question_text')
        question_type_str = request.form.get('question_type')
        options_str = request.form.get('options', '') # Comma-separated for radio/dropdown etc.

        if not question_text or not question_type_str:
            # Add flash message for error
            return redirect(url_for('main.edit_template', template_id=template_id))

        try:
            question_type = QuestionType[question_type_str.upper()]
        except KeyError:
            # Add flash message for invalid type
            return redirect(url_for('main.edit_template', template_id=template_id))

        parsed_options = []
        if question_type in [QuestionType.RADIO, QuestionType.DROPDOWN, QuestionType.CHECKBOX, QuestionType.MULTI_SELECT_COMBO]:
            if options_str:
                parsed_options = [opt.strip() for opt in options_str.split(',') if opt.strip()]
            else:
                # Potentially add flash message: Options are required for this type
                pass # Allow empty options for now, or add validation

        # The add_question method in FormTemplate handles creating Question instance
        # and adding to both template.questions and global questions_db
        template.add_question(
            question_text=question_text,
            question_type=question_type,
            options=parsed_options
        )
        # Redirect to the same page (GET request) to see the new question
        return redirect(url_for('main.edit_template', template_id=template_id))

    # GET request: display template details and questions
    template_questions = template.get_questions() # Use the method to get questions
    all_question_types = [qt.name for qt in QuestionType] # Use all available types
    return render_template('admin/edit_template.html',
                           template=template,
                           questions=template_questions,
                           question_types=all_question_types,
                           FormTemplateStatus=FormTemplateStatus) # Pass the enum
