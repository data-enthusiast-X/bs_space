import frappe
from datetime import datetime
from frappe.utils import now_datetime, getdate
from frappe.model.document import Document

def validate(doc, method=None):
    is_tax_user(doc, method)
    set_tax_filing_status(doc)
    set_vat_filing_status(doc)
    validate_no_self_shareholder(doc, method)

def is_circular_reference(child, potential_parent):
    """Check recursively if potential_parent is already in child's ancestry."""
    while potential_parent:
        if potential_parent == child:
            return True
        potential_parent = frappe.db.get_value("Customer", potential_parent, "custom_parent_company")
    return False


def before_insert(doc, method):
    # Choose legal name if available
    final_name = doc.custom_company_legal_name or doc.customer_name
    doc.custom_name = final_name

def before_save(doc, method):
    # ensure child table is ordered, then assign sr_no = 1..n
    if getattr(doc, "visa_holders", None):
        # sort by existing idx to keep current order
        doc.visa_holders = sorted(doc.visa_holders, key=lambda d: d.idx or 0)
        for i, row in enumerate(doc.visa_holders, start=1):
            row.sr_no = i



def update_license_status(doc, method):
    """Auto-update license status based on expiry date if notifications are enabled."""
    if doc.custom_license_expiry_notifications:
        expiry = doc.get("custom_license_expiry_date")
        if not expiry:
            return

        expiry_date = expiry if isinstance(expiry, datetime) else datetime.strptime(str(expiry), "%Y-%m-%d").date()
        today = datetime.today().date()
        doc.custom_status = "Expired" if expiry_date < today else "Active"


def validate_license_notification_setting(doc, method):
    """Prevent setting Active/Expired if notifications are disabled."""
    if not doc.custom_license_expiry_notifications and doc.custom_status in ["Active", "Expired"]:
        frappe.throw(
            "You cannot set status to 'Active' or 'Expired' when license expiry notifications are disabled."
        )


def validate_no_self_shareholder(doc, method=None):
    """Prevent a company from being its own shareholder and duplicate rows."""
    seen = set()
    for row in (doc.custom_shareholders or []):
        # Block self as shareholder for Corporate
        if row.shareholder_type == "Corporate" and (row.shareholder or "").strip() == (doc.name or "").strip():
            frappe.throw("A company cannot be its own shareholder.")

        key = (row.shareholder_type or "", row.shareholder or "")
        if key in seen:
            frappe.throw(f"Duplicate shareholder '{row.shareholder}' of type '{row.shareholder_type}'.")
        seen.add(key)


def validate_legal_authorities(doc, method):
    """Validate that business activities match the selected Legal Authority."""
    for ba in doc.custom_business_activities:
        authority = frappe.db.get_value("Business Activity", ba.business_activity, "legal_authority")
        if authority and authority != doc.custom_legal_authority:
            frappe.throw(
                f"Business Activity '{ba.business_activity}' does not belong to the selected Legal Authority '{doc.custom_legal_authority}'."
            )


def validate_parent_company(doc, method):
    """Prevent circular references when setting parent company."""
    if not doc.custom_parent_company:
        return

    if doc.custom_parent_company == doc.name:
        frappe.throw("A customer cannot be their own parent company.")

    if is_circular_reference(doc.name, doc.custom_parent_company):
        frappe.throw(
            f"Assigning '{doc.custom_parent_company}' as parent creates a circular reference."
        )


def validate_shareholders(doc, method):
    """Ensure at least one of each required role is present if shareholders are listed."""
    if not doc.custom_shareholders:
        return  # No shareholders → skip validation

    required_roles = {
        "is_shareholder": "Shareholder",
        "is_directormanager": "Director/Manager",
        "is_ubo": "UBO",
        "is_signatory": "Authorized Signatory"
    }

    missing_roles = []
    for field, label in required_roles.items():
        if not any(getattr(row, field) for row in doc.custom_shareholders):
            missing_roles.append(label)

    if missing_roles:
        frappe.throw(f"Missing required role(s): {', '.join(missing_roles)}")




