#!/usr/bin/env python3
"""Discover two scoped NWDAFs and own their subscription resources."""

import argparse
import json
import os
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
    parser.add_argument("command", choices=("run", "delete", "status"))
    args = parser.parse_args()
    config = load_yaml(args.config)
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
