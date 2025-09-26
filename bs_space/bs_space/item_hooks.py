import frappe

def set_item_code(doc, method):
	# Define prefix map
	prefix_map = {
		"License Services": "LIC",
		"Visa Services": "VIS",
		"Accounting & Tax Services": "ACC",
		"Other Services": "OTH",
		"Non-Service Item": "NSI"
	}

	# Normalize item_type
	item_type_cleaned = (doc.get("custom_item_type") or "").strip()
	prefix = prefix_map.get(item_type_cleaned, "SER")

	# Start at 1 and increment until unique code is found
	suffix = 1
	while True:
		new_code = f"{prefix}-{str(suffix).zfill(3)}"
		if not frappe.db.exists("Item", {"item_code": new_code}):
			break
		suffix += 1

	# Forcefully assign the generated code
	doc.item_code = new_code
	doc.name = new_code  # Optional: only if you want item_code = docname