def sync_channel_partner_sub_company(doc, method):
    if not doc.custom_parent_company:
        return

    new_parent = frappe.get_doc("Customer", doc.custom_parent_company)

    if new_parent.customer_group != "Channel Partner":
        return

    # Fetch previous value of parent company
    if frappe.db.exists("Customer", doc.name):
        old_parent_name = frappe.db.get_value("Customer", doc.name, "custom_parent_company")
        if old_parent_name and old_parent_name != new_parent.name:
            try:
                old_parent = frappe.get_doc("Customer", old_parent_name)
                # Remove this doc from old parent's sub_companies if exists
                old_parent.custom_sub_companies = [
                    row for row in old_parent.custom_sub_companies
                    if row.sub_company != doc.name
                ]
                old_parent.save(ignore_permissions=True)
            except Exception as e:
                frappe.log_error(f"Error removing sub_company from old parent: {e}")

    # Add to new parent if not already there
    if not any(row.sub_company == doc.name for row in new_parent.custom_sub_companies):
        new_parent.append("custom_sub_companies", {"sub_company": doc.name})
        new_parent.save(ignore_permissions=True)



def sync_client_shareholders(doc, method):
    """When saving Client, sync shareholders to linked docs (create/update/delete)."""
    if getattr(frappe.flags, "skip_client_shareholder_sync", False):
        return
    frappe.flags.skip_client_shareholder_sync = True

    # Track currently linked shareholders
    current_individuals = set()
    current_corporates = set()

    for row in doc.custom_shareholders or []:
        if row.shareholder_type == "Individual":
            current_individuals.add(row.shareholder)
            target = frappe.get_doc("Linked Individual", row.shareholder)

            updated = False
            for r in target.owned_companies:
                if r.company == doc.name:
                    # Update shareholding_pct if needed
                    if r.shareholding_pct != row.shareholding_pct:
                        r.shareholding_pct = row.shareholding_pct
                    updated = True
                    break
            if not updated:
                target.append("owned_companies", {
                    "company": doc.name,
                    "shareholding_pct": row.shareholding_pct
                })
            target.save(ignore_permissions=True)

        elif row.shareholder_type == "Corporate":
            current_corporates.add(row.shareholder)
            target = frappe.get_doc("Customer", row.shareholder)

            updated = False
            for r in target.custom_sub_companies:
                if r.sub_company == doc.name:
                    # Update shareholding_pct if needed
                    if r.shareholding_pct != row.shareholding_pct:
                        r.shareholding_pct = row.shareholding_pct
                    updated = True
                    break
            if not updated:
                target.append("custom_sub_companies", {
                    "sub_company": doc.name,
                    "shareholding_pct": row.shareholding_pct
                })
            target.save(ignore_permissions=True)

    # Cleanup: Remove outdated links

    # 1. For Individuals
    individual_names = [r.shareholder for r in doc.custom_shareholders if r.shareholder_type == "Individual"]
    for li_name in frappe.get_all("Linked Individual", pluck="name"):
        li_doc = frappe.get_doc("Linked Individual", li_name)
        before = len(li_doc.owned_companies)
        li_doc.owned_companies = [
            r for r in li_doc.owned_companies
            if not (r.company == doc.name and li_name not in current_individuals)
        ]
        if len(li_doc.owned_companies) != before:
            li_doc.save(ignore_permissions=True)

    # 2. For Corporates
    corporate_names = [r.shareholder for r in doc.custom_shareholders if r.shareholder_type == "Corporate"]
    for corp_name in frappe.get_all("Customer", pluck="name"):
        corp_doc = frappe.get_doc("Customer", corp_name)
        before = len(corp_doc.custom_sub_companies)
        corp_doc.custom_sub_companies = [
            r for r in corp_doc.custom_sub_companies
            if not (r.sub_company == doc.name and corp_name not in current_corporates)
        ]
        if len(corp_doc.custom_sub_companies) != before:
            corp_doc.save(ignore_permissions=True)

    frappe.flags.skip_client_shareholder_sync = False


