#!/usr/bin/env python3
"""Discover two scoped NWDAFs and own their subscription resources."""

import argparse
import ipaddress
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

import yaml


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def require_mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError("{} must be a mapping".format(label))
    return value


def require_exact_keys(value, label, expected):
    value = require_mapping(value, label)
    missing = sorted(set(expected) - set(value))
    unknown = sorted(set(value) - set(expected))
    if missing:
        raise ValueError("{} missing fields: {}".format(label, ", ".join(missing)))
    if unknown:
        raise ValueError("{} unknown fields: {}".format(label, ", ".join(unknown)))
    return value


def require_non_empty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(label))
    return value.strip()


def require_http_uri(value, label, require_path=False, require_port=False):
    value = require_non_empty_string(value, label)
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("{} must be an absolute HTTP URI".format(label))
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("{} has an invalid port: {}".format(label, error))
    if port is not None and not 0 < port < 65536:
        raise ValueError("{} has an invalid port".format(label))
    if require_port and port is None:
        raise ValueError("{} must include an explicit port".format(label))
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("{} must not contain credentials, query, or fragment".format(label))
    if require_path and (not parsed.path or parsed.path == "/"):
        raise ValueError("{} must include a callback path".format(label))
    return value


def validate_config(config):
    config = require_exact_keys(
        config,
        "config",
        {
            "nrfUri",
            "requesterNfType",
            "discovery",
            "target",
            "callback",
            "reporting",
            "stateFile",
        },
    )
    require_http_uri(config["nrfUri"], "nrfUri")
    require_non_empty_string(config["requesterNfType"], "requesterNfType")

    discovery = require_exact_keys(
        config["discovery"],
        "discovery",
        {"targetNfType", "serviceName", "event"},
    )
    for field in ("targetNfType", "serviceName", "event"):
        require_non_empty_string(discovery[field], "discovery." + field)

    target = require_exact_keys(
        config["target"],
        "target",
        {"plmn", "internalGroupId", "paths"},
    )
    plmn = require_exact_keys(target["plmn"], "target.plmn", {"mcc", "mnc"})
    if not re.fullmatch(r"[0-9]{3}", str(plmn["mcc"])):
        raise ValueError("target.plmn.mcc must contain three digits")
    if not re.fullmatch(r"[0-9]{2,3}", str(plmn["mnc"])):
        raise ValueError("target.plmn.mnc must contain two or three digits")
    require_non_empty_string(target["internalGroupId"], "target.internalGroupId")
    paths = target["paths"]
    if not isinstance(paths, list) or not paths:
        raise ValueError("target.paths must be a non-empty list")
    names = []
    for index, path in enumerate(paths):
        label = "target.paths[{}]".format(index)
        path = require_exact_keys(path, label, {"name", "tac"})
        names.append(require_non_empty_string(path["name"], label + ".name"))
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", str(path["tac"])):
            raise ValueError("{}.tac must contain six hexadecimal digits".format(label))
    if len(names) != len(set(names)):
        raise ValueError("target.paths names must be unique")

    callback = require_exact_keys(
        config["callback"],
        "callback",
        {"bindAddress", "advertisedUri"},
    )
    try:
        ipaddress.ip_address(callback["bindAddress"])
    except (TypeError, ValueError):
        raise ValueError("callback.bindAddress must be an IP address")
    require_http_uri(
        callback["advertisedUri"],
        "callback.advertisedUri",
        require_path=True,
        require_port=True,
    )

    reporting = require_exact_keys(
        config["reporting"],
        "reporting",
        {"method", "periodSeconds"},
    )
    if reporting["method"] != "PERIODIC":
        raise ValueError("reporting.method must be PERIODIC")
    period = reporting["periodSeconds"]
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError("reporting.periodSeconds must be a positive integer")

    state_file = require_non_empty_string(config["stateFile"], "stateFile")
    state_path = Path(state_file)
    if not state_path.is_absolute():
        raise ValueError("stateFile must be absolute")
    state_root = Path("/var/lib/5g-nwdaf-infrastructure/consumer")
    if state_path == state_root or state_root not in state_path.parents:
        raise ValueError("stateFile must remain inside {}".format(state_root))
    return config


