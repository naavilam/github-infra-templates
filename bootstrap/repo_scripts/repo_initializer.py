#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml

# ============================================================
# CONFIG
# ============================================================

API = "https://api.github.com"

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
if not GH_TOKEN:
    print("[ERROR] missing GH_TOKEN (or GITHUB_TOKEN)", file=sys.stderr)
    sys.exit(2)

HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

def _default_registry_file() -> Path:
    """
    Novo layout:
      org/org_registry/{org-lower}-registry.yml

    Este script roda no repo central (checkout).
    Então o path é relativo ao CWD do central.
    """

    # tenta inferir org do runtime
    org = (os.environ.get("ONE_ORG") or os.environ.get("ORG") or "").strip()
    if org:
        return Path("org") / "org_registry" / f"{org.lower()}-registry.yml"


REGISTRY_FILE = _default_registry_file()

WORKDIR_BASE = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "repo_init_work"

DISPATCH_SITE = os.environ.get("DISPATCH_SITE", "website-template-updated")
DISPATCH_README = os.environ.get("DISPATCH_README", "readme-template-updated")

# Se quiser SEMPRE disparar os 2 workflows, mesmo após push (default true aqui)
ALWAYS_DISPATCH = os.environ.get("ALWAYS_DISPATCH", "true").lower() == "true"

# Se rodar fora do Actions e precisar autenticar o clone/push via URL
USE_TOKEN_IN_URL = os.environ.get("USE_TOKEN_IN_URL", "true").lower() == "true"

BOOTSTRAP_BASE = Path("bootstrap")
DISCIPLINE_SRC = BOOTSTRAP_BASE / "repo_discipline"
WORKFLOWS_SRC = BOOTSTRAP_BASE / "repo_workflows"


# ============================================================
# LOGGING
# ============================================================

def log(repo: str, msg: str) -> None:
    print(f"[INIT] {repo}: {msg}", flush=True)

def warn(repo: str, msg: str) -> None:
    print(f"[WARN] {repo}: {msg}", flush=True)

def err(repo: str, step: str, msg: str) -> None:
    print(f"[ERROR] {repo} step={step}: {msg}", file=sys.stderr, flush=True)


# ============================================================
# HTTP / GitHub
# ============================================================

def repo_info(org: str, repo: str) -> dict:
    r = gh("GET", f"{API}/repos/{org}/{repo}")
    if r.status_code != 200:
        raise RuntimeError(f"repo_info failed {r.status_code}: {r.text}")
    return r.json()

def has_branch(org: str, repo: str, branch: str) -> bool:
    r = gh("GET", f"{API}/repos/{org}/{repo}/branches/{branch}")
    if r.status_code in (200, 404):
        return r.status_code == 200
    raise RuntimeError(f"has_branch unexpected {r.status_code}: {r.text}")

def workflow_file_exists(org: str, repo: str, branch: str, filename: str) -> bool:
    # contents API: checa arquivo específico no branch
    url = f"{API}/repos/{org}/{repo}/contents/.github/workflows/{filename}?ref={branch}"
    r = gh("GET", url)
    if r.status_code in (200, 404):
        return r.status_code == 200
    raise RuntimeError(f"workflow_file_exists unexpected {r.status_code}: {r.text}")

def missing_required_workflows(org: str, repo: str, branch: str) -> List[str]:
    required = ["listener-readme-update.yml", "listener-website-update.yml"]
    missing = []
    for wf in required:
        if not workflow_file_exists(org, repo, branch, wf):
            missing.append(wf)
    return missing
    
def gh(method: str, url: str, **kw) -> requests.Response:
    return requests.request(method, url, headers=HEADERS, timeout=30, **kw)

def repo_exists(org: str, repo: str) -> bool:
    r = gh("GET", f"{API}/repos/{org}/{repo}")
    if r.status_code in (200, 404):
        return r.status_code == 200
    raise RuntimeError(f"repo_exists unexpected {r.status_code}: {r.text}")

