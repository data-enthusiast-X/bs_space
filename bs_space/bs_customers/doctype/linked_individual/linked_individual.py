# Copyright (c) 2025, Best Solution® and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, today, getdate
from datetime import date
from frappe import _

class LinkedIndividual(Document):
    def validate(self):
        validate_parent_relationship(self)
        update_document_status(self)

def validate_linked_individual(doc, method=None):
    """Main validation function for Linked Individual"""
    validate_parent_relationship(doc)
    validate_dependents(doc)
    validate_owned_companies(doc)

CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE = "Visa Holders of Client"
DEPENDENTS_CHILD_DOTYPE = "Dependents of Individual"

def _resolve_customer_visa_holders_fieldname() -> str | None:
    """Return the child Table fieldname on Customer that points to Visa Holders of Client."""
    try:
        meta = frappe.get_meta("Customer")
        # exact match by child doctype
        for df in meta.get_table_fields():
            if (df.options or "").strip() == CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE:
                return df.fieldname
        # common fallback if you actually named it custom_visa_holders
        if meta.get_field("custom_visa_holders"):
            return "custom_visa_holders"
        # last resort: None (we'll log and skip)
    except Exception:
        pass
    return None
    

def after_save_linked_individual(doc, method=None):
    if getattr(frappe.flags, "li_after_save_ran", False):
        return
    frappe.flags.li_after_save_ran = True

    # If visa tracking is off, remove from both sides and stop
    if not getattr(doc, "has_visa", 0):
        _remove_dependent_from_all_li_parents(doc.name)
        _remove_visa_holder_from_all_customers(doc.name)
        return

    frappe.logger().info(f"[LI after_save] {doc.name} vt={doc.visa_type} pt={doc.parent_type} vp={doc.visa_parent}")
    try:
        sync_linked_individual_visa_parent(doc, method=method)   # non-Dependents → Customer
    except Exception as e:
        frappe.log_error(f"[LI after_save] Customer sync failed for {doc.name}: {frappe.as_json(str(e))}")
    try:
        sync_linked_individual_dependents(doc, method=method)    # Dependents → LI
        frappe.logger().info(f"[LI after_save] Dependent sync executed for {doc.name}")
    except Exception as e:
        frappe.log_error(f"[LI after_save] Dependent sync failed for {doc.name}: {frappe.as_json(str(e))}")



def validate_parent_relationship(doc):

    # Rules:
    # - If visa_type == 'Dependent':
    #     parent_type must be 'Linked Individual'
    #     visa_parent must be a Linked Individual (not self, not a Dependent)
    #     and no circular parent (their parent cannot be me).
    # - Else (non-Dependent):
    #     parent_type must be 'Customer'
    #     visa_parent must be a valid Customer.

    visa_type = (doc.visa_type or "").strip()
    parent_type = (doc.parent_type or "").strip()
    parent = getattr(doc, "visa_parent", None)

    if visa_type == "Dependent":
        if parent_type != "Linked Individual":
            frappe.throw(_("Dependents must have Parent Type = Linked Individual."))

        if not parent:
            frappe.throw(_("Please set Visa Parent (Linked Individual)."))

        if parent == doc.name:
            frappe.throw(_("Visa Parent cannot be the same as this individual."))

        parent_doc = frappe.db.get_value(
            "Linked Individual", parent, ["name", "visa_type"], as_dict=True
        )
        if not parent_doc:
            frappe.throw(_("Visa Parent must be an existing Linked Individual."))

        if (parent_doc.get("visa_type") or "") == "Dependent":
            frappe.throw(_("A Dependent cannot be selected as a Visa Parent."))

        # circular check: parent's parent cannot be me
        parents_parent = frappe.db.get_value("Linked Individual", parent, "visa_parent")
        if parents_parent == doc.name:
            frappe.throw(_("Circular visa parent relationship is not allowed."))

    else:
        # Non-dependent → must be under a Customer
        if parent_type != "Customer":
            frappe.throw(_("Non-dependents must have Parent Type = Customer."))

        if not parent:
            frappe.throw(_("Please set Visa Parent (Customer)."))

        if not frappe.db.exists("Customer", parent):
            frappe.throw(_("Visa Parent '{0}' is not a valid Customer.").format(parent))

