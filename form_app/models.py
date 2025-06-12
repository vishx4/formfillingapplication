from enum import Enum
from datetime import datetime
import json # For Question options

class Role(Enum):
    ADMIN = 'admin'
    SUPER_ADMIN = 'super_admin'
    EMPLOYEE = 'employee'

class User:
    def __init__(self, id, username, role, email=None): # Added email
        self.id = id
        self.username = username
        self.role = role
        self.email = email if email else f"{username}@example.com" # Default email
        self.form_templates_created = [] # Relationship placeholder

# Placeholder users for initial testing - ensuring admin/super_admin exists
users = [
    User(1, 'admin_user', Role.ADMIN, email='admin@example.com'),
    User(2, 'super_admin_user', Role.SUPER_ADMIN, email='superadmin@example.com'),
    User(3, 'employee_user', Role.EMPLOYEE, email='employee1@example.com'), # Employee needs email
    User(4, 'another_admin', Role.ADMIN, email='another_admin@example.com') # Could be an employee too
]

# In-memory storage for form templates and questions
form_templates_db = []
questions_db = []
next_form_template_id = 1
next_question_id = 1


class FormTemplateStatus(Enum):
    DRAFT = 'DRAFT'
    PUBLISHED = 'PUBLISHED'
    ARCHIVED = 'ARCHIVED'

class FormTemplate:
    def __init__(self, name, static_content, appendix_content, created_by_id, version=1, status=FormTemplateStatus.DRAFT):
        global next_form_template_id
        self.id = next_form_template_id
        next_form_template_id += 1
        self.name = name
        self.version = version # Manual or simple increment for now
        self.status = status
        self.static_content = static_content
        self.appendix_content = appendix_content
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.created_by_id = created_by_id
        self.questions = [] # Holds Question objects directly

    def add_question(self, question_text, question_type, options=None, is_mandatory=True):
        global questions_db # Ensure we're using the global list

        # Determine order: simple count for now
        order = len(self.questions) + 1

        new_question = Question(
            form_template_id=self.id,
            order=order,
            text=question_text,
            question_type=question_type,
            options=options if options else {},
            is_mandatory=is_mandatory
        )
        questions_db.append(new_question) # Add to global questions list
        self.questions.append(new_question) # Add to template's local list
        self.updated_at = datetime.utcnow()
        return new_question

    def get_questions(self):
        # If questions are stored globally, this method can filter them
        # Or, if stored locally as done above, just return self.questions
        # For consistency with how questions_db is planned to be used by routes:
        return [q for q in questions_db if q.form_template_id == self.id]


    def __repr__(self):
        return f"<FormTemplate {self.id}: {self.name} (v{self.version})>"


class QuestionType(Enum):
    TEXT_INPUT = 'TEXT_INPUT'
    RADIO = 'RADIO'
    CHECKBOX = 'CHECKBOX' # Added
    DROPDOWN = 'DROPDOWN' # Added
    FILE_UPLOAD = 'FILE_UPLOAD' # Added
    MULTI_SELECT_COMBO = 'MULTI_SELECT_COMBO' # Added

class Question:
    def __init__(self, form_template_id, order, text, question_type, options=None, is_mandatory=True):
        global next_question_id
        self.id = next_question_id
        next_question_id += 1
        self.form_template_id = form_template_id
        self.order = order
        self.text = text
        self.question_type = question_type
        # Options: store as dict, expect dict or comma-separated string for input
        if isinstance(options, str):
            self.options = [opt.strip() for opt in options.split(',') if opt.strip()]
        elif isinstance(options, list):
            self.options = options
        else:
            self.options = [] # Default to empty list if None or other type
        self.is_mandatory = is_mandatory

    def __repr__(self):
        return f"<Question {self.id} for FormTemplate {self.form_template_id}: {self.text[:30]}>"

# Helper function to find a user by ID (useful for created_by)
def find_user_by_id(user_id):
    return next((user for user in users if user.id == user_id), None)

def find_form_template_by_id(template_id):
    return next((template for template in form_templates_db if template.id == template_id), None)


# --- New Models & Data Stores for Compliance Job ---

class Client:
    _id_counter = 1
    def __init__(self, name, is_issuer_audit_client=False):
        self.id = Client._id_counter
        Client._id_counter += 1
        self.name = name
        self.is_issuer_audit_client = is_issuer_audit_client

    def __repr__(self):
        return f"<Client {self.id}: {self.name}>"

class TimesheetRecord:
    _id_counter = 1
    def __init__(self, employee_id, client_id, project_id, date_posted, status, hours):
        self.id = TimesheetRecord._id_counter
        TimesheetRecord._id_counter += 1
        self.employee_id = employee_id
        self.client_id = client_id
        self.project_id = project_id # Placeholder
        self.date_posted = date_posted # Should be a date object
        self.status = status # e.g., 30 for "posted"
        self.hours = hours

    def __repr__(self):
        return f"<TimesheetRecord {self.id} for Emp {self.employee_id} on Client {self.client_id}>"

class ComplianceRequestStatus(Enum):
    PENDING = 'PENDING'
    CANCELLED = 'CANCELLED'
    COMPLETED = 'COMPLETED'
    CLOSED = 'CLOSED' # As per requirement "User already completed the request and it is CLOSED by Admin"

class EmailStatus(Enum):
    SENT_SIMULATED = 'SENT_SIMULATED'
    SENT_ACTUAL = 'SENT_ACTUAL' # For future use
    FAILED = 'FAILED' # For future use