def create_repo(org: str, repo: str, desc: str, private: bool) -> None:
    payload = {
        "name": repo,
        "description": desc,
        "private": private,
        "auto_init": True,  # garante origin/main existir
        "has_issues": False,
        "has_projects": False,
        "has_wiki": False,
    }
    r = gh("POST", f"{API}/orgs/{org}/repos", json=payload)
    if r.status_code in (201, 422):
        return
    raise RuntimeError(f"create_repo failed {r.status_code}: {r.text}")

def dispatch(org: str, repo: str, event: str) -> None:
    r = gh("POST", f"{API}/repos/{org}/{repo}/dispatches", json={"event_type": event})
    if r.status_code != 204:
        raise RuntimeError(f"dispatch failed {r.status_code}: {r.text}")


# ============================================================
# GIT helpers
# ============================================================

def run(cmd: List[str], cwd: Optional[Path] = None) -> None:
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None)

def out(cmd: List[str], cwd: Optional[Path] = None) -> str:
    return subprocess.check_output(cmd, cwd=str(cwd) if cwd else None).decode().strip()

def ensure_repo_clone(org: str, repo: str) -> Path:
    d = WORKDIR_BASE / f"{org}__{repo}"
    if d.exists():
        shutil.rmtree(d)
    d.parent.mkdir(parents=True, exist_ok=True)

    clean_url = f"https://github.com/{org}/{repo}.git"
    token_url = f"https://x-access-token:{GH_TOKEN}@github.com/{org}/{repo}.git"
    clone_url = token_url if USE_TOKEN_IN_URL else clean_url

    run(["git", "clone", "--no-tags", clone_url, str(d)])
    run(["git", "remote", "set-url", "origin", token_url if USE_TOKEN_IN_URL else clean_url], cwd=d)
    run(["git", "fetch", "origin", "--prune"], cwd=d)

    # identidade local (necessário para commit)
    run(["git", "config", "user.name", os.environ.get("GIT_USER_NAME", "github-actions[bot]")], cwd=d)
    run(["git", "config", "user.email", os.environ.get("GIT_USER_EMAIL", "github-actions[bot]@users.noreply.github.com")], cwd=d)

    return d

def ensure_main_checkout(repo_dir: Path) -> None:
    # tenta trazer origin/main, se existir
    try:
        run(["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"], cwd=repo_dir)
        run(["git", "checkout", "-B", "main", "origin/main"], cwd=repo_dir)
        run(["git", "reset", "--hard", "origin/main"], cwd=repo_dir)
    except subprocess.CalledProcessError:
        # não existe origin/main: cria main (a partir do HEAD atual)
        run(["git", "checkout", "-B", "main"], cwd=repo_dir)

    run(["git", "clean", "-ffd"], cwd=repo_dir)

def install_required_workflows_only(repo_dir: Path) -> bool:
    if not WORKFLOWS_SRC.exists():
        raise RuntimeError(f"missing workflows source: {WORKFLOWS_SRC}")

    ensure_main_checkout(repo_dir)

    dst = repo_dir / ".github" / "workflows"
    dst.mkdir(parents=True, exist_ok=True)

    required = ["listener-readme-update.yml", "listener-website-update.yml"]

    changed = False
    for wf in required:
        src = WORKFLOWS_SRC / wf
        if not src.exists():
            raise RuntimeError(f"missing required workflow in template: {src}")
        # copia/atualiza (preserva perms básicos)
        shutil.copy2(src, dst / wf)
        changed = True

    if not out(["git", "status", "--porcelain"], cwd=repo_dir):
        return False

    run(["git", "add", ".github/workflows"], cwd=repo_dir)
    run(["git", "commit", "-m", "Add required workflows"], cwd=repo_dir)
    run(["git", "push", "-u", "origin", "main"], cwd=repo_dir)
    return True

