"""标准 MCP 适配层：把现有检索、QA 工具与研究计划暴露给 Claude/Codex 等 MCP Client。"""

from __future__ import annotations

import os
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, Optional

from .bootstrap import build_engine
from .config import SETTINGS
from .infra.research_repository import SQLiteResearchRepository
from .research import ResearchExecutor, ResearchPlanner, ToolRegistry


class MCPKnowledgeTools:
    def __init__(self, repository: Optional[SQLiteResearchRepository] = None) -> None:
        self.repository=repository or SQLiteResearchRepository(os.getenv("RAG_RESEARCH_DB","data/research/research.db"))
        self.executor=ResearchExecutor(self.repository)
        path="data/user_pages.json" if Path("data/user_pages.json").exists() else "data/demo_pages.json"
        self.default_engine=build_engine(path)

    def _engine(self, workspace_id: str = "") -> Any:
        if not workspace_id: return self.default_engine
        if SETTINGS.enable_auth:
            subject=os.getenv("RAG_MCP_SUBJECT","").strip()
            role=self.repository.workspace_role(workspace_id,[f"user:{subject}"] if subject else [])
            if role is None: raise PermissionError("RAG_MCP_SUBJECT has no workspace access")
        return self.executor.build_workspace_engine(workspace_id)

    def search(self, query: str, topk: int = 5, workspace_id: str = "") -> Dict[str, Any]:
        engine=self._engine(workspace_id); rewritten,hits=engine.retriever.retrieve(query,max(1,min(topk,12)))
        pages=[]
        for hit in hits:
            page=engine.retriever.get_page(hit.page_id)
            pages.append({"page_id":hit.page_id,"score":hit.score,"file_name":Path(page.source_file).name if page.source_file else page.doc_id,"page_no":page.page_no,"excerpt":page.content[:500]})
        return {"rewritten_query":rewritten,"hits":pages}

    def ask(self, query: str, topk: int = 3, workspace_id: str = "") -> Dict[str, Any]:
        result=self._engine(workspace_id).ask(query,max(1,min(topk,12)),"mcp")
        return {"answer":result.answer,"branch":result.branch,"verified":result.verified,"citations":result.citation_details}

    def run_tool(self, tool_name: str, query: str, topk: int = 5, workspace_id: str = "") -> Dict[str, Any]:
        run=ToolRegistry(self._engine(workspace_id)).execute(tool_name,{"query":query,"topk":max(1,min(topk,12))})
        result=run["result"]
        return {"answer":result.answer,"branch":result.branch,"verified":result.verified,"elapsed_ms":run["elapsed_ms"],"citations":result.citation_details}

    def plan_research(self, objective: str, workspace_id: str = "") -> Dict[str, Any]:
        engine=self._engine(workspace_id); documents=self.repository.list_documents(workspace_id) if workspace_id else []
        planner=ResearchPlanner(getattr(engine.router,"llm_client",None))
        return {"objective":objective,"steps":[{"title":x.title,"description":x.description,"tool_name":x.tool_name,"query":x.query} for x in planner.plan(objective,documents)]}


class LazyMCPKnowledgeTools:
    """让 MCP initialize/tools/list 秒回；只在首次真实工具调用时构建检索索引。"""
    def __init__(self) -> None: self._service: Optional[MCPKnowledgeTools]=None
    def __getattr__(self,name: str) -> Any:
        if self._service is None:
            with redirect_stdout(sys.stderr): self._service=MCPKnowledgeTools()
        return getattr(self._service,name)


def _mcp_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    """MCP stdout 只能输出协议消息，业务进度一律转 stderr。"""
    with redirect_stdout(sys.stderr): return function(*args,**kwargs)