def update_document_status(doc):
    """Set status from visa flags & expiry date safely."""
    if not getattr(doc, "has_visa", 0):
        doc.status = "Not Applicable"
        return

    expiry = None
    if getattr(doc, "visa_expiry_date", None):
        try:
            expiry = getdate(doc.visa_expiry_date)  # str -> date
        except Exception:
            expiry = None

    today = getdate()
    if not expiry:
        doc.status = "Active"
        return

    doc.status = "Expired" if expiry < today else "Active"

def validate_dependents(doc):
    """Check for duplicate dependents in child table"""
    seen = set()
    for row in doc.dependents or []:
        if row.dependent in seen:
            frappe.throw(f"Dependent '{row.dependent}' is listed more than once.")
        seen.add(row.dependent)

def validate_owned_companies(doc):
    """Check for duplicate companies and total shareholding %"""
    seen = set()
    for row in doc.owned_companies or []:
        if row.company in seen:
            frappe.throw(f"Company '{row.company}' is listed more than once in Owned Companies.")
        seen.add(row.company)

        if row.shareholding_pct and (row.shareholding_pct < 0 or row.shareholding_pct > 100):
            frappe.throw(f"Shareholding % for '{row.company}' must be between 0 and 100.")


def update_document_status(doc, method=None):
    """
    Sets status based on visa flags and expiry date.
    Expected status options: Not Applicable, Active, Expired (add others if you use them)
    """
    # No visa tracking → Not Applicable
    if not getattr(doc, "has_visa", 0):
        doc.status = "Not Applicable"
        return

    # Convert to date objects
    expiry = None
    if getattr(doc, "visa_expiry_date", None):
        try:
            expiry = getdate(doc.visa_expiry_date)  # str -> date
        except Exception:
            expiry = None  # invalid string; treat as unknown

    today = getdate()  # date object for "now" (no args returns today)

    # If no expiry date provided, treat as Active (or change to "Pending Expiry Date" if you have that)
    if not expiry:
        return

    # Compare dates safely
    doc.status = "Expired" if expiry < today else "Active"


def send_expiry_notifications():
    """Daily job to notify about upcoming document expiries."""
    days_before_expiry = 30  # notify 30 days before
    notification_date = add_days(today(), days_before_expiry)

    # Fetch Linked Individuals with notifications enabled
    individuals = frappe.get_all(
        "Linked Individual",
        filters={"enabled": 1, "need_expiry_notifications": 1},
        fields=[
            "name",
            "full_name",
            "email",
            "visa_expiry_date",
            "passport_expiry_date",
            "emirates_id_expiry_date"
        ]
    )

    for ind in individuals:
        messages = []

        # Visa check
        if ind.visa_expiry_date and getdate(ind.visa_expiry_date) <= getdate(notification_date):
            messages.append(f"Visa expires on {ind.visa_expiry_date}")

        # Passport check
        if ind.passport_expiry_date and getdate(ind.passport_expiry_date) <= getdate(notification_date):
            messages.append(f"Passport expires on {ind.passport_expiry_date}")

        # Emirates ID check
        if ind.emirates_id_expiry_date and getdate(ind.emirates_id_expiry_date) <= getdate(notification_date):
            messages.append(f"Emirates ID expires on {ind.emirates_id_expiry_date}")

        # Send email if any expiry is near
        if messages:
            send_expiry_email(ind, messages)

def send_expiry_email(ind, messages):
    """Send expiry email to the Linked Individual's email address."""
    if not ind.email:
        return

    subject = f"Document Expiry Reminder - {ind.full_name}"
    message = f"""
    Dear {ind.full_name},

    This is a reminder that the following documents are nearing expiry:

    {chr(10).join(messages)}

    Please take necessary action to renew them before the expiry date.

    Regards,  
    Best Solution® Team
    """

    try:
        frappe.sendmail(
            recipients=[ind.email],
            subject=subject,
            message=message
        )
    except Exception as e:
        frappe.log_error(f"Failed to send expiry email to {ind.email}: {str(e)}")



# def sync_linked_individual_visa_parent(doc, method=None):
#     # Only for NON-dependents where parent is a Customer
#     if (doc.visa_type or "").strip() == "Dependent":
#         return
#     if (doc.parent_type or "").strip() != "Customer":
#         return
#     if not (doc.visa_parent or "").strip():
#         return