def sync_bootstrap_into_main(repo_dir: Path) -> bool:
    """
    Repo recém-criado:
      - copia bootstrap/repo_discipline -> raiz
      - copia bootstrap/repo_workflows -> .github/workflows
    """
    if not DISCIPLINE_SRC.exists():
        raise RuntimeError(f"missing discipline source: {DISCIPLINE_SRC}")
    if not WORKFLOWS_SRC.exists():
        raise RuntimeError(f"missing workflows source: {WORKFLOWS_SRC}")

    ensure_main_checkout(repo_dir)

    # 1) disciplina -> raiz (autoritativo)
    run([
        "rsync", "-a", "--delete",
        "--exclude", ".git",
        "--exclude", ".DS_Store",
        "--exclude", "_work",
        "--exclude", ".github/workflows",  # workflows só vêm do WORKFLOWS_SRC
        f"{DISCIPLINE_SRC}/",
        f"{repo_dir}/"
    ])

    # 2) workflows -> .github/workflows (autoritativo)
    (repo_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    run([
        "rsync", "-a", "--delete",
        "--exclude", ".git",
        "--exclude", ".DS_Store",
        f"{WORKFLOWS_SRC}/",
        f"{repo_dir}/.github/workflows/"
    ])

    if not out(["git", "status", "--porcelain"], cwd=repo_dir):
        return False

    run(["git", "add", "."], cwd=repo_dir)
    run(["git", "commit", "-m", "Update files"], cwd=repo_dir)
    run(["git", "push", "-u", "origin", "main"], cwd=repo_dir)
    return True


# ============================================================
# CORE
# ============================================================

def process_repo(entry: Dict) -> None:
    org = (entry.get("org") or "").strip()

    raw_repo = (entry.get("name") or "").strip()
    repo = normalize_repo_name(raw_repo)

    desc = (entry.get("description") or entry.get("title") or "").strip()
    private = bool(entry.get("private", False))
    full = f"{org}/{repo}"

    if not org or not repo:
        return

    log(full, "start")

    exists = repo_exists(org, repo)

    # ----------------------------
    # Case A) repo não existe
    # ----------------------------
    if not exists:
        create_repo(org, repo, desc, private)
        log(full, "repo created")
        wait_repo_ready(org, repo)

        repo_dir = ensure_repo_clone(org, repo)
        pushed = sync_bootstrap_into_main(repo_dir)  # disciplina + workflows (agora 2)
        log(full, "bootstrap pushed" if pushed else "no diff after bootstrap (unexpected)")

        if ALWAYS_DISPATCH or (not pushed):
            dispatch(org, repo, DISPATCH_SITE)
            log(full, f"dispatched {DISPATCH_SITE}")
            dispatch(org, repo, DISPATCH_README)
            log(full, f"dispatched {DISPATCH_README}")

        log(full, "done")
        return

    # ----------------------------
    # Case B/C) repo existe
    #   B) sem main => criar main + instalar workflows
    #   C) com main => se faltar workflows, instalar
    # ----------------------------
    info = repo_info(org, repo)
    default_branch = (info.get("default_branch") or "main").strip()

    has_main = has_branch(org, repo, "main")

    # clona 1x só quando precisar mexer
    def clone_for_fix() -> Path:
        wait_repo_ready(org, repo)
        d = ensure_repo_clone(org, repo)
        return d

    if not has_main:
        log(full, f"missing branch main (default_branch={default_branch}) — creating main + installing workflows")
        repo_dir = clone_for_fix()

        # garante que temos o conteúdo do default branch localmente
        try:
            run(["git", "checkout", default_branch], cwd=repo_dir)
        except subprocess.CalledProcessError:
            # se der ruim, fica no HEAD do clone mesmo e segue
            pass

        # cria main a partir do estado atual e adiciona workflows
        ensure_main_checkout(repo_dir)
        pushed = install_required_workflows_only(repo_dir)
        log(full, "main created/updated with required workflows" if pushed else "no changes after workflow install")

        if ALWAYS_DISPATCH or pushed:
            dispatch(org, repo, DISPATCH_SITE)
            log(full, f"dispatched {DISPATCH_SITE}")
            dispatch(org, repo, DISPATCH_README)
            log(full, f"dispatched {DISPATCH_README}")

        log(full, "done")
        return

    # tem main: checa workflows
    missing = missing_required_workflows(org, repo, "main")
    if missing:
        log(full, f"main exists but missing workflows: {', '.join(missing)} — installing")
        repo_dir = clone_for_fix()
        pushed = install_required_workflows_only(repo_dir)
        log(full, "workflows installed/pushed" if pushed else "no changes after workflow install")

        if ALWAYS_DISPATCH or pushed:
            dispatch(org, repo, DISPATCH_SITE)
            log(full, f"dispatched {DISPATCH_SITE}")
            dispatch(org, repo, DISPATCH_README)
            log(full, f"dispatched {DISPATCH_README}")

        log(full, "done")
        return

    log(full, "exists with main + required workflows — skipped")

import time

def wait_repo_ready(org: str, repo: str, attempts: int = 12) -> None:
    """
    Aguarda o repo ficar disponível tanto na API quanto no endpoint de clone.
    Evita race condition: API cria, mas git backend ainda não "subiu".
    """
    # 1) aguarda API confirmar existência
    for i in range(1, attempts + 1):
        r = gh("GET", f"{API}/repos/{org}/{repo}")
        if r.status_code == 200:
            break
        time.sleep(min(2 * i, 10))
    else:
        raise RuntimeError(f"repo not reachable via API after retries: {org}/{repo}")

    # 2) aguarda endpoint de clone responder (info/refs)
    #    200 = ok público; 401/403 pode ocorrer em privado; 404 é o problema aqui
    url = f"https://github.com/{org}/{repo}.git/info/refs?service=git-upload-pack"
    for i in range(1, attempts + 1):
        rr = requests.get(url, timeout=15)
        if rr.status_code != 404:
            return
        time.sleep(min(2 * i, 10))

    raise RuntimeError(f"repo git endpoint still 404 after retries: {org}/{repo}")

import re

def normalize_repo_name(name: str) -> str:
    """
    Replica o slug que o GitHub gera para nomes com caracteres especiais.
    Ex:
      MIT(8.20)-Intro -> MIT-8-20--Intro
    """
    s = name.strip()
    s = s.replace("(", "-").replace(")", "-")
    return s

def main() -> None:
    if not REGISTRY_FILE.exists():
        print(f"[ERROR] registry not found: {REGISTRY_FILE}", file=sys.stderr)
        print("[HINT] expected new path: org/org_registry/{org-lower}-registry.yml", file=sys.stderr)
        print("[HINT] set ONE_ORG or ORG env, or pass REGISTRY_FILE explicitly.", file=sys.stderr)
        sys.exit(2)

    data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        print("[ERROR] registry.repos must be a list", file=sys.stderr)
        sys.exit(2)

    # No schema novo, o org vem no topo do arquivo (data["org"]) e os itens em repos não têm "org".
    top_org = (data.get("org") or "").strip()

    if top_org:
        for e in repos:
            if isinstance(e, dict) and not (e.get("org") or "").strip():
                e["org"] = top_org


    # ------------------------------------------------------------
    # Optional filter: run only ONE_REPO (env)
    # Accepts:
    #   - "repo"
    #   - "org/repo"
    #   - exact "name" from registry
    # ------------------------------------------------------------
    one_repo = (os.environ.get("ONE_REPO") or "").strip()

    def norm(s: str) -> str:
        return re.sub(r"\s+", "", (s or "").strip().lower())

    if one_repo:
        one_repo_norm = norm(one_repo)
        filtered = []

        for entry in repos:
            org = (entry.get("org") or "").strip()
            raw_name = (entry.get("name") or "").strip()
            repo = normalize_repo_name(raw_name)

            full_norm = norm(f"{org}/{repo}")
            name_norm = norm(raw_name)
            repo_norm = norm(repo)

            if "/" in one_repo_norm:
                if full_norm == one_repo_norm:
                    filtered.append(entry)
            else:
                if one_repo_norm in (repo_norm, name_norm):
                    filtered.append(entry)

        if not filtered:
            print(f"[WARN] ONE_REPO set but no match found: {one_repo}", flush=True)
        else:
            repos = filtered



    for entry in repos:
        full = f"{(entry.get('org') or '').strip()}/{(entry.get('name') or '').strip()}"
        try:
            process_repo(entry)
        except Exception as e:
            err(full, "process", str(e))


if __name__ == "__main__":
    main()