# Clean Up Uploaded CSVs

I get CSV files uploaded via webhook from our internal tool. The data is messy —
phone numbers in different formats, extra whitespace everywhere, some rows
missing required fields (name and email).

I need a workflow that takes the CSV, cleans up the phone numbers to a standard
format, trims whitespace, drops any rows missing name or email, and sends back
the cleaned data as JSON in the webhook response.

This runs maybe 10-50 rows at a time. Low stakes, internal tool only.
