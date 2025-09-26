import frappe
from frappe.utils import today, getdate

def update_all_license_statuses():
    """Properly update license status with correct date comparison"""
    customers = frappe.get_all("Customer", 
        fields=["name", "custom_license_expiry_date", "custom_license_expiry_notifications"])
    
    current_date = getdate(today())  # Get date object for comparison
    
    for c in customers:
        if not c.custom_license_expiry_notifications:
            continue
            
        if not c.custom_license_expiry_date:
            continue
            
        expiry_date = getdate(c.custom_license_expiry_date)  # Convert to date object
        status = "Expired" if expiry_date < current_date else "Active"
        
        frappe.db.set_value("Customer", c.name, "custom_status", status)