#     fieldname = _resolve_customer_visa_holders_fieldname()
#     if not fieldname:
#         frappe.log_error(
#             title="Customer visa-holders table not found",
#             message="Could not resolve Customer child table for Visa Holders of Client. "
#                     "Check Customize Form > Customer."
#         )
#         return

#     if getattr(frappe.flags, "skip_visa_holder_sync", False):
#         return
#     frappe.flags.skip_visa_holder_sync = True
#     try:
#         parent_name = (doc.visa_parent or "").strip()
#         if not frappe.db.exists("Customer", parent_name):
#             return

#         current_parent = frappe.get_doc("Customer", parent_name)

#         # idempotent upsert on the resolved field
#         current_parent.set(fieldname, [
#             r for r in (current_parent.get(fieldname) or [])
#             if (getattr(r, "visa_holder", None) or "").strip() != doc.name
#         ])
#         current_parent.append(fieldname, {
#             "visa_holder": doc.name,
#             "passport_number": doc.passport_number,
#             "visa_type": doc.visa_type,
#             "visa_status": doc.status,
#             "emirates_id": getattr(doc, "emirates_id_number", None),
#         })

#         _normalize_child_idx(current_parent, fieldname)
#         current_parent.save(ignore_permissions=True)

#         # cleanup: remove from all other customers
#         for cust_name in frappe.get_all("Customer", pluck="name"):
#             if cust_name == current_parent.name:
#                 continue
#             cust_doc = frappe.get_doc("Customer", cust_name)
#             before = len(cust_doc.get(fieldname) or [])
#             cust_doc.set(fieldname, [
#                 r for r in (cust_doc.get(fieldname) or [])
#                 if (getattr(r, "visa_holder", None) or "").strip() != doc.name
#             ])
#             if len(cust_doc.get(fieldname) or []) != before:
#                 _normalize_child_idx(cust_doc, fieldname)
#                 cust_doc.save(ignore_permissions=True)
#     finally:
#         frappe.flags.skip_visa_holder_sync = False


def sync_linked_individual_visa_parent(doc, method=None):
    # Only for NON-dependents where parent is a Customer
    if (doc.visa_type or "").strip() == "Dependent":
        return
    if (doc.parent_type or "").strip() != "Customer":
        return
    if not (doc.visa_parent or "").strip():
        return

    fieldname = _resolve_customer_visa_holders_fieldname()
    if not fieldname:
        frappe.log_error(
            title="Customer visa-holders table not found",
            message="Could not resolve Customer child table for Visa Holders of Client. "
                    "Check Customize Form > Customer."
        )
        return

    if getattr(frappe.flags, "skip_visa_holder_sync", False):
        return
    frappe.flags.skip_visa_holder_sync = True
    try:
        parent_name = (doc.visa_parent or "").strip()
        if not frappe.db.exists("Customer", parent_name):
            return

        # 1) Remove this holder from ALL other customers via row-level delete
        child_dt = CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE
        rows_elsewhere = frappe.get_all(
            child_dt,
            filters={"visa_holder": doc.name, "parenttype": "Customer"},
            fields=["name", "parent"]
        )
        for r in rows_elsewhere:
            if r.parent != parent_name:
                frappe.delete_doc(child_dt, r.name, ignore_permissions=True, force=True)

        # 2) Upsert into the correct parent (delete duplicates on same parent, then append once)
        #    Do NOT rewrite the entire list.
        dup_rows = frappe.get_all(
            child_dt,
            filters={"visa_holder": doc.name, "parenttype": "Customer", "parent": parent_name},
            fields=["name"]
        )
        for r in dup_rows:
            frappe.delete_doc(child_dt, r.name, ignore_permissions=True, force=True)

        current_parent = frappe.get_doc("Customer", parent_name)
        current_parent.append(fieldname, {
            "visa_holder": doc.name,
            "passport_number": doc.passport_number,
            "visa_type": doc.visa_type,
            "visa_status": doc.status,
            "emirates_id": getattr(doc, "emirates_id_number", None),
        })
        _normalize_child_idx(current_parent, fieldname)
        current_parent.save(ignore_permissions=True)

    finally:
        frappe.flags.skip_visa_holder_sync = False