def validate_shareholding_total(doc, method):
    """Ensure total shareholding adds up to 100%."""
    if not doc.custom_shareholders:
        # No shareholders to validate
        return

    total = sum(row.shareholding_pct or 0 for row in doc.custom_shareholders)
    
    if round(total, 2) != 100.0:
        frappe.throw(f"Total shareholding must be exactly 100%. Current total: {total}%")



def update_remaining_quota(doc, method=None):
    if getattr(frappe.flags, "skip_quota_update", False):
        return
    frappe.flags.skip_quota_update = True
    
    used = len(doc.custom_visa_holders or [])
    quota = doc.custom_no_of_visa_quota or 0
    remaining = quota - used
    
    if remaining < 0:
        frappe.msgprint(
            f"Warning: Visa quota exceeded by {-remaining}",
            alert=True,
            indicator="orange"
        )
    doc.custom_no_of_remaining_quota = remaining

def is_tax_user(doc, method):
    if doc.get("custom_show_tax_credentials") and "Tax Support" not in frappe.get_roles():
        frappe.throw("You must be in Tax Support role to view tax credentials")

def set_tax_filing_status(doc):
    """Auto-set tax status if due date year matches current year"""
    if not doc.custom_corporate_tax_next_filing_due_date:
        return
    
    # Don't override if already in progress
    if doc.custom_corporate_tax_status == "Filing In Progress":
        return
        
    due_date = getdate(doc.custom_corporate_tax_next_filing_due_date)
    current_year = now_datetime().year
    
    if due_date.year == current_year:
        doc.custom_corporate_tax_status = "Filing Pending"
    elif doc.custom_corporate_tax_status == "Filing Pending":
        # Reset if year changed
        doc.custom_corporate_tax_status = None

def update_all_tax_statuses():
    """Weekly check for all customers"""
    current_year = now_datetime().year
    
    customers = frappe.get_all("Customer", 
        filters={
            "custom_corporate_tax_next_filing_due_date": ["is", "set"],
            "custom_corporate_tax_status": ["not in", ["Filing Pending", "Filing In Progress"]]
        },
        fields=["name", "custom_corporate_tax_next_filing_due_date"]
    )
    
    for cust in customers:
        due_date = getdate(cust.custom_corporate_tax_next_filing_due_date)
        if due_date.year == current_year:
            frappe.db.set_value("Customer", cust.name, 
                "custom_corporate_tax_status", "Filing Pending")

def set_vat_filing_status(doc):
    """Auto-set VAT status if due date month/year matches current month/year"""
    if not doc.custom_vat_next_filing_due_date or doc.custom_vat_status == "Filing In Progress":
        return
        
    due_date = getdate(doc.custom_vat_next_filing_due_date)
    current_date = now_datetime()
    
    # Check if due date is in current month/year and not past due
    if (due_date.month == current_date.month and 
        due_date.year == current_date.year and
        due_date >= current_date.date()):
        doc.custom_vat_status = "Filing Pending"
    elif doc.custom_vat_status == "Filing Pending":
        doc.custom_vat_status = None  # Reset if no longer current

def update_all_vat_statuses():
    """Weekly check for VAT filings"""
    current_date = now_datetime()
    next_week = add_to_date(current_date, days=7, as_string=True)
    
    customers = frappe.get_all("Customer", 
        filters=[
            ["custom_vat_next_filing_due_date", "between", [current_date, next_week]],
            ["custom_vat_status", "not in", ["Filing Pending", "Filing In Progress"]]
        ],
        fields=["name", "custom_vat_next_filing_due_date"]
    )
    
    for cust in customers:
        frappe.db.set_value("Customer", cust.name, 
            "custom_vat_status", "Filing Pending")