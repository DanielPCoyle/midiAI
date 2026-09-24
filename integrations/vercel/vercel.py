#!/usr/bin/env python3
"""The Vercel adapter for midiAI's integrations contract (see integrations.py
at the repo root). stdlib only -- this runs as a subprocess integrations.call()
execs, with no guarantee its interpreter has `requests` installed.

Reads one JSON object on stdin: {"op", "args", "secrets": {"token": "..."},
"config": {"team": "..."}}. Writes one JSON object to stdout:
{"ok": true, "data": ...} or {"ok": false, "error": "..."}. Never logs the
token, never echoes it back.

Endpoints verified against vercel.com/docs/rest-api on 2026-09-23/24:
  GET   /v2/user                                    -- auth.check
  GET   /v10/projects                                -- resources.list
  GET   /v7/deployments?projectId=                   -- deploy.list
  GET   /v13/deployments/{id}?withGitRepoInfo=true    -- deploy.get
  GET   /v3/deployments/{id}/events                   -- deploy.get's log tail
  POST  /v10/projects/{projectId}/promote/{id}        -- deploy.promote
  POST  /v1/projects/{projectId}/rollback/{id}        -- deploy.rollback
  PATCH /v12/deployments/{id}/cancel                  -- deploy.cancel

Two of these differ from the spec that named this file: resources.list is
/v10/projects (not /v9), and deploy.list is /v7/deployments (not /v6) --
those are the currently-documented versions of endpoints that have moved
before. `meta.*` git fields (sha/branch/message/author) aren't individually
named in Vercel's own schema (it types `meta` as a bare string map), so
_git_field reads the conventional githubCommit*/gitlabCommit*/bitbucketCommit*
keys defensively rather than asserting one is *the* field name.

"current" (which deployment the production alias is actually serving) has no
cheap flag in the API either -- readySubstate=PROMOTED means "has seen
production traffic; ever", not "is serving it now". Per the spec's fallback,
this uses the newest READY production deployment.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.vercel.com"
TIMEOUT = 20

_urlopen = urllib.request.urlopen   # tests swap this

_READY_STATES = {"READY": "ready", "BUILDING": "building", "ERROR": "error",
                 "CANCELED": "canceled", "QUEUED": "queued", "INITIALIZING": "queued",
                 "BLOCKED": "error", "DELETED": "canceled"}


class ApiError(Exception):
    pass


def _tls():
    """The python.org build ships with no CA bundle until someone runs its
    "Install Certificates" script; macOS's own bundle is at /etc/ssl/cert.pem
    (same fallback access.py uses)."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


