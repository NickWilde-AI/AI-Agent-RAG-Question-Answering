import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from src.auth import AuthenticationError, actor_from_bearer
from src.infra.research_repository import SQLiteResearchRepository
from src.research_graph import ResearchAgentWorkflow
from src.mcp_server import MinimalMCPServer


def _token(payload,secret="secret"):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x,separators=(",",":")).encode()).decode().rstrip("=")
    head=enc({"alg":"HS256","typ":"JWT"}); body=enc(payload); raw=f"{head}.{body}"
    sig=base64.urlsafe_b64encode(hmac.new(secret.encode(),raw.encode(),hashlib.sha256).digest()).decode().rstrip("=")
    return f"{raw}.{sig}"


def test_jwt_actor_and_expiry():
    actor=actor_from_bearer("Bearer "+_token({"sub":"alice","groups":["rd"],"roles":["admin"],"exp":time.time()+60}),"secret")
    assert actor.subject=="alice" and "group:rd" in actor.acl_subjects and actor.is_admin
    with pytest.raises(AuthenticationError,match="expired"):
        actor_from_bearer("Bearer "+_token({"sub":"alice","exp":1}),"secret")


def test_workspace_acl_user_group_and_visibility(tmp_path):
    repo=SQLiteResearchRepository(str(tmp_path/"acl.db"))
    ws=repo.create_workspace("secret","",False,"alice")
    assert repo.workspace_role(ws["workspace_id"],["user:alice"])=="owner"
    assert repo.list_workspaces(["user:bob"])==[]
    repo.grant_workspace_access(ws["workspace_id"],"group:rd","viewer")
    assert repo.workspace_role(ws["workspace_id"],["user:bob","group:rd"])=="viewer"
    assert len(repo.list_workspaces(["group:rd"]))==1
    assert repo.list_workspace_acl(ws["workspace_id"])[0]["subject"] in {"group:rd","user:alice"}


def test_research_graph_has_three_real_roles():
    calls=[]
    def planner(objective,documents): calls.append(("plan",objective)); return [{"query":objective}]
    def executor(steps): calls.append(("execute",len(steps))); return [{"verified":True,"evidence":["p1"]}]
    state=ResearchAgentWorkflow(planner,executor).run("对比资料",[])
    assert calls==[("plan","对比资料"),("execute",1)]
    assert state["role_trace"]==["planner_agent","executor_agent","verifier_agent"]
    assert len(state["verified_findings"])==1


def test_mcp_protocol_lists_and_calls_tools():
    class FakeTools:
        def search(self,query,topk=5,workspace_id=""): return {"query":query,"topk":topk}
    server=MinimalMCPServer(FakeTools())
    listed=server.handle({"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
    assert {x["name"] for x in listed["result"]["tools"]} >= {"search_knowledge","ask_knowledge","plan_research"}
    called=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge","arguments":{"query":"x","topk":2}}})
    assert '"topk": 2' in called["result"]["content"][0]["text"]


def test_api_enforces_workspace_acl(tmp_path):
    import src.api as api
    old_repo,old_executor_repo=api.research_repository,api.research_executor.repo
    old_enabled,old_secret=api.SETTINGS.enable_auth,api.SETTINGS.jwt_secret
    repo=SQLiteResearchRepository(str(tmp_path/"api-acl.db"))
    try:
        api.research_repository=repo; api.research_executor.repo=repo
        object.__setattr__(api.SETTINGS,"enable_auth",True); object.__setattr__(api.SETTINGS,"jwt_secret","secret")
        client=TestClient(api.app)
        alice={"Authorization":"Bearer "+_token({"sub":"alice@example.com","exp":time.time()+60})}
        bob={"Authorization":"Bearer "+_token({"sub":"bob@example.com","groups":["rd"],"exp":time.time()+60})}
        created=client.post("/workspaces",json={"name":"A","use_demo":True},headers=alice)
        assert created.status_code==201; wid=created.json()["workspace_id"]
        assert client.get(f"/workspaces/{wid}",headers=bob).status_code==403
        grant=client.put(f"/workspaces/{wid}/acl",json={"subject":"group:rd","role":"viewer"},headers=alice)
        assert grant.status_code==200
        assert client.get(f"/workspaces/{wid}",headers=bob).status_code==200
        assert client.post(f"/workspaces/{wid}/documents",files={"file":("a.txt",b"x","text/plain")},headers=bob).status_code==403
    finally:
        api.research_repository=old_repo; api.research_executor.repo=old_executor_repo
        object.__setattr__(api.SETTINGS,"enable_auth",old_enabled); object.__setattr__(api.SETTINGS,"jwt_secret",old_secret)
