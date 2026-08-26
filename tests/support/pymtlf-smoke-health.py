#!/usr/bin/env python3
"""Validate PyMTLF process health without requiring a containing NWDAF."""

import json
import os
import urllib.error
import urllib.request


url = "http://127.0.0.1:{}/health/ready".format(os.environ["SERVICE_PORT"])
try:
    response = urllib.request.urlopen(url, timeout=2)
    status = response.status
    body = response.read()
except urllib.error.HTTPError as error:
    status = error.code
    body = error.read()

payload = json.loads(body)
assert payload["runtimeMode"] == "federated", payload
assert payload["artifacts"] == "ready", payload
assert payload["capabilityVerification"] in {"verified", "unavailable"}, payload
if payload["capabilityVerification"] == "verified":
    assert status == 200 and payload["status"] == "ready", payload
else:
    assert status == 503 and payload["status"] == "not_ready", payload
