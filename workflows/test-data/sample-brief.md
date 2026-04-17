# Webhook Echo Workflow

I need a simple workflow that receives a POST request with a JSON body
containing "name" (string) and "value" (number 0-100).

It should validate that name is non-empty and value is in range. If valid,
echo back the input with status "ok" and a timestamp. If invalid, return
status "error" with a message explaining what's wrong.

This is for testing — low stakes. No external APIs, no credentials needed.
The trigger is a webhook at path "echo-test".