def request_json(method, url, body=None, timeout=30):
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=payload, method=method)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            value = json.loads(raw) if raw else None
            return response.status, dict(response.headers), value
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError("{} {} returned {}: {}".format(method, url, error.code, detail))
    except URLError as error:
        raise RuntimeError("{} {} failed: {}".format(method, url, error.reason))


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {"status": "empty", "subscriptions": [], "notificationCount": 0}
            return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, value):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".subscriptions-", dir=str(self.path.parent))
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def record_notification(self, body):
        value = self.read()
        items = body if isinstance(body, list) else [body]
        correlations = sorted({str(item.get("notifCorrId")) for item in items if isinstance(item, dict) and item.get("notifCorrId")})
        value["notificationCount"] = int(value.get("notificationCount", 0)) + 1
        value["lastNotificationAt"] = now()
        value["lastNotificationCorrelations"] = correlations
        self.write(value)


def tracking_areas(profile):
    areas = set()
    info = profile.get("nwdafInfo") or {}
    for analytics in info.get("mlAnalyticsList") or []:
        if "UE_COMMUNICATION" not in (analytics.get("mlAnalyticsIds") or []):
            continue
        for tai in analytics.get("trackingAreaList") or []:
            plmn = tai.get("plmnId") or {}
            areas.add((str(plmn.get("mcc", "")), str(plmn.get("mnc", "")), str(tai.get("tac", "")).zfill(6)))
    return areas


def events_service(profile, service_name):
    for service in profile.get("nfServices") or []:
        if service.get("serviceName") == service_name and service.get("nfServiceStatus", "REGISTERED") == "REGISTERED":
            return service
    return None


def service_api_root(service):
    prefix = str(service.get("apiPrefix") or "").rstrip("/")
    if not prefix:
        endpoints = service.get("ipEndPoints") or []
        if not endpoints:
            raise RuntimeError("discovered service has neither apiPrefix nor ipEndPoints")
        endpoint = endpoints[0]
        host = endpoint.get("ipv4Address") or endpoint.get("ipv6Address")
        if not host:
            raise RuntimeError("discovered service endpoint has no IP address")
        prefix = "{}://{}:{}".format(service.get("scheme", "http").lower(), host, endpoint.get("port", 80))
    suffix = "/nnwdaf-eventssubscription/v1"
    return prefix if prefix.endswith(suffix) else prefix + suffix


def discover(config):
    discovery = config["discovery"]
    query = urlencode({
        "target-nf-type": discovery.get("targetNfType", "NWDAF"),
        "requester-nf-type": config.get("requesterNfType", "AF"),
        "service-names": discovery["serviceName"],
    })
    _, _, document = request_json("GET", config["nrfUri"].rstrip("/") + "/nnrf-disc/v1/nf-instances?" + query)
    profiles = document.get("nfInstances", []) if isinstance(document, dict) else []
    selected = []
    used = set()
    plmn = config["target"]["plmn"]
    for path in config["target"]["paths"]:
        wanted = (str(plmn["mcc"]), str(plmn["mnc"]), str(path["tac"]).zfill(6))
        matches = []
        for profile in profiles:
            instance_id = profile.get("nfInstanceId")
            service = events_service(profile, discovery["serviceName"])
            if instance_id and instance_id not in used and service and wanted in tracking_areas(profile):
                matches.append((instance_id, profile, service))
        if len(matches) != 1:
            raise RuntimeError("path {} expected exactly one unused NWDAF, found {}".format(path["name"], len(matches)))
        instance_id, _profile, service = matches[0]
        used.add(instance_id)
        selected.append({"path": path["name"], "tac": path["tac"], "nfInstanceId": instance_id, "apiRoot": service_api_root(service)})
    if len({item["nfInstanceId"] for item in selected}) != len(selected):
        raise RuntimeError("NRF discovery did not select distinct NWDAFs")
    return selected