def _request(method, path, token, team=None, query=None, timeout=TIMEOUT):
    q = dict(query or {})
    if team:
        q["teamId"] = team
    qs = ("?" + urllib.parse.urlencode(q)) if q else ""
    req = urllib.request.Request(f"{API}{path}{qs}", method=method,
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with _urlopen(req, timeout=timeout, context=_tls()) as r:
            body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8", "replace"))
            err = payload.get("error")
            detail = err.get("message") if isinstance(err, dict) else str(payload)
        except Exception:
            detail = e.reason or ""
        raise ApiError(f"{e.code} {detail}".strip())
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ApiError(str(e))
    except json.JSONDecodeError:
        raise ApiError("Vercel returned an unparseable response")


# ------------------------------------------------------------- normalising

def _meta_field(meta, *keys):
    for k in keys:
        v = (meta or {}).get(k)
        if v:
            return v
    return ""


def _norm_deploy(d):
    raw_state = str(d.get("readyState") or d.get("state") or "").upper()
    state = _READY_STATES.get(raw_state, "queued")
    env = "production" if d.get("target") == "production" else "preview"
    meta = d.get("meta") or {}
    sha = _meta_field(meta, "githubCommitSha", "gitlabCommitSha", "bitbucketCommitSha")
    branch = _meta_field(meta, "githubCommitRef", "gitlabCommitRef", "bitbucketCommitRef")
    message = _meta_field(meta, "githubCommitMessage", "gitlabCommitMessage",
                          "bitbucketCommitMessage")
    author = _meta_field(meta, "githubCommitAuthorName", "gitlabCommitAuthorName",
                         "bitbucketCommitAuthorName", "githubCommitAuthorLogin")
    return {
        "id": d.get("uid") or d.get("id") or "",
        "url": d.get("url") or "",
        "state": state,
        "env": env,
        "sha": sha,
        "message": message,
        "branch": branch,
        "author": author,
        "created": d.get("created") if d.get("created") is not None else d.get("createdAt"),
        "ready": d.get("ready") if d.get("ready") is not None else d.get("readyAt"),
        "current": False,       # filled in by _mark_current over the whole list
    }


def _mark_current(rows):
    """No cheap "is this what production serves right now" flag exists --
    fall back to the newest READY production deployment, as the spec allows."""
    prod_ready = [r for r in rows if r["env"] == "production" and r["state"] == "ready"]
    if not prod_ready:
        return
    newest = max(prod_ready, key=lambda r: r.get("ready") or r.get("created") or 0)
    newest["current"] = True


# --------------------------------------------------------------------- ops

def _op_auth_check(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    data = _request("GET", "/v2/user", token, team=config.get("team"))
    user = data.get("user") or {}
    return {"user": user.get("username") or user.get("name") or user.get("id") or "",
            "email": user.get("email") or ""}


def _op_resources_list(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    data = _request("GET", "/v10/projects", token, team=config.get("team"))
    projects = data.get("projects") if isinstance(data, dict) else data
    if not isinstance(projects, list):
        projects = []
    return [{"id": p.get("id") or "", "name": p.get("name") or "",
            "framework": p.get("framework") or ""} for p in projects]


def _list_deployments(token, team, project_id, extra=None, limit=20):
    query = {"projectId": project_id, "limit": limit}
    query.update(extra or {})
    data = _request("GET", "/v7/deployments", token, team=team, query=query)
    return data.get("deployments") or []


def _op_deploy_list(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    resource = args.get("resource")
    if not resource:
        raise ApiError("no project mapped")
    limit = args.get("limit") or 20
    rows = [_norm_deploy(d) for d in _list_deployments(token, config.get("team"), resource,
                                                        limit=limit)]
    _mark_current(rows)
    return rows


def _op_deploy_get(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    did = args.get("id")
    if not did:
        raise ApiError("no deployment id")
    d = _request("GET", "/v13/deployments/" + urllib.parse.quote(str(did), safe=""), token,
                team=config.get("team"), query={"withGitRepoInfo": "true"})
    row = _norm_deploy(d)
    project_id = d.get("projectId") or ""
    if project_id and row["env"] == "production" and row["state"] == "ready":
        siblings = [_norm_deploy(s) for s in _list_deployments(
            token, config.get("team"), project_id, extra={"target": "production"}, limit=5)]
        _mark_current(siblings)
        row["current"] = any(s["id"] == row["id"] and s["current"] for s in siblings)
    events = _request("GET", "/v3/deployments/" + urllib.parse.quote(str(did), safe="") + "/events",
                      token, team=config.get("team"), query={"limit": 80, "direction": "backward"})
    lines = []
    if isinstance(events, list):
        for e in events:
            text = e.get("text") if isinstance(e, dict) else None
            if text:
                lines.append(text)
        lines.reverse()   # backward -> chronological
    row["log"] = lines[-80:]
    return row


def _op_deploy_promote(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    resource, did = args.get("resource"), args.get("id")
    if not resource or not did:
        raise ApiError("resource and id are required")
    _request("POST", f"/v10/projects/{urllib.parse.quote(str(resource), safe='')}"
             f"/promote/{urllib.parse.quote(str(did), safe='')}", token, team=config.get("team"))
    return {"promoted": did}


def _op_deploy_rollback(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    resource, did = args.get("resource"), args.get("id")
    if not resource or not did:
        raise ApiError("resource and id are required")
    _request("POST", f"/v1/projects/{urllib.parse.quote(str(resource), safe='')}"
             f"/rollback/{urllib.parse.quote(str(did), safe='')}", token, team=config.get("team"))
    return {"rolledback": did}


def _op_deploy_cancel(args, secrets, config):
    token = secrets.get("token")
    if not token:
        raise ApiError("no Vercel token configured")
    did = args.get("id")
    if not did:
        raise ApiError("no deployment id")
    _request("PATCH", "/v12/deployments/" + urllib.parse.quote(str(did), safe="") + "/cancel",
             token, team=config.get("team"))
    return {"canceled": did}


_OPS = {
    "auth.check": _op_auth_check,
    "resources.list": _op_resources_list,
    "deploy.list": _op_deploy_list,
    "deploy.get": _op_deploy_get,
    "deploy.promote": _op_deploy_promote,
    "deploy.rollback": _op_deploy_rollback,
    "deploy.cancel": _op_deploy_cancel,
}


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "bad request"}))
        return
    op = payload.get("op")
    args = payload.get("args") or {}
    secrets = payload.get("secrets") or {}
    config = payload.get("config") or {}
    fn = _OPS.get(op)
    if not fn:
        print(json.dumps({"ok": False, "error": f"unknown op: {op}"}))
        return
    try:
        print(json.dumps({"ok": True, "data": fn(args, secrets, config)}))
    except ApiError as e:
        print(json.dumps({"ok": False, "error": str(e)[:500]}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"unexpected: {e}"[:500]}))


if __name__ == "__main__":
    main()
