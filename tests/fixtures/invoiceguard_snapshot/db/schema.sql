CREATE TABLE compliance_reports (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	total_score FLOAT NOT NULL, 
	issued_at DATETIME, 
	CONSTRAINT pk_compliance_reports PRIMARY KEY (id), 
	CONSTRAINT fk_compliance_reports_invoice_id_invoices FOREIGN KEY(invoice_id) REFERENCES invoices (id)
);

CREATE TABLE compliance_rules (
	id INTEGER NOT NULL, 
	compliance_report_id INTEGER NOT NULL, 
	rule_code VARCHAR(50) NOT NULL, 
	description VARCHAR(1000), 
	amount FLOAT, 
	severity VARCHAR(20), 
	CONSTRAINT pk_compliance_rules PRIMARY KEY (id), 
	CONSTRAINT fk_compliance_rules_compliance_report_id_compliance_reports FOREIGN KEY(compliance_report_id) REFERENCES compliance_reports (id)
);

CREATE TABLE config (
	id INTEGER NOT NULL, 
	values_json JSON NOT NULL, 
	retired BOOLEAN NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_config PRIMARY KEY (id)
);

CREATE TABLE contracts (
	id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	item_code VARCHAR(50) NOT NULL, 
	description VARCHAR(500), 
	contract_rate FLOAT NOT NULL, 
	list_price FLOAT, 
	effective_from DATETIME, 
	effective_to DATETIME, 
	CONSTRAINT pk_contracts PRIMARY KEY (id), 
	CONSTRAINT fk_contracts_supplier_id_suppliers FOREIGN KEY(supplier_id) REFERENCES suppliers (id)
);

CREATE TABLE finding_feedback (
	id INTEGER NOT NULL, 
	finding_id INTEGER NOT NULL, 
	auditor_id INTEGER, 
	valid_exception BOOLEAN NOT NULL, 
	rule_misfire BOOLEAN NOT NULL, 
	feedback_text VARCHAR(1000), 
	misfire_text VARCHAR(1000), 
	cloned BOOLEAN NOT NULL, 
	CONSTRAINT pk_finding_feedback PRIMARY KEY (id), 
	CONSTRAINT uq_finding_feedback_finding_id UNIQUE (finding_id), 
	CONSTRAINT fk_finding_feedback_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id), 
	CONSTRAINT fk_finding_feedback_auditor_id_users FOREIGN KEY(auditor_id) REFERENCES users (id)
);

CREATE TABLE findings (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	line_number INTEGER, 
	rule_name VARCHAR(100) NOT NULL, 
	category VARCHAR(15) NOT NULL, 
	description VARCHAR(1000), 
	amount FLOAT, 
	created_at DATETIME, 
	CONSTRAINT pk_findings PRIMARY KEY (id), 
	CONSTRAINT fk_findings_invoice_id_invoices FOREIGN KEY(invoice_id) REFERENCES invoices (id)
);

CREATE TABLE invoice_history (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	from_status VARCHAR(20), 
	to_status VARCHAR(20) NOT NULL, 
	actor VARCHAR(100) NOT NULL, 
	at DATETIME NOT NULL, 
	CONSTRAINT pk_invoice_history PRIMARY KEY (id), 
	CONSTRAINT fk_invoice_history_invoice_id_invoices FOREIGN KEY(invoice_id) REFERENCES invoices (id)
);

CREATE TABLE invoice_lines (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	line_number INTEGER NOT NULL, 
	item_code VARCHAR(50), 
	description VARCHAR(500), 
	quantity FLOAT, 
	unit_rate FLOAT, 
	extended_price FLOAT, 
	line_type VARCHAR(10), 
	service_hours FLOAT, 
	notes VARCHAR(1000), 
	CONSTRAINT pk_invoice_lines PRIMARY KEY (id), 
	CONSTRAINT fk_invoice_lines_invoice_id_invoices FOREIGN KEY(invoice_id) REFERENCES invoices (id)
);

CREATE TABLE invoices (
	id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	invoice_number VARCHAR(100) NOT NULL, 
	revision INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	payload_json JSON, 
	invoice_total FLOAT, 
	adjustment_flag BOOLEAN NOT NULL, 
	rush_flag BOOLEAN NOT NULL, 
	is_credit_memo BOOLEAN NOT NULL, 
	disputed_hold BOOLEAN NOT NULL, 
	po_reference VARCHAR(100), 
	currency VARCHAR(10), 
	received_at DATETIME, 
	scored_at DATETIME, 
	opportunity FLOAT, 
	weight FLOAT, 
	compliance_score FLOAT, 
	service_hours_delta FLOAT, 
	alt_source_pct_delta FLOAT, 
	claimed_by INTEGER, 
	prior_revision_id INTEGER, 
	supplier_acceptance VARCHAR(10), 
	CONSTRAINT pk_invoices PRIMARY KEY (id), 
	CONSTRAINT uq_invoices_supplier_id_invoice_number_revision UNIQUE (supplier_id, invoice_number, revision), 
	CONSTRAINT fk_invoices_supplier_id_suppliers FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
	CONSTRAINT fk_invoices_claimed_by_users FOREIGN KEY(claimed_by) REFERENCES users (id), 
	CONSTRAINT fk_invoices_prior_revision_id_invoices FOREIGN KEY(prior_revision_id) REFERENCES invoices (id)
);

CREATE TABLE review_report_lines (
	id INTEGER NOT NULL, 
	review_report_id INTEGER NOT NULL, 
	line_number INTEGER NOT NULL, 
	requested_rate FLOAT, 
	remove_requested BOOLEAN NOT NULL, 
	note VARCHAR(1000), 
	CONSTRAINT pk_review_report_lines PRIMARY KEY (id), 
	CONSTRAINT fk_review_report_lines_review_report_id_review_reports FOREIGN KEY(review_report_id) REFERENCES review_reports (id)
);

CREATE TABLE review_reports (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	auditor_notes VARCHAR(2000), 
	disposition VARCHAR(50), 
	CONSTRAINT pk_review_reports PRIMARY KEY (id), 
	CONSTRAINT fk_review_reports_invoice_id_invoices FOREIGN KEY(invoice_id) REFERENCES invoices (id)
);

CREATE TABLE scheduled_tasks (
	id INTEGER NOT NULL, 
	task_name VARCHAR(100) NOT NULL, 
	args_json JSON, 
	due_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	CONSTRAINT pk_scheduled_tasks PRIMARY KEY (id)
);

CREATE TABLE suppliers (
	id INTEGER NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	network BOOLEAN NOT NULL, 
	first_contracted_at DATETIME, 
	CONSTRAINT pk_suppliers PRIMARY KEY (id), 
	CONSTRAINT uq_suppliers_code UNIQUE (code)
);

CREATE TABLE users (
	id INTEGER NOT NULL, 
	short_name VARCHAR(50) NOT NULL, 
	team VARCHAR(100), 
	role VARCHAR(20) NOT NULL, 
	available BOOLEAN NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id)
);