def normalize_location(location, api_root):
    absolute = urljoin(api_root.rstrip("/") + "/", location)
    expected, actual = urlparse(api_root), urlparse(absolute)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        raise RuntimeError("NWDAF returned a cross-origin Location")
    return absolute


def create_one(config, candidate):
    callback = config["callback"]["advertisedUri"]
    correlation = "5g-nwdaf-{}-{}".format(candidate["path"], uuid.uuid4().hex[:12])
    plmn = config["target"]["plmn"]
    payload = {
        "notificationURI": callback,
        "notifCorrId": correlation,
        "eventSubscriptions": [{
            "event": config["discovery"]["event"],
            "tgtUe": {"intGroupIds": [config["target"]["internalGroupId"]]},
            "networkArea": {"tais": [{"plmnId": dict(plmn), "tac": candidate["tac"]}]},
        }],
        "evtReq": {"notifMethod": config["reporting"]["method"], "repPeriod": config["reporting"]["periodSeconds"]},
    }
    endpoint = candidate["apiRoot"].rstrip("/") + "/subscriptions"
    status, headers, _ = request_json("POST", endpoint, payload)
    if status != 201 or not headers.get("Location"):
        raise RuntimeError("NWDAF {} create returned {} without Location".format(candidate["nfInstanceId"], status))
    result = dict(candidate)
    result.update({"correlationId": correlation, "location": normalize_location(headers["Location"], candidate["apiRoot"]), "status": "active", "createdAt": now()})
    return result


def delete_location(location):
    status, _, _ = request_json("DELETE", location)
    if status != 204:
        raise RuntimeError("DELETE {} returned {}".format(location, status))


def create_all(config, store):
    existing = store.read()
    if existing.get("status") == "active" and len(existing.get("subscriptions", [])) == 2:
        return existing
    created = []
    try:
        for candidate in discover(config):
            created.append(create_one(config, candidate))
        value = {"status": "active", "createdAt": now(), "subscriptions": created, "notificationCount": 0}
        store.write(value)
        return value
    except Exception:
        for item in reversed(created):
            try:
                delete_location(item["location"])
            except Exception:
                pass
        raise


def delete_all(store):
    value = store.read()
    errors = []
    for item in value.get("subscriptions", []):
        if item.get("status") != "active":
            continue
        try:
            delete_location(item["location"])
            item["status"] = "deleted"
            item["deletedAt"] = now()
        except Exception as error:
            errors.append(str(error))
    value["status"] = "delete-failed" if errors else "stopped"
    value["lastError"] = errors or None
    store.write(value)
    if errors:
        raise RuntimeError("; ".join(errors))


def callback_handler(store, expected_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != expected_path:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4 * 1024 * 1024:
                    raise ValueError("invalid callback body length")
                body = json.loads(self.rfile.read(length))
                store.record_notification(body)
                self.send_response(204)
                self.end_headers()
            except Exception as error:
                self.send_error(400, str(error))

        def log_message(self, pattern, *args):
            print("callback " + pattern % args, flush=True)

    return Handler


def run(config, store):
    callback = config["callback"]
    parsed = urlparse(callback["advertisedUri"])
    server = ThreadingHTTPServer((callback["bindAddress"], parsed.port), callback_handler(store, parsed.path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        create_all(config, store)
        print(json.dumps(store.read(), sort_keys=True), flush=True)
        while worker.is_alive():
            time.sleep(1)
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("command", choices=("run", "delete", "status", "validate"))
    args = parser.parse_args()
    try:
        config = validate_config(load_yaml(args.config))
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.error(str(error))
    if args.command == "validate":
        print("OK config={}".format(Path(args.config).resolve()))
        return 0
    store = StateStore(config["stateFile"])
    if args.command == "run":
        run(config, store)
    elif args.command == "delete":
        delete_all(store)
    else:
        print(json.dumps(store.read(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