def cleanup_on_trash(doc, method):
    # Remove from visa_parent
    if doc.visa_parent:
        try:
            parent = frappe.get_doc("Customer", doc.visa_parent)
            parent.custom_visa_holders = [row for row in parent.custom_visa_holders if row.visa_holder != doc.name]
            parent.save(ignore_permissions=True)
        except:
            pass

    # Remove from shareholders
    for cust in frappe.get_all("Customer", pluck="name"):
        customer_doc = frappe.get_doc("Customer", cust)
        customer_doc.custom_shareholders = [
            row for row in customer_doc.custom_shareholders
            if not (row.shareholder_type == "Individual" and row.shareholder == doc.name)
        ]
        customer_doc.save(ignore_permissions=True)


def sync_linked_individual_dependents(doc, method=None):
    """
    Upsert this dependent into its Linked Individual parent's 'dependents' table.
    Also ensures the person is removed from all Customer visa-holder tables
    when they become a Dependent.
    """
    vt = (doc.visa_type or "").strip()
    vp = (doc.visa_parent or "").strip()

    # If this LI is (now) a Dependent, it must NOT remain on any Customer
    if vt == "Dependent":
        _remove_visa_holder_from_all_customers(doc.name)

    # Only act for real dependents with some parent selected
    if vt != "Dependent" or not vp:
        # Also remove them from any LI parents if they are not dependent anymore / no parent
        _remove_dependent_from_all_li_parents(doc.name)
        frappe.logger().info(f"[LI Dep Sync] Skipped (not dependent or no parent): {doc.name} vt={vt} vp={vp}")
        return

    if getattr(frappe.flags, "in_li_dependent_sync", False):
        return

    frappe.flags.in_li_dependent_sync = True
    try:
        # Parent must be a Linked Individual; if not, clean and exit
        if not frappe.db.exists("Linked Individual", vp):
            _remove_dependent_from_all_li_parents(doc.name)
            # Already removed from Customers above if vt == Dependent
            frappe.logger().info(f"[LI Dep Sync] Parent {vp} not an LI → cleaned rows for {doc.name}.")
            return

        # Remove rows from OTHER LI parents, and ensure no Customer rows linger
        _remove_dependent_from_all_li_parents(doc.name, skip_parent=vp)
        _remove_visa_holder_from_all_customers(doc.name)

        parent_li = frappe.get_doc("Linked Individual", vp)
        dep_field = _resolve_dependents_fieldname()
        if not dep_field:
            frappe.log_error(
                title="Dependents table missing on Linked Individual",
                message=f"Could not resolve dependents field on Linked Individual for {doc.name} → parent {vp}",
            )
            return

        # Upsert (preserve remarks)
        rows = list(parent_li.get(dep_field) or [])
        for r in rows:
            if (r.get("dependent") or "").strip() == doc.name:
                r.relation = doc.relation
                r.date_of_birth = doc.date_of_birth
                break
        else:
            parent_li.append(dep_field, {
                "dependent": doc.name,
                "relation": doc.relation,
                "date_of_birth": doc.date_of_birth,
            })

        _normalize_child_idx(parent_li, dep_field)
        parent_li.save(ignore_permissions=True)
        frappe.logger().info(f"[LI Dep Sync] Upserted {doc.name} → {vp}.{dep_field}")

    finally:
        frappe.flags.in_li_dependent_sync = False



def _resolve_dependents_fieldname() -> str | None:
    """Return the child Table fieldname on 'Linked Individual' that points to 'Dependents of Individual'."""
    try:
        meta = frappe.get_meta("Linked Individual")
        for df in meta.get_table_fields():
            if (df.options or "").strip() == DEPENDENTS_CHILD_DOTYPE:
                return df.fieldname
        if meta.get_field("dependents"):
            return "dependents"
        tfs = meta.get_table_fields()
        if tfs:
            return tfs[0].fieldname
    except Exception:
        pass
    return None


CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE = "Visa Holders of Client"  # child DocType name

def _resolve_customer_visa_holders_fieldname() -> str | None:
    """Return the child Table fieldname on Customer that points to 'Visa Holders of Client'."""
    try:
        meta = frappe.get_meta("Customer")
        # Prefer exact child doctype match
        for df in meta.get_table_fields():
            if (df.options or "").strip() == CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE:
                return df.fieldname
        # Fallback to a common custom fieldname if you used it
        if meta.get_field("custom_visa_holders"):
            return "custom_visa_holders"
    except Exception:
        pass
    return None