class MinimalMCPServer:
    """Python 3.9 兼容的 MCP stdio fallback；覆盖 initialize / tools/list / tools/call。"""
    SPECS={
        "search_knowledge":("在企业知识库检索页级证据",{"query":{"type":"string"},"topk":{"type":"integer","default":5},"workspace_id":{"type":"string","default":""}},["query"]),
        "ask_knowledge":("执行完整 RAG 问答",{"query":{"type":"string"},"topk":{"type":"integer","default":3},"workspace_id":{"type":"string","default":""}},["query"]),
        "run_qa_tool":("显式调用三类 QA 工具",{"tool_name":{"type":"string","enum":list(ToolRegistry.NAMES)},"query":{"type":"string"},"topk":{"type":"integer","default":5},"workspace_id":{"type":"string","default":""}},["tool_name","query"]),
        "plan_research":("把复杂目标拆成研究计划",{"objective":{"type":"string"},"workspace_id":{"type":"string","default":""}},["objective"]),
    }
    def __init__(self,service: MCPKnowledgeTools) -> None: self.service=service
    def _tools(self): return [{"name":name,"description":spec[0],"inputSchema":{"type":"object","properties":spec[1],"required":spec[2]}} for name,spec in self.SPECS.items()]
    def _call(self,name,args):
        methods={"search_knowledge":"search","ask_knowledge":"ask","run_qa_tool":"run_tool","plan_research":"plan_research"}
        if name not in methods: raise ValueError(f"unknown tool: {name}")
        return _mcp_call(getattr(self.service,methods[name]),**args)
    def handle(self,message: Dict[str,Any]) -> Optional[Dict[str,Any]]:
        if "id" not in message: return None
        rid=message["id"]; method=message.get("method")
        try:
            if method=="initialize": result={"protocolVersion":message.get("params",{}).get("protocolVersion","2025-03-26"),"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"enterprise-multimodal-rag","version":"1.0.0"}}
            elif method=="ping": result={}
            elif method=="tools/list": result={"tools":self._tools()}
            elif method=="tools/call":
                params=message.get("params",{}); payload=self._call(params.get("name",""),params.get("arguments",{}))
                result={"content":[{"type":"text","text":json.dumps(payload,ensure_ascii=False)}],"isError":False}
            else: return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":"Method not found"}}
            return {"jsonrpc":"2.0","id":rid,"result":result}
        except Exception as exc: return {"jsonrpc":"2.0","id":rid,"error":{"code":-32602,"message":str(exc)[:500]}}
    def run(self,transport: str = "stdio") -> None:
        if transport!="stdio": raise ValueError("Python 3.9 fallback supports stdio only; use Python >=3.10 for other transports")
        for line in sys.stdin:
            try: message=json.loads(line); response=self.handle(message)
            except Exception as exc: response={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":str(exc)[:500]}}
            if response is not None: print(json.dumps(response,ensure_ascii=False,separators=(",",":")),flush=True)


def create_mcp_server(tools: Optional[MCPKnowledgeTools] = None) -> Any:
    service=tools or LazyMCPKnowledgeTools()
    try: from mcp.server.fastmcp import FastMCP
    except ImportError: return MinimalMCPServer(service)
    server=FastMCP("enterprise-multimodal-rag")

    @server.tool()
    def search_knowledge(query: str, topk: int = 5, workspace_id: str = "") -> Dict[str, Any]:
        """在企业知识库检索页级证据。"""
        return _mcp_call(service.search,query,topk,workspace_id)

    @server.tool()
    def ask_knowledge(query: str, topk: int = 3, workspace_id: str = "") -> Dict[str, Any]:
        """执行完整 RAG 问答，返回校验状态和页级引用。"""
        return _mcp_call(service.ask,query,topk,workspace_id)

    @server.tool()
    def run_qa_tool(tool_name: str, query: str, topk: int = 5, workspace_id: str = "") -> Dict[str, Any]:
        """显式调用 fact_qa、multi_page_qa 或 chart_qa。"""
        return _mcp_call(service.run_tool,tool_name,query,topk,workspace_id)

    @server.tool()
    def plan_research(objective: str, workspace_id: str = "") -> Dict[str, Any]:
        """把复杂研究目标拆成可执行工具计划。"""
        return _mcp_call(service.plan_research,objective,workspace_id)
    return server


def main() -> None:
    create_mcp_server().run(transport=os.getenv("RAG_MCP_TRANSPORT","stdio"))


if __name__ == "__main__": main()