class EmailLog:
    _id_counter = 1
    def __init__(self, recipient_email, subject, body, status, employee_id=None, compliance_request_id=None):
        self.id = EmailLog._id_counter
        EmailLog._id_counter += 1
        self.timestamp = datetime.utcnow()
        self.recipient_email = recipient_email
        self.employee_id = employee_id
        self.subject = subject
        self.body = body # Can be extensive, consider how it's stored/displayed
        self.status = status
        self.compliance_request_id = compliance_request_id

    def __repr__(self):
        return f"<EmailLog {self.id} to {self.recipient_email} - {self.subject[:30]}>"

class ComplianceRequest:
    _id_counter = 1
    def __init__(self, employee_id, form_template_id, client_id, project_id, timesheet_record_ids, status=ComplianceRequestStatus.PENDING):
        self.id = ComplianceRequest._id_counter
        ComplianceRequest._id_counter += 1
        self.employee_id = employee_id
        self.form_template_id = form_template_id
        self.client_id = client_id
        self.project_id = project_id
        self.status = status
        self.request_sent_date = datetime.utcnow()
        self.updated_at = datetime.utcnow() # Added/Ensure this field
        self.due_date = None # To be determined or calculated
        self.completion_date = None
        self.timesheet_record_ids = timesheet_record_ids # List of IDs

    def __repr__(self):
        return f"<ComplianceRequest {self.id} for Emp {self.employee_id}, Template {self.form_template_id}>"

clients_db = []
timesheet_records_db = []
compliance_requests_db = []
email_logs_db = []
form_answers_db = [] # New DB for form answers

class FormAnswer:
    _id_counter = 1
    def __init__(self, compliance_request_id, question_id, answer_text=None, selected_options=None):
        self.id = FormAnswer._id_counter
        FormAnswer._id_counter += 1
        self.compliance_request_id = compliance_request_id
        self.question_id = question_id
        self.answer_text = answer_text # For TEXT_INPUT, FILE_UPLOAD (path)
        self.selected_options = selected_options if selected_options is not None else [] # For RADIO, CHECKBOX, DROPDOWN, MULTI_SELECT

    def __repr__(self):
        return f"<FormAnswer {self.id} for Req {self.compliance_request_id}, Q {self.question_id}>"

# --- Helper functions to find items in in-memory DBs ---
def find_client_by_id(client_id):
    return next((client for client in clients_db if client.id == client_id), None)

# Placeholder if projects were more complex; for now, project_id is just an attribute.
# def find_project_by_id(project_id):
#     return next((project for project in projects_db if project.id == project_id), None)

def get_active_draft_template():
    """Finds if there's an active DRAFT template."""
    for template in form_templates_db:
        if template.status == FormTemplateStatus.DRAFT:
            return template
    return None

def get_published_template():
    """Finds if there's an active PUBLISHED template."""
    for template in form_templates_db:
        if template.status == FormTemplateStatus.PUBLISHED:
            return template
    return None

# --- Sample Data Initialization ---
def init_sample_data():
    global users, clients_db, timesheet_records_db
    # Ensure users exist (from previous setup, or add more if needed)
    # Ensure users have emails for the job to use
    # This is handled by updating User class and user list above.

    # Find employee by ID from users list
    employee1 = find_user_by_id(3) # employee_user
    employee2 = find_user_by_id(4) # another_admin (acting as an employee for test data)

    employee1_id = employee1.id if employee1 else None
    employee2_id = employee2.id if employee2 else None

    # If users list was modified, ensure these IDs are valid or update them.
    # For robustness, let's ensure we use valid IDs if users list changed.
    if not employee1_id and users: employee1_id = users[0].id # Fallback to first user
    if not employee2_id and len(users) > 1: employee2_id = users[1].id # Fallback to second user


    if not clients_db:
        client1 = Client(name="Global Corp (Issuer Audit)", is_issuer_audit_client=True)
        client2 = Client(name="Tech Solutions Inc. (Issuer Audit)", is_issuer_audit_client=True)
        client3 = Client(name="Local Biz Ltd (Non-Issuer)")
        client4 = Client(name="Innovate Co (Issuer Audit)", is_issuer_audit_client=True)
        clients_db.extend([client1, client2, client3, client4])

    if not timesheet_records_db:
        from datetime import date, timedelta
        today = date.today()
        # Valid: Issuer Audit, recent, status 30
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[0].id, project_id="P101", date_posted=today - timedelta(days=10), status=30, hours=8))
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[0].id, project_id="P101", date_posted=today - timedelta(days=12), status=30, hours=4)) # another for same project

        # Valid: Another Issuer Audit Client, recent, status 30
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[1].id, project_id="P202", date_posted=today - timedelta(days=30), status=30, hours=6))

        # Invalid: Too old
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[0].id, project_id="P102", date_posted=today - timedelta(days=100), status=30, hours=8))

        # Invalid: Not status 30
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[0].id, project_id="P103", date_posted=today - timedelta(days=5), status=20, hours=8))

        # Invalid: Non-Issuer Audit Client
        timesheet_records_db.append(TimesheetRecord(employee_id=employee1_id, client_id=clients_db[2].id, project_id="P301", date_posted=today - timedelta(days=15), status=30, hours=8))

        # Valid: Different employee, Issuer Audit Client
        if employee2_id:
            timesheet_records_db.append(TimesheetRecord(employee_id=employee2_id, client_id=clients_db[3].id, project_id="P401", date_posted=today - timedelta(days=20), status=30, hours=7))
            # Duplicate for same employee, client, project to test aggregation
            timesheet_records_db.append(TimesheetRecord(employee_id=employee2_id, client_id=clients_db[3].id, project_id="P401", date_posted=today - timedelta(days=22), status=30, hours=3))


# Call to initialize data when models.py is loaded.
# This is okay for in-memory, but for DB this would be separate seeding scripts.
init_sample_data()