# def _remove_visa_holder_from_all_customers(visa_holder: str, skip_customer: str | None = None):
#     """Remove this LI from ALL Customers’ visa-holders tables (idempotent)."""
#     fieldname = _resolve_customer_visa_holders_fieldname()
#     if not fieldname:
#         frappe.log_error(
#             title="Customer visa-holders table not found (cleanup)",
#             message=f"Could not resolve Customer child table for '{CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE}'."
#         )
#         return

#     if getattr(frappe.flags, "skip_visa_holder_cleanup", False):
#         return

#     frappe.flags.skip_visa_holder_cleanup = True
#     try:
#         for cust_name in frappe.get_all("Customer", pluck="name"):
#             if skip_customer and cust_name == skip_customer:
#                 continue
#             cust_doc = frappe.get_doc("Customer", cust_name)
#             current = list(cust_doc.get(fieldname) or [])
#             cleaned = [
#                 r for r in current if (getattr(r, "visa_holder", "") or "").strip() != visa_holder
#             ]
#             if len(cleaned) != len(current):
#                 cust_doc.set(fieldname, cleaned)
#                 _normalize_child_idx(cust_doc, fieldname)
#                 cust_doc.save(ignore_permissions=True)
#     finally:
#         frappe.flags.skip_visa_holder_cleanup = False

def _remove_visa_holder_from_all_customers(visa_holder: str, skip_customer: str | None = None):
    """Remove this LI from ALL Customers’ visa-holders tables (row-level delete, no list rewrites)."""
    fieldname = _resolve_customer_visa_holders_fieldname()
    if not fieldname:
        frappe.log_error(
            title="Customer visa-holders table not found (cleanup)",
            message=f"Could not resolve Customer child table for '{CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE}'."
        )
        return

    child_dt = CUSTOMER_VISA_HOLDERS_CHILD_DOTYPE

    if getattr(frappe.flags, "skip_visa_holder_cleanup", False):
        return

    frappe.flags.skip_visa_holder_cleanup = True
    try:
        # Find exact child rows to delete
        filters = {"visa_holder": visa_holder, "parenttype": "Customer"}
        rows = frappe.get_all(child_dt, filters=filters, fields=["name", "parent"])

        # Delete only those rows; don't touch others
        for r in rows:
            if skip_customer and r.parent == skip_customer:
                continue
            frappe.delete_doc(child_dt, r.name, ignore_permissions=True, force=True)

        # Optional: reindex idx on affected parents
        affected = sorted({r.parent for r in rows if not (skip_customer and r.parent == skip_customer)})
        fn = fieldname
        for parent in affected:
            try:
                cdoc = frappe.get_doc("Customer", parent)
                _normalize_child_idx(cdoc, fn)
                cdoc.save(ignore_permissions=True)
            except Exception:
                # Non-fatal; rows are already deleted
                pass
    finally:
        frappe.flags.skip_visa_holder_cleanup = False




def _remove_dependent_from_all_li_parents(dependent_name: str, skip_parent: str | None = None):
    """Remove this dependent row from ALL Linked Individuals' dependents tables."""
    rows = frappe.get_all(
        DEPENDENTS_CHILD_DOTYPE,
        filters={"dependent": dependent_name, "parenttype": "Linked Individual"},
        fields=["name", "parent"],
    )
    if not rows:
        return

    parents = {}
    for r in rows:
        if skip_parent and r.parent == skip_parent:
            continue
        parents.setdefault(r.parent, []).append(r.name)

    dep_field = _resolve_dependents_fieldname()
    if not dep_field:
        frappe.log_error(
            title="Dependents cleanup skipped (field missing)",
            message=f"Could not resolve dependents field while cleaning rows for {dependent_name}",
        )
        return

    for parent_name, row_names in parents.items():
        try:
            li = frappe.get_doc("Linked Individual", parent_name)
        except frappe.DoesNotExistError:
            continue
        current = list(li.get(dep_field) or [])
        cleaned = [row for row in current if row.name not in row_names]
        if len(cleaned) != len(current):
            li.set(dep_field, cleaned)
            _normalize_child_idx(li, dep_field)
            li.save(ignore_permissions=True)
            frappe.logger().info(f"[LI Dep Sync] Cleaned {dependent_name} from {parent_name}.{dep_field}")

def _normalize_child_idx(parent_doc, child_fieldname: str) -> None:
    """Ensure child table rows have sequential idx starting at 1."""
    rows = list(parent_doc.get(child_fieldname) or [])
    for i, r in enumerate(rows, start=1):
        r.idx = i